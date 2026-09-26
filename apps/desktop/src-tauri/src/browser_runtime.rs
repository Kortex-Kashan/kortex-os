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
//! It deliberately does not implement: any capability/governance layer
//! (`kortex.browser.*`, Browser-B5), or an AI Browser Agent (Browser-B6).
//! There is deliberately no bridge from page-loaded JavaScript to any
//! command in this module or elsewhere in this crate.
//!
//! **Browser-B3 boundary**: this module remains profile-agnostic — it has
//! no concept of tenants, `BrowserProfileId`s, or profile lifecycle at all.
//! `CreateSurfaceRequest::data_directory` is an already-resolved, already-
//! validated path handed to it by the caller; resolving WHICH directory a
//! given tenant/profile maps to is entirely `browser_profile_store::
//! BrowserProfileStore`'s job (see that module's own doc comment). The
//! `browser_create_surface`/`browser_destroy` Tauri commands below are the
//! orchestration point between the two — they resolve a profile via
//! `BrowserProfileStore`, then call this module's `BrowserRuntime` with the
//! result — but the `BrowserRuntime` trait and `WebView2RuntimeAdapter`
//! themselves never reach into `BrowserProfileStore` or `IpcClientState`.
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
//! **Profile-directory boundary**: `CreateSurfaceRequest::data_directory` is
//! a `PathBuf` this module trusts verbatim — it never accepts a raw,
//! frontend-supplied path itself (the frontend still only ever supplies an
//! opaque `BrowserProfileId`; see `browser_profile_store.rs`'s own
//! containment-checked resolution, which runs BEFORE this module is ever
//! called). This module's only remaining responsibility is handing that
//! already-validated path to `WebviewBuilder::data_directory()`.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{Emitter, LogicalPosition, LogicalSize, Runtime, Webview, WebviewUrl, Window};

use crate::browser_policy::{
    self, DenyReason, NavigationRequest, PolicyAction, PolicyAuditEvent, PolicyDecision,
    PolicyDeniedEvent, POLICY_DENIED_EVENT_NAME,
};

#[cfg(windows)]
use webview2_com::Microsoft::Web::WebView2::Win32::{
    ICoreWebView2, ICoreWebView2NavigationStartingEventArgs, ICoreWebView2Settings4,
    ICoreWebView2_4, COREWEBVIEW2_PERMISSION_STATE_DENY,
};
#[cfg(windows)]
use webview2_com::{
    DownloadStartingEventHandler, NavigationCompletedEventHandler, NavigationStartingEventHandler,
    NewWindowRequestedEventHandler, PermissionRequestedEventHandler,
};
#[cfg(windows)]
use windows::core::Interface;

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

