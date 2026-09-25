//! Browser-B1/B2: the `BrowserRuntime` abstraction and its V1 implementation,
//! `WebView2RuntimeAdapter`.
//!
//! This is the KORTEX-side equivalent of the architecture-doc concept
//! `IKortexBrowserRuntime` (see `docs/architecture/browser_architecture.md`
//! §2, `docs/architecture/browser_b1_preflight_report.md`). No Rust code in
//! this crate outside this module may reference `tauri::webview::WebviewBuilder`,
//! `tauri::Webview`, `webview2_com`/raw WebView2 COM interfaces, or any other
//! WebView2/wry-specific type — everything that crosses this module's
//! boundary is one of the plain types declared below (`BrowserSurfaceId`,
//! `CreateSurfaceRequest`, `BrowserSurfaceState`, `SurfaceBounds`,
//! `BrowserRuntimeError`), so a future adapter for a different runtime
//! (`CefRuntimeAdapter`, `ChromiumRuntimeAdapter`) could implement the same
//! `BrowserRuntime` trait without any caller needing to change.
//!
//! **Scope boundary (Browser-B1+B2)**: this module proves that KORTEX can
//! create, navigate, reload, go back/forward, reposition/resize, query, and
//! destroy a WebView2 child webview through this abstraction — nothing more.
//! It deliberately does not implement: persistent profiles/sessions
//! (Browser-B3), any capability/governance layer (`kortex.browser.*`,
//! Browser-B5), or an AI Browser Agent (Browser-B6). There is deliberately
//! no bridge from page-loaded JavaScript to any command in this module or
//! elsewhere in this crate.
//!
//! **Platform boundary**: back/forward navigation and real event-driven
//! loading state require raw `ICoreWebView2` COM calls (`webview2_com`),
//! which only exist on Windows. Every use of them is `#[cfg(windows)]`-gated
//! with a `#[cfg(not(windows))]` fallback (an explicit "not supported"
//! error for `go_back`/`go_forward`, and `loading`/`can_go_back`/
//! `can_go_forward` staying at their safe defaults) so this crate keeps
//! compiling on the non-Windows targets this repository's own CI (`rust`
//! job, `ubuntu-latest`) already builds against — see
//! `docs/architecture/browser_b2_implementation_report.md`.
//!
//! **Profile-directory boundary**: `CreateSurfaceRequest::profile_id` is an
//! opaque identifier, never a filesystem path — the frontend
//! (`apps/desktop/src/features/browser/api.ts`) must never be able to steer
//! WebView2's on-disk user-data folder to an arbitrary location.
//! [`WebView2RuntimeAdapter::resolve_profile_directory`] is the sole place a
//! `profile_id` is turned into a `PathBuf`, and it sanitizes the id first.
//! This is a minimal stand-in for the full `BrowserProfileStore` design
//! (`docs/architecture/browser_security_model.md` §10) — Browser-B3 owns
//! per-tenant allocation, lifecycle, and cross-tenant isolation guarantees;
//! this module only proves the underlying `WebviewBuilder::data_directory()`
//! hook works. Browser-B2's tabs all share one process-lifetime, non-
//! persisted profile — real per-tenant/per-profile persistence remains
//! Browser-B3's job.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{LogicalPosition, LogicalSize, Runtime, Webview, WebviewUrl, Window};

#[cfg(windows)]
use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2;
#[cfg(windows)]
use webview2_com::{NavigationCompletedEventHandler, NavigationStartingEventHandler};

/// Opaque handle to a live browser surface. Crosses the Tauri IPC boundary
/// as a plain string — the webview itself is never exposed to the frontend.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct BrowserSurfaceId(String);

/// Process-lifetime-monotonic sequence appended to `BrowserSurfaceId::generate()`'s
/// timestamp, so two ids generated in the same process can never collide
/// regardless of system clock granularity — closing the collision path a
/// Browser-B1 adversarial review flagged in `create_surface`'s registration
/// step (a colliding id would have silently dropped the newly-created,
/// already-embedded webview without ever closing it). `Relaxed` ordering is
/// sufficient: only mutual distinctness across calls is required, not any
/// ordering relative to other memory operations.
static SURFACE_ID_SEQUENCE: AtomicU64 = AtomicU64::new(0);

impl BrowserSurfaceId {
    /// Not a real UUID (mirrors `ipc.rs::uuid_like_id`'s own reasoning — no
    /// extra crate pulled in for one call site); only needs to be unique
    /// enough to key an in-process `HashMap`, never parsed as a UUID.
    fn generate() -> Self {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let sequence = SURFACE_ID_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        Self(format!("browser-surface-{nanos:x}-{sequence:x}"))
    }

    fn as_label(&self) -> &str {
        &self.0
    }
}

/// Request to create a new browser surface. `profile_id` is opaque — see
/// this module's own doc comment for why it is never a path.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateSurfaceRequest {
    pub profile_id: String,
    pub initial_url: String,
}

/// A surface's on-screen placement in the KORTEX main window's own logical
/// (DPI-independent) coordinate space — matching `LogicalPosition`/
/// `LogicalSize`, which the frontend's CSS pixels already map to directly at
/// 100% scale. Browser-B2's tab-switching also uses this: an inactive tab's
/// surface is parked at an off-screen `SurfaceBounds` rather than destroyed,
/// so switching tabs never discards a live page.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SurfaceBounds {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// Navigation state. `loading` is event-driven (see
/// `register_navigation_loading_handlers`), not guessed from whether a
/// navigate/reload/back/forward command was merely *dispatched* — posting
/// such a command only proves the request reached WebView2, not that the
/// resulting page load has finished. `can_go_back`/`can_go_forward` are
/// read fresh from WebView2 on every call (Windows only — see the module's
/// platform-boundary doc; both are `false` on any other platform, since no
/// non-Windows adapter exists yet).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BrowserSurfaceState {
    pub surface_id: BrowserSurfaceId,
    pub url: String,
    pub loading: bool,
    pub can_go_back: bool,
    pub can_go_forward: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum BrowserRuntimeError {
    SurfaceNotFound { surface_id: BrowserSurfaceId },
    AlreadyExists { surface_id: BrowserSurfaceId },
    Platform { message: String },
}

impl std::fmt::Display for BrowserRuntimeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::SurfaceNotFound { surface_id } => {
                write!(f, "no browser surface with id {:?}", surface_id.0)
            }
            Self::AlreadyExists { surface_id } => {
                write!(f, "a browser surface with id {:?} already exists", surface_id.0)
            }
            Self::Platform { message } => write!(f, "browser runtime error: {message}"),
        }
    }
}

impl std::error::Error for BrowserRuntimeError {}