/// Request to create a new browser surface. `data_directory` is already
/// resolved and containment-checked by the caller (`browser_create_surface`,
/// via `browser_profile_store::BrowserProfileStore::open_profile`) — this
/// type is constructed only in Rust, never deserialized directly from a
/// frontend-supplied value, so it deliberately has no `Deserialize` impl.
#[derive(Debug, Clone)]
pub struct CreateSurfaceRequest {
    pub data_directory: PathBuf,
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

/// `#[serde(rename_all = "camelCase")]` on an enum only renames VARIANT
/// names (the `"kind"` tag, here) — it does NOT cascade into a struct-like
/// variant's own field names, so `surface_id` is renamed explicitly on each
/// variant below (see `browser_profile_store::BrowserProfileError`'s own
/// doc comment, which documents the identical fix for the identical bug).
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum BrowserRuntimeError {
    SurfaceNotFound {
        #[serde(rename = "surfaceId")]
        surface_id: BrowserSurfaceId,
    },
    AlreadyExists {
        #[serde(rename = "surfaceId")]
        surface_id: BrowserSurfaceId,
    },
    Platform {
        message: String,
    },
}

impl std::fmt::Display for BrowserRuntimeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::SurfaceNotFound { surface_id } => {
                write!(f, "no browser surface with id {:?}", surface_id.0)
            }
            Self::AlreadyExists { surface_id } => {
                write!(
                    f,
                    "a browser surface with id {:?} already exists",
                    surface_id.0
                )
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
    fn create_surface(
        &self,
        request: CreateSurfaceRequest,
    ) -> Result<BrowserSurfaceId, BrowserRuntimeError>;
    fn navigate(&self, surface_id: &BrowserSurfaceId, url: &str)
        -> Result<(), BrowserRuntimeError>;
    fn reload(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError>;
    fn go_back(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError>;
    fn go_forward(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError>;
    fn set_bounds(
        &self,
        surface_id: &BrowserSurfaceId,
        bounds: SurfaceBounds,
    ) -> Result<(), BrowserRuntimeError>;
    fn query_state(
        &self,
        surface_id: &BrowserSurfaceId,
    ) -> Result<BrowserSurfaceState, BrowserRuntimeError>;
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
    surfaces: Mutex<HashMap<BrowserSurfaceId, SurfaceEntry<R>>>,
    /// Browser-B4: where policy-denial audit events are appended — the
    /// SAME file `browser_profile_store.rs` already writes its own
    /// lifecycle audit entries to (`<profiles_root>/audit.log`), so
    /// operators have one place to look. A plain `PathBuf`, not a
    /// `BrowserProfileStore` reference: this keeps `WebView2RuntimeAdapter`
    /// profile-AGNOSTIC in the sense that matters (no tenant/profile
    /// concept, no dependency on that module's types) while still writing
    /// to the same physical location — no different in spirit from the
    /// crash-log path `lib.rs`'s own panic hook already writes to
    /// unconditionally.
    policy_audit_log_path: PathBuf,
}

impl<R: Runtime> WebView2RuntimeAdapter<R> {
    /// `window` is the KORTEX main window every browser surface is embedded
    /// into as a child webview (Browser-B1 preflight §5 — Option A,
    /// confirmed supported by the pinned Tauri version). No longer takes a
    /// profile root (Browser-B3): this adapter is profile-agnostic — see
    /// the module's own doc comment. `policy_audit_log_path` is Browser-B4's
    /// addition — see the field's own doc comment.
    pub fn new(window: Window<R>, policy_audit_log_path: PathBuf) -> Self {
        Self {
            window,
            surfaces: Mutex::new(HashMap::new()),
            policy_audit_log_path,
        }
    }

    fn with_surface<T>(
        &self,
        surface_id: &BrowserSurfaceId,
        f: impl FnOnce(&Webview<R>) -> Result<T, BrowserRuntimeError>,
    ) -> Result<T, BrowserRuntimeError> {
        let surfaces = self.surfaces.lock().unwrap();
        let entry =
            surfaces
                .get(surface_id)
                .ok_or_else(|| BrowserRuntimeError::SurfaceNotFound {
                    surface_id: surface_id.clone(),
                })?;
        f(&entry.webview)
    }

    /// Clones the webview handle out of the registry and releases the lock
    /// before returning — needed by `go_back`/`go_forward`/`query_state`,
    /// which (on Windows) must hand a `'static` closure to `with_webview`,
    /// impossible while still borrowing from the `MutexGuard`. `Webview<R>`
    /// is a cheap handle/dispatcher clone (the same pattern `Window<R>` uses
    /// inside Tauri's own `add_child`), not a second live webview.
    fn cloned_surface(
        &self,
        surface_id: &BrowserSurfaceId,
    ) -> Result<(Webview<R>, Arc<AtomicBool>), BrowserRuntimeError> {
        let surfaces = self.surfaces.lock().unwrap();
        surfaces
            .get(surface_id)
            .map(|entry| (entry.webview.clone(), entry.loading.clone()))
            .ok_or_else(|| BrowserRuntimeError::SurfaceNotFound {
                surface_id: surface_id.clone(),
            })
    }
}

/// Fixed placement for a newly-created surface, before the frontend's first
/// real `set_bounds` call (issued immediately after creation, once the
/// content area's actual on-screen rect is known via `ResizeObserver` —
/// Browser-B2's `useBrowserSurfaceBounds` hook). Never the final, real size.
const DEFAULT_SURFACE_WIDTH: f64 = 1024.0;
const DEFAULT_SURFACE_HEIGHT: f64 = 720.0;

/// Registers WebView2's `NavigationStarting`/`NavigationCompleted`/
/// `NewWindowRequested`/`DownloadStarting`/`PermissionRequested` handlers.
/// `NavigationCompleted` still only flips `loading` true/false, as before.
/// `NavigationStarting` does two things: (1) Browser-B4's navigation
/// policy check — reads `Uri`/`IsUserInitiated`/`IsRedirected`, calls
/// `browser_policy::evaluate_navigation`, and calls `SetCancel(true)` to
/// actually block a denied navigation (the ONLY mechanism available: this
/// event does not support `GetDeferral`, confirmed against the pinned
/// `webview2-com-sys-0.38.2` bindings, so the decision must be synchronous
/// — see `browser_policy.rs`'s own module doc for why this can never be a
/// backend round-trip); (2) the original `loading` flag, now set only when
/// the navigation is ALLOWED — a denied navigation never truly "loads"
/// anything, so it should not report `loading: true` even momentarily.
///
/// `NewWindowRequested`/`DownloadStarting`/`PermissionRequested` each deny
/// unconditionally, per the B4 approval decision ("DENY all... do not
/// implement popup-to-tab conversion yet", "BLOCK all downloads... do not
/// implement save-path confirmation", "DENY by default... do not implement
/// human confirmation UI yet"): no policy evaluation is needed since the
/// decision is a constant, unlike navigation's per-request evaluation. Each
/// still goes through the same audit+frontend-event plumbing as a denied
/// navigation (`record_and_emit_policy_denial`), reusing
/// `DenyReason::NotYetSupported` (see its own doc comment on why "not yet"
/// rather than "denied").
///
/// Tokens are intentionally not retained for explicit `remove_*` calls: all
/// five handlers' lifetime is tied to the underlying WebView2 instance,
/// which is torn down wholesale on `destroy`/`destroy_all` — a
/// token-tracking abstraction here would manage cleanup this runtime
/// already gets for free, and the task brief explicitly asks not to
/// introduce unnecessary abstractions.
///
/// Registration failure is swallowed deliberately: it can only fail if the
/// WebView2 controller/environment isn't ready immediately after
/// `add_child` returns, which Tauri's own synchronous wait inside
/// `add_child` is documented to guarantee against. If it ever did fail,
/// `loading` simply stays `false` forever for that surface, and — more
/// importantly for B4 — none of the five policy enforcement points above
/// would ever run for that surface at all: a popup, a download, or a
/// permission request would fall through to WebView2's own default
/// behavior instead of being blocked. This degrade-but-safe-for-`loading`
/// outcome is NOT safe for policy enforcement; see the known-limitations
/// doc for this disclosed gap (registration failure is only theoretically
/// reachable per the reasoning above, but "theoretically unreachable" is
/// not the same claim as "verified enforced").
#[cfg(windows)]
fn register_navigation_loading_handlers<R: Runtime>(
    webview: &Webview<R>,
    loading: Arc<AtomicBool>,
    surface_id: BrowserSurfaceId,
    policy_audit_log_path: PathBuf,
) {
    let loading_for_start = loading.clone();
    let loading_for_complete = loading;
    let emit_webview = webview.clone();
    let _ = webview.with_webview(move |platform_webview| {
        let core = unsafe { platform_webview.controller().CoreWebView2() };
        let Ok(core) = core else { return };

        let policy_surface_id = surface_id.clone();
        let policy_audit_log_path_for_start = policy_audit_log_path.clone();
        let emit_webview_for_nav = emit_webview.clone();
        let start_handler =
            NavigationStartingEventHandler::create(Box::new(move |_sender, args| {
                let Some(args) = args else { return Ok(()) };
                match evaluate_navigation_args(&args) {
                    Ok(PolicyDecision::Allow) => {
                        loading_for_start.store(true, Ordering::Relaxed);
                    }
                    Ok(PolicyDecision::Deny(reason)) => {
                        unsafe { args.SetCancel(true) }?;
                        deny_navigation(
                            &emit_webview_for_nav,
                            &policy_audit_log_path_for_start,
                            &policy_surface_id,
                            reason,
                        );
                    }
                    Err(_) => {
                        // `Uri`/`IsUserInitiated`/`IsRedirected` themselves
                        // failed (an unexpected COM failure, not a policy
                        // question) — fail closed: cancel rather than let an
                        // un-evaluatable navigation proceed unchecked.
                        unsafe { args.SetCancel(true) }?;
                        deny_navigation(
                            &emit_webview_for_nav,
                            &policy_audit_log_path_for_start,
                            &policy_surface_id,
                            DenyReason::Malformed,
                        );
                    }
                }
                Ok(())
            }));
        let mut start_token: i64 = 0;
        let _ = unsafe { core.add_NavigationStarting(&start_handler, &mut start_token) };

        let complete_handler =
            NavigationCompletedEventHandler::create(Box::new(move |_sender, _args| {
                loading_for_complete.store(false, Ordering::Relaxed);
                Ok(())
            }));
        let mut complete_token: i64 = 0;
        let _ = unsafe { core.add_NavigationCompleted(&complete_handler, &mut complete_token) };

        let policy_surface_id_popup = surface_id.clone();
        let policy_audit_log_path_for_popup = policy_audit_log_path.clone();
        let emit_webview_for_popup = emit_webview.clone();
        let popup_handler =
            NewWindowRequestedEventHandler::create(Box::new(move |_sender, args| {
                let Some(args) = args else { return Ok(()) };
                // Leaving `NewWindow` unset (never calling `SetNewWindow`) and
                // marking the event `Handled` is the documented way to refuse a
                // popup outright — verified live in B4.8, not merely assumed
                // from documentation.
                unsafe { args.SetHandled(true) }?;
                deny_popup_download_or_permission(
                    &emit_webview_for_popup,
                    &policy_audit_log_path_for_popup,
                    &policy_surface_id_popup,
                    PolicyAction::Popup,
                    PolicyAuditEvent::PopupBlocked,
                );
                Ok(())
            }));
        let mut popup_token: i64 = 0;
        let _ = unsafe { core.add_NewWindowRequested(&popup_handler, &mut popup_token) };

        // `DownloadStarting` is declared on the versioned `ICoreWebView2_4`
        // interface, not the base `ICoreWebView2` (confirmed against the
        // pinned `webview2-com-sys-0.38.2` bindings) — unlike
        // `NewWindowRequested`/`PermissionRequested`, which both live on the
        // base interface. Best-effort cast, matching
        // `disable_password_and_autofill`'s own precedent: on a WebView2
        // runtime old enough not to implement `ICoreWebView2_4`, downloads
        // would not be blocked by this handler at all — a disclosed gap
        // (see the known-limitations doc), not a silently-assumed-safe
        // simplification.
        if let Ok(core4) = core.cast::<ICoreWebView2_4>() {
            let policy_surface_id_download = surface_id.clone();
            let policy_audit_log_path_for_download = policy_audit_log_path.clone();
            let emit_webview_for_download = emit_webview.clone();
            let download_handler =
                DownloadStartingEventHandler::create(Box::new(move |_sender, args| {
                    let Some(args) = args else { return Ok(()) };
                    unsafe { args.SetCancel(true) }?;
                    deny_popup_download_or_permission(
                        &emit_webview_for_download,
                        &policy_audit_log_path_for_download,
                        &policy_surface_id_download,
                        PolicyAction::Download,
                        PolicyAuditEvent::DownloadBlocked,
                    );
                    Ok(())
                }));
            let mut download_token: i64 = 0;
            let _ = unsafe { core4.add_DownloadStarting(&download_handler, &mut download_token) };
        }

        let policy_surface_id_permission = surface_id.clone();
        let policy_audit_log_path_for_permission = policy_audit_log_path.clone();
        let emit_webview_for_permission = emit_webview.clone();
        let permission_handler =
            PermissionRequestedEventHandler::create(Box::new(move |_sender, args| {
                let Some(args) = args else { return Ok(()) };
                unsafe { args.SetState(COREWEBVIEW2_PERMISSION_STATE_DENY) }?;
                deny_popup_download_or_permission(
                    &emit_webview_for_permission,
                    &policy_audit_log_path_for_permission,
                    &policy_surface_id_permission,
                    PolicyAction::Permission,
                    PolicyAuditEvent::PermissionDenied,
                );
                Ok(())
            }));
        let mut permission_token: i64 = 0;
        let _ = unsafe { core.add_PermissionRequested(&permission_handler, &mut permission_token) };
    });
}

/// Reads `Uri`/`IsUserInitiated`/`IsRedirected` off a real
/// `NavigationStarting` event and evaluates Browser-B4's policy — a thin
/// adapter between raw COM out-parameters and `browser_policy`'s pure,
/// COM-free `evaluate_navigation`, kept separate specifically so the
/// policy logic itself never needs to be `unsafe` or WebView2-aware (see
/// `browser_policy.rs`'s own module doc on why it never references a
/// WebView2/wry type).
#[cfg(windows)]
fn evaluate_navigation_args(
    args: &ICoreWebView2NavigationStartingEventArgs,
) -> windows::core::Result<PolicyDecision> {
    let mut uri_pwstr = windows::core::PWSTR::null();
    unsafe { args.Uri(&mut uri_pwstr) }?;
    let uri = unsafe { uri_pwstr.to_string() }.unwrap_or_default();

    let mut is_user_initiated = windows::core::BOOL(0);
    unsafe { args.IsUserInitiated(&mut is_user_initiated) }?;
    let mut is_redirected = windows::core::BOOL(0);
    unsafe { args.IsRedirected(&mut is_redirected) }?;

    Ok(browser_policy::evaluate_navigation(&NavigationRequest {
        uri,
        is_user_initiated: is_user_initiated.as_bool(),
        is_redirected: is_redirected.as_bool(),
    }))
}

/// Records the audit entry and notifies the trusted frontend (`"main"`
/// only — never the browser surface itself, which has no reason to
/// receive this and, being untrusted content, must never be trusted with
/// it either) that a policy action was blocked. Deliberately minimal
/// payload — see `browser_policy::PolicyDeniedEvent`'s own doc comment on
/// why the full URI is never included. Shared by `deny_navigation` (which
/// has a genuine per-request `reason`) and `deny_popup_download_or_permission`
/// (whose `reason` is always the fixed `DenyReason::NotYetSupported`).
#[cfg(windows)]
fn record_and_emit_policy_denial<R: Runtime>(
    webview: &Webview<R>,
    policy_audit_log_path: &std::path::Path,
    surface_id: &BrowserSurfaceId,
    action: PolicyAction,
    audit_event: PolicyAuditEvent,
    reason: DenyReason,
) {
    browser_policy::record_policy_audit_event(
        policy_audit_log_path,
        audit_event,
        surface_id.as_label(),
        Some(&reason),
    );
    let _ = webview.emit_to(
        "main",
        POLICY_DENIED_EVENT_NAME,
        PolicyDeniedEvent {
            surface_id: surface_id.as_label().to_string(),
            action,
            reason,
        },
    );
}

#[cfg(windows)]
fn deny_navigation<R: Runtime>(
    webview: &Webview<R>,
    policy_audit_log_path: &std::path::Path,
    surface_id: &BrowserSurfaceId,
    reason: DenyReason,
) {
    record_and_emit_policy_denial(
        webview,
        policy_audit_log_path,
        surface_id,
        PolicyAction::Navigation,
        PolicyAuditEvent::NavigationBlocked,
        reason,
    );
}

/// Popups, downloads, and native permission requests are denied
/// unconditionally in Browser-B4 V1 — see `DenyReason::NotYetSupported`'s
/// own doc comment — so, unlike `deny_navigation`, there is only ever one
/// possible `reason` to report here; callers supply only which action kind
/// and which audit event this particular denial is for.
#[cfg(windows)]
fn deny_popup_download_or_permission<R: Runtime>(
    webview: &Webview<R>,
    policy_audit_log_path: &std::path::Path,
    surface_id: &BrowserSurfaceId,
    action: PolicyAction,
    audit_event: PolicyAuditEvent,
) {
    record_and_emit_policy_denial(
        webview,
        policy_audit_log_path,
        surface_id,
        action,
        audit_event,
        DenyReason::NotYetSupported,
    );
}

#[cfg(not(windows))]
fn register_navigation_loading_handlers<R: Runtime>(
    _webview: &Webview<R>,
    _loading: Arc<AtomicBool>,
    _surface_id: BrowserSurfaceId,
    _policy_audit_log_path: PathBuf,
) {
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
        .map_err(|e| BrowserRuntimeError::Platform {
            message: e.to_string(),
        })?;
    rx.recv()
        .map_err(|_| BrowserRuntimeError::Platform {
            message: "browser runtime internal error: with_webview callback did not respond"
                .to_string(),
        })?
        .map_err(|message| BrowserRuntimeError::Platform { message })
}

/// Reads `CanGoBack`/`CanGoForward` fresh from WebView2. Windows only — see
/// the module's platform-boundary doc; both are `false` everywhere else.
#[cfg(windows)]
fn navigation_capability<R: Runtime>(
    webview: &Webview<R>,
) -> Result<(bool, bool), BrowserRuntimeError> {
    with_core_webview2(webview, |core| unsafe {
        let mut can_go_back = windows::core::BOOL(0);
        let mut can_go_forward = windows::core::BOOL(0);
        core.CanGoBack(&mut can_go_back)?;
        core.CanGoForward(&mut can_go_forward)?;
        Ok((can_go_back.as_bool(), can_go_forward.as_bool()))
    })
}

#[cfg(not(windows))]
fn navigation_capability<R: Runtime>(
    _webview: &Webview<R>,
) -> Result<(bool, bool), BrowserRuntimeError> {
    Ok((false, false))
}

/// Browser-B3 (D22): disables WebView2's own built-in password-autosave
/// and general-autofill UI for a surface — verified against the pinned
/// `webview2-com-sys-0.38.2` bindings before implementation (not guessed):
/// `ICoreWebView2Settings4::SetIsPasswordAutosaveEnabled`/
/// `SetIsGeneralAutofillEnabled` are real, generated methods, reachable via
/// `ICoreWebView2::Settings()` cast to the versioned `Settings4` interface.
/// Consistent with "no provider password collection inside KORTEX forms"
/// extended to "no browser-native password capture either" — a password
/// saved into WebView2's own password manager inside a KORTEX-hosted
/// surface is a feature surface nobody has designed isolation/audit for.
///
/// Best-effort: a failure here (e.g. an older WebView2 runtime that
/// doesn't implement `Settings4`) degrades to WebView2's own default
/// behavior for that one surface rather than failing surface creation
/// outright — the surface remains fully usable, just without this
/// hardening applied. `#[cfg(windows)]`-gated with a `#[cfg(not(windows))]`
/// no-op, matching every other raw-COM call in this module.
#[cfg(windows)]
fn disable_password_and_autofill<R: Runtime>(webview: &Webview<R>) {
    let _ = with_core_webview2(webview, |core| unsafe {
        let settings = core.Settings()?;
        let settings4: ICoreWebView2Settings4 = settings.cast()?;
        settings4.SetIsPasswordAutosaveEnabled(false)?;
        settings4.SetIsGeneralAutofillEnabled(false)?;
        Ok(())
    });
}

#[cfg(not(windows))]
fn disable_password_and_autofill<R: Runtime>(_webview: &Webview<R>) {}

impl<R: Runtime> BrowserRuntime for WebView2RuntimeAdapter<R> {
    fn create_surface(
        &self,
        request: CreateSurfaceRequest,
    ) -> Result<BrowserSurfaceId, BrowserRuntimeError> {
        let url: tauri::Url =
            request
                .initial_url
                .parse()
                .map_err(|e| BrowserRuntimeError::Platform {
                    message: format!("invalid initial_url: {e}"),
                })?;

        let surface_id = BrowserSurfaceId::generate();

        let builder =
            tauri::webview::WebviewBuilder::new(surface_id.as_label(), WebviewUrl::External(url))
                .data_directory(request.data_directory);

        let webview = self
            .window
            .add_child(
                builder,
                LogicalPosition::new(0.0, 0.0),
                LogicalSize::new(DEFAULT_SURFACE_WIDTH, DEFAULT_SURFACE_HEIGHT),
            )
            .map_err(|e| BrowserRuntimeError::Platform {
                message: e.to_string(),
            })?;

        let loading = Arc::new(AtomicBool::new(false));
        register_navigation_loading_handlers(
            &webview,
            loading.clone(),
            surface_id.clone(),
            self.policy_audit_log_path.clone(),
        );
        disable_password_and_autofill(&webview);

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

    fn navigate(
        &self,
        surface_id: &BrowserSurfaceId,
        url: &str,
    ) -> Result<(), BrowserRuntimeError> {
        let parsed: tauri::Url = url.parse().map_err(|e| BrowserRuntimeError::Platform {
            message: format!("invalid url: {e}"),
        })?;
        self.with_surface(surface_id, |webview| {
            webview
                .navigate(parsed)
                .map_err(|e| BrowserRuntimeError::Platform {
                    message: e.to_string(),
                })
        })
    }

    fn reload(&self, surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
        self.with_surface(surface_id, |webview| {
            webview.reload().map_err(|e| BrowserRuntimeError::Platform {
                message: e.to_string(),
            })
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
            message: "back navigation is only implemented for the Windows WebView2RuntimeAdapter"
                .to_string(),
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
            message:
                "forward navigation is only implemented for the Windows WebView2RuntimeAdapter"
                    .to_string(),
        })
    }

    fn set_bounds(
        &self,
        surface_id: &BrowserSurfaceId,
        bounds: SurfaceBounds,
    ) -> Result<(), BrowserRuntimeError> {
        self.with_surface(surface_id, |webview| {
            webview
                .set_position(LogicalPosition::new(bounds.x, bounds.y))
                .map_err(|e| BrowserRuntimeError::Platform {
                    message: e.to_string(),
                })?;
            webview
                .set_size(LogicalSize::new(bounds.width, bounds.height))
                .map_err(|e| BrowserRuntimeError::Platform {
                    message: e.to_string(),
                })
        })
    }

    fn query_state(
        &self,
        surface_id: &BrowserSurfaceId,
    ) -> Result<BrowserSurfaceState, BrowserRuntimeError> {
        let (webview, loading_flag) = self.cloned_surface(surface_id)?;
        let loading = loading_flag.load(Ordering::Relaxed);
        let url = webview
            .url()
            .map_err(|e| BrowserRuntimeError::Platform {
                message: e.to_string(),
            })?
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
        let entry =
            surfaces
                .remove(surface_id)
                .ok_or_else(|| BrowserRuntimeError::SurfaceNotFound {
                    surface_id: surface_id.clone(),
                })?;
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

/// Tauri-managed app state wrapping the single, process-wide
/// [`BrowserRuntime`]. Boxed as `dyn BrowserRuntime` (never
/// `WebView2RuntimeAdapter` directly) so every command below depends only on
/// the abstraction, matching this module's own boundary rule.
pub struct BrowserRuntimeState(pub Arc<dyn BrowserRuntime>);

/// Every failure `browser_create_surface` can produce — either resolving
/// the profile (before any surface exists at all) or creating the surface
/// itself (after the profile was already opened/locked). `#[serde(untagged)]`
/// so the JSON on the wire is exactly whichever inner error's own `{"kind":
/// ...}` shape — the frontend's existing `isBrowserRuntimeError()`-style
/// guard is extended (Browser-B3) to also recognize `BrowserProfileError`'s
/// kind strings, rather than this type introducing a second wrapper shape.
#[derive(Debug, Clone, Serialize)]
#[serde(untagged)]
pub enum BrowserSurfaceCreationError {
    Profile(crate::browser_profile_store::BrowserProfileError),
    Runtime(BrowserRuntimeError),
}

impl From<crate::browser_profile_store::BrowserProfileError> for BrowserSurfaceCreationError {
    fn from(error: crate::browser_profile_store::BrowserProfileError) -> Self {
        Self::Profile(error)
    }
}

impl From<BrowserRuntimeError> for BrowserSurfaceCreationError {
    fn from(error: BrowserRuntimeError) -> Self {
        Self::Runtime(error)
    }
}

/// Browser-B1/B2 commands: plain, direct Tauri IPC (frontend ↔ Rust), the
/// same transport `ipc::has_session`/`.logout` already use — deliberately
/// **not** routed through `invoke_capability`/the backend, since there is no
/// capability/governance layer yet (that is Browser-B5's job; see
/// `docs/architecture/browser_b1_preflight_report.md` §7). `async fn` per
/// `WebviewBuilder::new`'s own documented deadlock warning for
/// `create_surface`, and kept uniform across every command for the same
/// reason `ipc.rs`'s command wrappers are.
///
/// **Browser-B3 orchestration**: this command is the one place a tenant's
/// authoritative identity (`IpcClientState::current_tenant_id()`, OD-B7)
/// and a profile's on-disk directory (`BrowserProfileStore::open_profile`)
/// are resolved BEFORE this module's own `BrowserRuntime::create_surface`
/// is ever called — neither `BrowserRuntime` nor `WebView2RuntimeAdapter`
/// gains any knowledge of tenants or profiles as a result (see the module's
/// own doc comment). Fails closed with `ProfileIdentityUnavailable` if no
/// tenant identity is available yet — never falls back to a default/shared
/// directory.
#[tauri::command]
pub async fn browser_create_surface(
    runtime_state: tauri::State<'_, BrowserRuntimeState>,
    profile_state: tauri::State<'_, crate::browser_profile_store::BrowserProfileStoreState>,
    bindings: tauri::State<'_, crate::browser_profile_store::ActiveProfileSurfaces>,
    ipc_state: tauri::State<'_, Arc<crate::ipc::IpcClientState>>,
    profile_id: crate::browser_profile_store::BrowserProfileId,
    initial_url: String,
) -> Result<BrowserSurfaceId, BrowserSurfaceCreationError> {
    let tenant_id = crate::browser_profile_store::resolve_tenant_or_deny(
        &ipc_state,
        &profile_state.0,
        "browser_create_surface",
    )?;

    let data_directory = profile_state.0.open_profile(&tenant_id, &profile_id)?;

    let creation_result = runtime_state.0.create_surface(CreateSurfaceRequest {
        data_directory,
        initial_url,
    });
    match creation_result {
        Ok(surface_id) => {
            bindings.record(surface_id.clone(), tenant_id, profile_id);
            Ok(surface_id)
        }
        Err(runtime_error) => {
            // The profile's lock was already acquired above, but no
            // surface ended up using it — release it rather than leave
            // the profile stuck reporting `Locked` for a surface that
            // was never actually created.
            let _ = profile_state.0.close_profile(&tenant_id, &profile_id);
            Err(runtime_error.into())
        }
    }
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

/// Browser-B3: also releases the surface's profile lock (if it was created
/// through `browser_create_surface` and therefore has a recorded binding),
/// after the surface itself is gone. A lock-release failure is swallowed
/// (`let _ =`) for the same reason `destroy`'s own webview-close failure
/// already is — "already released" and "release failed" both leave the
/// profile in the same observable state (unlocked at the next stale-lock
/// check, since this process holding a lock it just tried and failed to
/// remove is itself an internally-inconsistent state that can't actually
/// arise from `std::fs::remove_file`'s own failure modes on a file this
/// process just created).
#[tauri::command]
pub async fn browser_destroy(
    runtime_state: tauri::State<'_, BrowserRuntimeState>,
    profile_state: tauri::State<'_, crate::browser_profile_store::BrowserProfileStoreState>,
    bindings: tauri::State<'_, crate::browser_profile_store::ActiveProfileSurfaces>,
    surface_id: BrowserSurfaceId,
) -> Result<(), BrowserRuntimeError> {
    runtime_state.0.destroy(&surface_id)?;
    if let Some((tenant_id, profile_id)) = bindings.take(&surface_id) {
        let _ = profile_state.0.close_profile(&tenant_id, &profile_id);
    }
    Ok(())
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

    // Browser-B3: `sanitize_profile_id`/`resolve_profile_directory`/
    // `default_profile_root` were removed from this module entirely — this
    // module is now profile-agnostic (see the module's own doc comment).
    // Their hardened successors (`sanitize_path_component`,
    // `resolve_child_directory`, `browser_profiles_root`) live in, and are
    // tested by, `browser_profile_store.rs`.

    #[test]
    fn browser_surface_id_generate_never_collides_across_many_sequential_calls() {
        // Targets the root cause of the collision path a Browser-B1
        // adversarial review flagged in `create_surface` (a colliding id
        // would drop the newly-created webview without closing it) —
        // proves ids are unique without needing a live `Window`/`Webview`
        // (the crashing `tauri::test` harness this crate cannot use; see
        // this module's own doc comment on its test module).
        let ids: Vec<BrowserSurfaceId> =
            (0..10_000).map(|_| BrowserSurfaceId::generate()).collect();
        let unique: std::collections::HashSet<_> = ids.iter().collect();
        assert_eq!(
            unique.len(),
            ids.len(),
            "every generated id must be distinct"
        );
    }

    #[test]
    fn browser_surface_id_generate_never_collides_across_concurrent_threads() {
        // `create_surface` can in principle be invoked concurrently (each
        // is a separate async Tauri command) — proves the monotonic
        // sequence counter truly serializes across threads, not just
        // within one.
        let handles: Vec<_> = (0..8)
            .map(|_| {
                std::thread::spawn(|| {
                    (0..2_000)
                        .map(|_| BrowserSurfaceId::generate())
                        .collect::<Vec<_>>()
                })
            })
            .collect();
        let mut all_ids = Vec::new();
        for handle in handles {
            all_ids.extend(handle.join().expect("generator thread must not panic"));
        }
        let unique: std::collections::HashSet<_> = all_ids.iter().collect();
        assert_eq!(
            unique.len(),
            all_ids.len(),
            "every generated id must be distinct even across threads"
        );
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
        let error = BrowserRuntimeError::SurfaceNotFound {
            surface_id: id.clone(),
        };
        assert!(error.to_string().contains(id.as_label()));
        let json = serde_json::to_value(&error).unwrap();
        assert_eq!(json["kind"], "surfaceNotFound");
    }

    /// `#[serde(rename_all = "camelCase")]` on an enum only renames the
    /// `"kind"` tag itself — it does NOT cascade into a struct-like
    /// variant's own field names (the identical latent bug was found and
    /// fixed the same way in `browser_profile_store::BrowserProfileError`;
    /// see that type's own doc comment). The frontend's `BrowserRuntimeError`
    /// TypeScript type (`apps/desktop/src/features/browser/api.ts`) expects
    /// `surfaceId`, not `surface_id`.
    #[test]
    fn browser_runtime_error_variants_serialize_surface_id_as_camel_case() {
        let id = BrowserSurfaceId::generate();

        let error = BrowserRuntimeError::SurfaceNotFound {
            surface_id: id.clone(),
        };
        let json = serde_json::to_value(&error).unwrap();
        assert_eq!(json["surfaceId"], serde_json::to_value(&id).unwrap());
        assert!(
            json.get("surface_id").is_none(),
            "must not ALSO emit the raw snake_case key"
        );

        let error = BrowserRuntimeError::AlreadyExists {
            surface_id: id.clone(),
        };
        let json = serde_json::to_value(&error).unwrap();
        assert_eq!(json["surfaceId"], serde_json::to_value(&id).unwrap());
        assert!(
            json.get("surface_id").is_none(),
            "must not ALSO emit the raw snake_case key"
        );
    }

    // Browser-B3: `CreateSurfaceRequest` no longer derives `Deserialize` at
    // all (it now carries an already-resolved `data_directory: PathBuf`,
    // constructed only by `browser_create_surface`'s own orchestration
    // logic — see the module's own doc comment) — there is no longer a
    // frontend-facing JSON shape for this type to round-trip.

    #[test]
    fn surface_bounds_deserializes_from_the_frontends_camel_case_shape() {
        let bounds: SurfaceBounds =
            serde_json::from_str(r#"{"x":12.5,"y":48.0,"width":800.0,"height":600.0}"#).unwrap();
        assert_eq!(
            (bounds.x, bounds.y, bounds.width, bounds.height),
            (12.5, 48.0, 800.0, 600.0)
        );
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