/// The KORTEX-side runtime abstraction (`IKortexBrowserRuntime` in the
/// architecture docs). Every method operates on the opaque
/// [`BrowserSurfaceId`] only — no WebView2/wry type is part of this
/// contract, so callers (Tauri commands, and eventually Browser Policy)
/// depend only on this trait, never on [`WebView2RuntimeAdapter`] directly.
///
/// `create_surface` combines what the task brief lists as separate `create`
/// and `attach` responsibilities into one call: Tauri's own primitive
/// (`WebviewBuilder` + `Window::add_child`) does not separate "build a
/// webview configuration" from "embed it in a window" — `add_child` does
/// both atomically — so splitting them here would fight the underlying API
/// rather than reflect it (the task brief explicitly permits deviating from
/// its listed method names when the real architecture suggests a better
/// interface).
///
/// Browser-B2 adds `go_back`/`go_forward` (required by the browser
/// toolbar) and `set_bounds` (required for both window-resize tracking and
/// tab-switching — an inactive tab is parked at an off-screen `SurfaceBounds`
/// via this same primitive, not a second concept).
pub trait BrowserRuntime: Send + Sync {
    fn create_surface(&self, request: CreateSurfaceRequest) -> Result<BrowserSurfaceId, BrowserRuntimeError>;
    fn navigate(&self, surface_id: &BrowserSurfaceId, url: &str) -> Result<(), BrowserRuntimeError>;
    fn reload(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError>;
    fn go_back(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError>;
    fn go_forward(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError>;
    fn set_bounds(&self, surface_id: &BrowserSurfaceId, bounds: SurfaceBounds) -> Result<(), BrowserRuntimeError>;
    fn query_state(&self, surface_id: &BrowserSurfaceId) -> Result<BrowserSurfaceState, BrowserRuntimeError>;
    fn destroy(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError>;

    /// Destroys every live surface this runtime owns. Called from this
    /// crate's existing app-shutdown sequence (`lib.rs`'s `CloseRequested`/
    /// `ExitRequested` handlers, alongside `SidecarSupervision::shutdown()`)
    /// so no WebView2 surface can outlive the Browser application —
    /// Browser-B1's explicit "no orphaned browser runtime" requirement,
    /// which now also covers every open Browser-B2 tab, not just one
    /// surface.
    fn destroy_all(&self);
}

/// Keeps only ASCII alphanumerics, `-`, and `_` from `profile_id`, and caps
/// its length — defense in depth on top of the fact that `profile_id` is
/// already documented as opaque and is never accepted as a path. An empty
/// or fully-invalid input degrades to a fixed fallback name rather than
/// producing an empty path segment.
fn sanitize_profile_id(profile_id: &str) -> String {
    let cleaned: String = profile_id
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .take(128)
        .collect();
    if cleaned.is_empty() {
        "default".to_string()
    } else {
        cleaned
    }
}

/// Free function (not a method) specifically so this — the actual
/// path-construction logic the security model depends on — is unit-testable
/// without constructing a live `WebView2RuntimeAdapter`/`Window`. See this
/// module's own doc comment on `profile_id` never being a path.
fn resolve_profile_directory(profile_root: &Path, profile_id: &str) -> PathBuf {
    profile_root.join(sanitize_profile_id(profile_id))
}

/// One live browser surface's Rust-side state: the webview handle itself,
/// plus the event-driven `loading` flag `register_navigation_loading_handlers`
/// keeps current. Grouping them (rather than a second parallel map) is the
/// minimal change needed to add per-surface state beyond the webview handle
/// itself.
struct SurfaceEntry<R: Runtime> {
    webview: Webview<R>,
    loading: Arc<AtomicBool>,
}

/// V1 concrete implementation of [`BrowserRuntime`], against the Microsoft
/// Edge WebView2 runtime (via Tauri's `wry`/`webview2-com` integration).
/// Every WebView2/wry-specific type used to implement this struct is
/// private to this module — see the module doc comment.
pub struct WebView2RuntimeAdapter<R: Runtime> {
    window: Window<R>,
    profile_root: PathBuf,
    surfaces: Mutex<HashMap<BrowserSurfaceId, SurfaceEntry<R>>>,
}

impl<R: Runtime> WebView2RuntimeAdapter<R> {
    /// `window` is the KORTEX main window every browser surface is embedded
    /// into as a child webview (Browser-B1 preflight §5 — Option A,
    /// confirmed supported by the pinned Tauri version). `profile_root` is
    /// the base directory under which per-profile WebView2 user-data
    /// folders are allocated; Browser-B3 owns the real allocation policy —
    /// this adapter only proves the mechanism.
    pub fn new(window: Window<R>, profile_root: PathBuf) -> Self {
        Self {
            window,
            profile_root,
            surfaces: Mutex::new(HashMap::new()),
        }
    }

    fn resolve_profile_directory(&self, profile_id: &str) -> PathBuf {
        resolve_profile_directory(&self.profile_root, profile_id)
    }

    fn with_surface<T>(
        &self,
        surface_id: &BrowserSurfaceId,
        f: impl FnOnce(&Webview<R>) -> Result<T, BrowserRuntimeError>,
    ) -> Result<T, BrowserRuntimeError> {
        let surfaces = self.surfaces.lock().unwrap();
        let entry = surfaces
            .get(surface_id)
            .ok_or_else(|| BrowserRuntimeError::SurfaceNotFound { surface_id: surface_id.clone() })?;
        f(&entry.webview)
    }

    /// Clones the webview handle out of the registry and releases the lock
    /// before returning — needed by `go_back`/`go_forward`/`query_state`,
    /// which (on Windows) must hand a `'static` closure to `with_webview`,
    /// impossible while still borrowing from the `MutexGuard`. `Webview<R>`
    /// is a cheap handle/dispatcher clone (the same pattern `Window<R>` uses
    /// inside Tauri's own `add_child`), not a second live webview.
    fn cloned_surface(&self, surface_id: &BrowserSurfaceId) -> Result<(Webview<R>, Arc<AtomicBool>), BrowserRuntimeError> {
        let surfaces = self.surfaces.lock().unwrap();
        surfaces
            .get(surface_id)
            .map(|entry| (entry.webview.clone(), entry.loading.clone()))
            .ok_or_else(|| BrowserRuntimeError::SurfaceNotFound { surface_id: surface_id.clone() })
    }
}

/// Fixed placement for a newly-created surface, before the frontend's first
/// real `set_bounds` call (issued immediately after creation, once the
/// content area's actual on-screen rect is known via `ResizeObserver` —
/// Browser-B2's `useBrowserSurfaceBounds` hook). Never the final, real size.
const DEFAULT_SURFACE_WIDTH: f64 = 1024.0;
const DEFAULT_SURFACE_HEIGHT: f64 = 720.0;

/// Registers WebView2 `NavigationStarting`/`NavigationCompleted` handlers
/// that flip `loading` true/false — the one genuinely event-driven piece of
/// state this runtime tracks, rather than guessing from command completion
/// (posting a navigate/reload/back/forward command only proves the request
/// was dispatched, not that the resulting page load has finished).
///
/// Tokens are intentionally not retained for explicit `remove_*` calls: both
/// handlers' lifetime is tied to the underlying WebView2 instance, which is
/// torn down wholesale on `destroy`/`destroy_all` — a token-tracking
/// abstraction here would manage cleanup this runtime already gets for
/// free, and the task brief explicitly asks not to introduce unnecessary
/// abstractions.
///
/// Registration failure is swallowed deliberately: it can only fail if the
/// WebView2 controller/environment isn't ready immediately after
/// `add_child` returns, which Tauri's own synchronous wait inside
/// `add_child` is documented to guarantee against. If it ever did fail,
/// `loading` simply stays `false` forever for that surface — a degraded-
/// but-safe outcome, never a crash or a hang.
#[cfg(windows)]
fn register_navigation_loading_handlers<R: Runtime>(webview: &Webview<R>, loading: Arc<AtomicBool>) {
    let loading_for_start = loading.clone();
    let loading_for_complete = loading;
    let _ = webview.with_webview(move |platform_webview| {
        let core = unsafe { platform_webview.controller().CoreWebView2() };
        let Ok(core) = core else { return };

        let start_handler = NavigationStartingEventHandler::create(Box::new(move |_sender, _args| {
            loading_for_start.store(true, Ordering::Relaxed);
            Ok(())
        }));
        let mut start_token: i64 = 0;
        let _ = unsafe { core.add_NavigationStarting(&start_handler, &mut start_token) };

        let complete_handler = NavigationCompletedEventHandler::create(Box::new(move |_sender, _args| {
            loading_for_complete.store(false, Ordering::Relaxed);
            Ok(())
        }));
        let mut complete_token: i64 = 0;
        let _ = unsafe { core.add_NavigationCompleted(&complete_handler, &mut complete_token) };
    });
}

#[cfg(not(windows))]
fn register_navigation_loading_handlers<R: Runtime>(_webview: &Webview<R>, _loading: Arc<AtomicBool>) {
    // No non-Windows `BrowserRuntime` adapter exists yet (ADR-0019 §5) —
    // `loading` simply never flips from its initial `false` on this
    // platform, a degraded-but-safe default, not a compile-time gap.
}

/// Dispatches `f` onto WebView2's COM apartment thread via `with_webview`
/// and blocks (on the *calling* thread, never the main/event-loop thread —
/// every caller is one of Browser-B2's `async fn` Tauri commands, which
/// Tauri already runs off the main thread, per the same reasoning
/// `create_surface`'s `add_child` call already relies on) until it responds
/// on an internal channel. Needed for anything that must read a value back
/// from raw WebView2 COM state (`go_back`/`go_forward`'s own errors,
/// `can_go_back`/`can_go_forward`) — `with_webview`'s own signature has no
/// return-value path of its own.
#[cfg(windows)]
fn with_core_webview2<R, T, F>(webview: &Webview<R>, f: F) -> Result<T, BrowserRuntimeError>
where
    R: Runtime,
    T: Send + 'static,
    F: FnOnce(&ICoreWebView2) -> windows::core::Result<T> + Send + 'static,
{
    let (tx, rx) = std::sync::mpsc::channel::<Result<T, String>>();
    webview
        .with_webview(move |platform_webview| {
            let outcome = unsafe { platform_webview.controller().CoreWebView2() }
                .and_then(|core| f(&core))
                .map_err(|e| e.to_string());
            let _ = tx.send(outcome);
        })
        .map_err(|e| BrowserRuntimeError::Platform { message: e.to_string() })?;
    rx.recv()
        .map_err(|_| BrowserRuntimeError::Platform {
            message: "browser runtime internal error: with_webview callback did not respond".to_string(),
        })?
        .map_err(|message| BrowserRuntimeError::Platform { message })
}

/// Reads `CanGoBack`/`CanGoForward` fresh from WebView2. Windows only — see
/// the module's platform-boundary doc; both are `false` everywhere else.
#[cfg(windows)]
fn navigation_capability<R: Runtime>(webview: &Webview<R>) -> Result<(bool, bool), BrowserRuntimeError> {
    with_core_webview2(webview, |core| unsafe {
        let mut can_go_back = windows::core::BOOL(0);
        let mut can_go_forward = windows::core::BOOL(0);
        core.CanGoBack(&mut can_go_back)?;
        core.CanGoForward(&mut can_go_forward)?;
        Ok((can_go_back.as_bool(), can_go_forward.as_bool()))
    })
}

#[cfg(not(windows))]
fn navigation_capability<R: Runtime>(_webview: &Webview<R>) -> Result<(bool, bool), BrowserRuntimeError> {
    Ok((false, false))
}

impl<R: Runtime> BrowserRuntime for WebView2RuntimeAdapter<R> {
    fn create_surface(&self, request: CreateSurfaceRequest) -> Result<BrowserSurfaceId, BrowserRuntimeError> {
        let url: tauri::Url = request
            .initial_url
            .parse()
            .map_err(|e| BrowserRuntimeError::Platform { message: format!("invalid initial_url: {e}") })?;

        let surface_id = BrowserSurfaceId::generate();
        let data_directory = self.resolve_profile_directory(&request.profile_id);

        let builder = tauri::webview::WebviewBuilder::new(surface_id.as_label(), WebviewUrl::External(url))
            .data_directory(data_directory);

        let webview = self
            .window
            .add_child(
                builder,
                LogicalPosition::new(0.0, 0.0),
                LogicalSize::new(DEFAULT_SURFACE_WIDTH, DEFAULT_SURFACE_HEIGHT),
            )
            .map_err(|e| BrowserRuntimeError::Platform { message: e.to_string() })?;

        let loading = Arc::new(AtomicBool::new(false));
        register_navigation_loading_handlers(&webview, loading.clone());

        let mut surfaces = self.surfaces.lock().unwrap();
        // `BrowserSurfaceId::generate()`'s monotonic sequence (see
        // `SURFACE_ID_SEQUENCE`) makes a real collision unreachable in
        // practice, but this branch still closes the just-created,
        // already-embedded webview before returning — belt-and-suspenders,
        // not load-bearing: without it, a colliding id (were one ever
        // possible) would drop `webview` here without ever calling
        // `.close()` on it, leaking a live WebView2 surface `destroy`/
        // `destroy_all` could never reach, since it was never inserted.
        if surfaces.contains_key(&surface_id) {
            let _ = webview.close();
            return Err(BrowserRuntimeError::AlreadyExists { surface_id });
        }
        surfaces.insert(surface_id.clone(), SurfaceEntry { webview, loading });
        Ok(surface_id)
    }

    fn navigate(&self, surface_id: &BrowserSurfaceId, url: &str) -> Result<(), BrowserRuntimeError> {
        let parsed: tauri::Url = url
            .parse()
            .map_err(|e| BrowserRuntimeError::Platform { message: format!("invalid url: {e}") })?;
        self.with_surface(surface_id, |webview| {
            webview
                .navigate(parsed)
                .map_err(|e| BrowserRuntimeError::Platform { message: e.to_string() })
        })
    }

    fn reload(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
        self.with_surface(surface_id, |webview| {
            webview
                .reload()
                .map_err(|e| BrowserRuntimeError::Platform { message: e.to_string() })
        })
    }

    #[cfg(windows)]
    fn go_back(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
        let (webview, _loading) = self.cloned_surface(surface_id)?;
        with_core_webview2(&webview, |core| unsafe { core.GoBack() })
    }

    #[cfg(not(windows))]
    fn go_back(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
        // Confirms the surface exists (consistent error semantics with the
        // Windows path) even though no non-Windows adapter implements
        // session-history navigation yet — see the module's platform-
        // boundary doc.
        self.cloned_surface(surface_id)?;
        Err(BrowserRuntimeError::Platform {
            message: "back navigation is only implemented for the Windows WebView2RuntimeAdapter".to_string(),
        })
    }

    #[cfg(windows)]
    fn go_forward(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
        let (webview, _loading) = self.cloned_surface(surface_id)?;
        with_core_webview2(&webview, |core| unsafe { core.GoForward() })
    }

    #[cfg(not(windows))]
    fn go_forward(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
        self.cloned_surface(surface_id)?;
        Err(BrowserRuntimeError::Platform {
            message: "forward navigation is only implemented for the Windows WebView2RuntimeAdapter".to_string(),
        })
    }

    fn set_bounds(&self, surface_id: &BrowserSurfaceId, bounds: SurfaceBounds) -> Result<(), BrowserRuntimeError> {
        self.with_surface(surface_id, |webview| {
            webview
                .set_position(LogicalPosition::new(bounds.x, bounds.y))
                .map_err(|e| BrowserRuntimeError::Platform { message: e.to_string() })?;
            webview
                .set_size(LogicalSize::new(bounds.width, bounds.height))
                .map_err(|e| BrowserRuntimeError::Platform { message: e.to_string() })
        })
    }

    fn query_state(&self, surface_id: &BrowserSurfaceId) -> Result<BrowserSurfaceState, BrowserRuntimeError> {
        let (webview, loading_flag) = self.cloned_surface(surface_id)?;
        let loading = loading_flag.load(Ordering::Relaxed);
        let url = webview
            .url()
            .map_err(|e| BrowserRuntimeError::Platform { message: e.to_string() })?
            .to_string();
        let (can_go_back, can_go_forward) = navigation_capability(&webview)?;
        Ok(BrowserSurfaceState {
            surface_id: surface_id.clone(),
            url,
            loading,
            can_go_back,
            can_go_forward,
        })
    }

    fn destroy(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
        let mut surfaces = self.surfaces.lock().unwrap();
        let entry = surfaces
            .remove(surface_id)
            .ok_or_else(|| BrowserRuntimeError::SurfaceNotFound { surface_id: surface_id.clone() })?;
        // A close failure (e.g. the webview was already gone) must not
        // leave the removed entry un-removed above — this method's
        // postcondition ("this id is no longer valid") already holds by
        // the time `close()` is attempted, matching `KeyringTokenStore::
        // clear`'s "already gone is an acceptable outcome" precedent.
        let _ = entry.webview.close();
        Ok(())
    }

    fn destroy_all(&self) {
        let mut surfaces = self.surfaces.lock().unwrap();
        for (_, entry) in surfaces.drain() {
            let _ = entry.webview.close();
        }
    }
}

/// Resolves the base directory Browser-B1 allocates per-profile WebView2
/// user-data folders under. Not `BrowserProfileStore` itself (Browser-B3) —
/// just enough to prove `WebviewBuilder::data_directory()` works against a
/// real, KORTEX-owned directory rather than an ad hoc temp path.
pub fn default_profile_root(app_data_dir: &Path) -> PathBuf {
    app_data_dir.join("browser-profiles")
}

/// Tauri-managed app state wrapping the single, process-wide
/// [`BrowserRuntime`]. Boxed as `dyn BrowserRuntime` (never
/// `WebView2RuntimeAdapter` directly) so every command below depends only on
/// the abstraction, matching this module's own boundary rule.
pub struct BrowserRuntimeState(pub Arc<dyn BrowserRuntime>);

/// Browser-B1/B2 commands: plain, direct Tauri IPC (frontend ↔ Rust), the
/// same transport `ipc::has_session`/`.logout` already use — deliberately
/// **not** routed through `invoke_capability`/the backend, since there is no
/// capability/governance layer yet (that is Browser-B5's job; see
/// `docs/architecture/browser_b1_preflight_report.md` §7). `async fn` per
/// `WebviewBuilder::new`'s own documented deadlock warning for
/// `create_surface`, and kept uniform across every command for the same
/// reason `ipc.rs`'s command wrappers are.
#[tauri::command]
pub async fn browser_create_surface(
    state: tauri::State<'_, BrowserRuntimeState>,
    request: CreateSurfaceRequest,
) -> Result<BrowserSurfaceId, BrowserRuntimeError> {
    state.0.create_surface(request)
}

#[tauri::command]
pub async fn browser_navigate(
    state: tauri::State<'_, BrowserRuntimeState>,
    surface_id: BrowserSurfaceId,
    url: String,
) -> Result<(), BrowserRuntimeError> {
    state.0.navigate(&surface_id, &url)
}

#[tauri::command]
pub async fn browser_reload(
    state: tauri::State<'_, BrowserRuntimeState>,
    surface_id: BrowserSurfaceId,
) -> Result<(), BrowserRuntimeError> {
    state.0.reload(&surface_id)
}

#[tauri::command]
pub async fn browser_go_back(
    state: tauri::State<'_, BrowserRuntimeState>,
    surface_id: BrowserSurfaceId,
) -> Result<(), BrowserRuntimeError> {
    state.0.go_back(&surface_id)
}

#[tauri::command]
pub async fn browser_go_forward(
    state: tauri::State<'_, BrowserRuntimeState>,
    surface_id: BrowserSurfaceId,
) -> Result<(), BrowserRuntimeError> {
    state.0.go_forward(&surface_id)
}

#[tauri::command]
pub async fn browser_set_bounds(
    state: tauri::State<'_, BrowserRuntimeState>,
    surface_id: BrowserSurfaceId,
    bounds: SurfaceBounds,
) -> Result<(), BrowserRuntimeError> {
    state.0.set_bounds(&surface_id, bounds)
}

#[tauri::command]
pub async fn browser_query_state(
    state: tauri::State<'_, BrowserRuntimeState>,
    surface_id: BrowserSurfaceId,
) -> Result<BrowserSurfaceState, BrowserRuntimeError> {
    state.0.query_state(&surface_id)
}

#[tauri::command]
pub async fn browser_destroy(
    state: tauri::State<'_, BrowserRuntimeState>,
    surface_id: BrowserSurfaceId,
) -> Result<(), BrowserRuntimeError> {
    state.0.destroy(&surface_id)
}

/// Deliberately pure-logic only — no live `tauri::Window`/`Webview` is
/// constructed anywhere in this module's tests. `tauri`'s own `test`
/// feature (`tauri::test::{mock_builder, MockRuntime, ...}`), which would
/// let a test exercise the real `add_child`/`WebviewBuilder`/`Webview` code
/// path in-process, was evaluated and rejected in Browser-B1: enabling it
/// alongside the `unstable` feature this crate already needs reproducibly
/// crashes every test binary at process startup on this development machine
/// (`STATUS_ENTRYPOINT_NOT_FOUND` / `0xC0000139`, confirmed via bisection —
/// `unstable` alone does not cause this). The root cause was not further
/// isolated (Browser-B1 OD-B5). This means the full create → navigate →
/// reload → go back/forward → resize → query → destroy lifecycle against a
/// real or mocked `Window`/`Webview` — including every raw WebView2 COM call
/// Browser-B2 adds (`GoBack`/`GoForward`/`CanGoBack`/`CanGoForward`/
/// `NavigationStarting`/`NavigationCompleted`) — is **not** covered by an
/// automated test in this environment. See
/// `docs/architecture/browser_b2_implementation_report.md` TESTS section
/// for the honest accounting of what was and wasn't verified, and why the
/// new COM-calling code is this phase's highest-risk, least-verified
/// surface area.
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_profile_id_keeps_only_safe_characters() {
        assert_eq!(sanitize_profile_id("tenant-42_abc"), "tenant-42_abc");
        assert_eq!(sanitize_profile_id("../../etc/passwd"), "etcpasswd");
        assert_eq!(sanitize_profile_id(""), "default");
        assert_eq!(sanitize_profile_id("../"), "default");
    }

    #[test]
    fn resolve_profile_directory_never_escapes_the_profile_root() {
        let root = PathBuf::from("test-profiles");
        let resolved = resolve_profile_directory(&root, "../../../windows/system32");
        assert_eq!(resolved, root.join("windowssystem32"));
        assert!(resolved.starts_with(&root));
    }

    #[test]
    fn resolve_profile_directory_is_stable_for_the_same_profile_id() {
        let root = PathBuf::from("test-profiles");
        assert_eq!(
            resolve_profile_directory(&root, "tenant-1"),
            resolve_profile_directory(&root, "tenant-1"),
        );
    }

    #[test]
    fn resolve_profile_directory_keeps_distinct_tenants_in_distinct_directories() {
        let root = PathBuf::from("test-profiles");
        assert_ne!(
            resolve_profile_directory(&root, "tenant-1"),
            resolve_profile_directory(&root, "tenant-2"),
        );
    }

    #[test]
    fn default_profile_root_is_a_dedicated_subdirectory_of_the_app_data_dir() {
        let app_data_dir = PathBuf::from("C:/fake/app-data");
        let root = default_profile_root(&app_data_dir);
        assert_eq!(root, app_data_dir.join("browser-profiles"));
    }

    #[test]
    fn browser_surface_id_generate_never_collides_across_many_sequential_calls() {
        // Targets the root cause of the collision path a Browser-B1
        // adversarial review flagged in `create_surface` (a colliding id
        // would drop the newly-created webview without closing it) —
        // proves ids are unique without needing a live `Window`/`Webview`
        // (the crashing `tauri::test` harness this crate cannot use; see
        // this module's own doc comment on its test module).
        let ids: Vec<BrowserSurfaceId> = (0..10_000).map(|_| BrowserSurfaceId::generate()).collect();
        let unique: std::collections::HashSet<_> = ids.iter().collect();
        assert_eq!(unique.len(), ids.len(), "every generated id must be distinct");
    }

    #[test]
    fn browser_surface_id_generate_never_collides_across_concurrent_threads() {
        // `create_surface` can in principle be invoked concurrently (each
        // is a separate async Tauri command) — proves the monotonic
        // sequence counter truly serializes across threads, not just
        // within one.
        let handles: Vec<_> = (0..8)
            .map(|_| std::thread::spawn(|| (0..2_000).map(|_| BrowserSurfaceId::generate()).collect::<Vec<_>>()))
            .collect();
        let mut all_ids = Vec::new();
        for handle in handles {
            all_ids.extend(handle.join().expect("generator thread must not panic"));
        }
        let unique: std::collections::HashSet<_> = all_ids.iter().collect();
        assert_eq!(unique.len(), all_ids.len(), "every generated id must be distinct even across threads");
    }

    #[test]
    fn browser_surface_id_generate_produces_a_stable_serializable_value() {
        let id = BrowserSurfaceId::generate();
        assert!(id.as_label().starts_with("browser-surface-"));
        // Round-trips through the exact JSON shape the frontend receives
        // and sends back on every subsequent navigate/reload/query/destroy
        // call — proving the opaque-handle contract holds end to end at the
        // serialization boundary, independent of any live webview.
        let json = serde_json::to_string(&id).unwrap();
        let round_tripped: BrowserSurfaceId = serde_json::from_str(&json).unwrap();
        assert_eq!(id, round_tripped);
    }

    #[test]
    fn surface_not_found_error_serializes_with_the_offending_id() {
        let id = BrowserSurfaceId::generate();
        let error = BrowserRuntimeError::SurfaceNotFound { surface_id: id.clone() };
        assert!(error.to_string().contains(id.as_label()));
        let json = serde_json::to_value(&error).unwrap();
        assert_eq!(json["kind"], "surfaceNotFound");
    }

    #[test]
    fn create_surface_request_deserializes_from_the_frontends_camel_case_shape() {
        let request: CreateSurfaceRequest = serde_json::from_str(
            r#"{"profileId":"tenant-1","initialUrl":"https://example.invalid/start"}"#,
        )
        .unwrap();
        assert_eq!(request.profile_id, "tenant-1");
        assert_eq!(request.initial_url, "https://example.invalid/start");
    }

    #[test]
    fn surface_bounds_deserializes_from_the_frontends_camel_case_shape() {
        let bounds: SurfaceBounds =
            serde_json::from_str(r#"{"x":12.5,"y":48.0,"width":800.0,"height":600.0}"#).unwrap();
        assert_eq!((bounds.x, bounds.y, bounds.width, bounds.height), (12.5, 48.0, 800.0, 600.0));
    }

    #[test]
    fn browser_surface_state_serializes_every_field_in_camel_case() {
        let state = BrowserSurfaceState {
            surface_id: BrowserSurfaceId::generate(),
            url: "https://example.invalid/".to_string(),
            loading: true,
            can_go_back: true,
            can_go_forward: false,
        };
        let json = serde_json::to_value(&state).unwrap();
        assert_eq!(json["url"], "https://example.invalid/");
        assert_eq!(json["loading"], true);
        assert_eq!(json["canGoBack"], true);
        assert_eq!(json["canGoForward"], false);
        // `surfaceId` is present and, per `BrowserSurfaceId`'s newtype
        // Serialize impl, a bare string — not a nested `{"0": "..."}`.
        assert!(json["surfaceId"].is_string());
    }
}
