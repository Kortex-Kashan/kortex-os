// Sidecar process supervision (`sidecar.rs`) and the M7.1 code that
// actually spawns and supervises the real backend process with it
// (`backend_process.rs`, `secure_keys.rs`) — see each module's own docs.
//
// Kept crate-private (`mod`, not `pub mod`): nothing outside this crate
// needs it — every module's tests are unit tests in the same compilation
// unit, not a separate integration-test crate, so they don't require
// public visibility either.
mod backend_process;
mod secure_keys;
mod sidecar;

// Browser-B1/B2: the `BrowserRuntime` abstraction and its `WebView2RuntimeAdapter`
// implementation — see the module's own docs for the exact scope boundary
// (no capability/governance layer, no persistent profiles; create/navigate/
// reload/back/forward/resize/query/destroy through embedded child webviews).
mod browser_runtime;
// Browser-B3: `BrowserProfileStore` — persistent, tenant-scoped profile
// identity/storage/locking/lifecycle. Sits alongside `browser_runtime`,
// never inside it — see that module's own doc for the boundary.
mod browser_profile_store;
// Browser-B4: `BrowserPolicyEngine` — local, synchronous, deterministic
// navigation policy (scheme + local/private-network destination checks).
// See that module's own doc for why this cannot be a backend round-trip.
mod browser_policy;
// M3 IPC bridge (`invoke_capability`) and event relay
// (`connect_event_stream`) — see each module's own docs for the exact
// transport contract. `ipc.rs` talks to the backend at a configured
// loopback URL; it does not itself spawn that backend — `backend_process.rs`
// is what does, as of M7.1.
mod events;
mod ipc;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use browser_profile_store::{ActiveProfileSurfaces, BrowserProfileStore, BrowserProfileStoreState};
use browser_runtime::{BrowserRuntime, BrowserRuntimeState, WebView2RuntimeAdapter};
use events::EventRelayState;
use ipc::{IpcClientState, KeyringTokenStore};
use sidecar::SidecarSupervision;
use tauri::Manager;
use tauri_plugin_deep_link::DeepLinkExt;

/// Set (by the window-close/exit handlers below) *before* they call
/// `SidecarSupervision::shutdown` — read by `backend_process`'s monitor
/// loop to distinguish an intentional shutdown from an unexpected crash,
/// since both make the sidecar's `is_running()` return `false`. Managed as
/// Tauri app state, mirroring `EventRelayState`'s own `Arc`-wrapped
/// convention, so both the `.setup()`-spawned monitor task and the
/// window-event closures below can reach the identical shared flag.
type ShutdownIntentFlag = Arc<AtomicBool>;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    std::panic::set_hook(Box::new(|info| {
        let msg = match info.payload().downcast_ref::<&'static str>() {
            Some(s) => *s,
            None => match info.payload().downcast_ref::<String>() {
                Some(s) => &**s,
                None => "Box<dyn Any>",
            },
        };
        let location = info
            .location()
            .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
            .unwrap_or_else(|| "unknown".to_string());
        eprintln!("KORTEX PANIC: '{msg}' at {location}");

        let crash_log_path = std::env::temp_dir().join("kortex-desktop-crash.log");
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&crash_log_path)
        {
            use std::io::Write;
            let _ = writeln!(
                file,
                "[{:?}] KORTEX PANIC: '{}' at {}",
                std::time::SystemTime::now(),
                msg,
                location
            );
        }
    }));

    let mut builder = tauri::Builder::default();

    // Phase A: must be the first plugin registered (per
    // `tauri-plugin-single-instance`'s own documented requirement) —
    // on Windows, the OS delivers an OAuth `kortex-auth://` callback
    // as argv to a brand-new process launch rather than an event on
    // the already-running one; this plugin detects that and forwards
    // argv to the existing instance's own deep-link handler below
    // instead, so a pending OAuth flow's in-memory state is never
    // orphaned in an abandoned second window. The callback here is a
    // no-op: forwarding alone is enough to trigger `on_open_url`
    // below in the *original* process. `single-instance` does not
    // support mobile, hence the `desktop` gate (this app only ships
    // desktop targets, but the gate matches the plugin's own contract).
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|_app, _argv, _cwd| {}));
    }

    builder
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            ipc::invoke_capability,
            ipc::has_session,
            ipc::logout,
            ipc::refresh_session,
            ipc::get_system_health,
            events::connect_event_stream,
            browser_runtime::browser_create_surface,
            browser_runtime::browser_navigate,
            browser_runtime::browser_reload,
            browser_runtime::browser_go_back,
            browser_runtime::browser_go_forward,
            browser_runtime::browser_set_bounds,
            browser_runtime::browser_query_state,
            browser_runtime::browser_destroy,
            browser_profile_store::browser_list_profiles,
            browser_profile_store::browser_create_profile,
            browser_profile_store::browser_rename_profile,
            browser_profile_store::browser_delete_profile,
        ])
        .setup(|app| {
            app.manage(Mutex::new(SidecarSupervision::Disabled));
            app.manage(ShutdownIntentFlag::new(AtomicBool::new(false)));
            app.manage(Arc::new(IpcClientState::new(Arc::new(KeyringTokenStore))));
            app.manage(Arc::new(EventRelayState::default()));

            // `app_data_dir()` falling back to the current directory rather
            // than failing app startup mirrors this file's own existing
            // degrade-not-fail posture (see `backend_process::
            // spawn_and_monitor`'s doc). Computed once, up front, since both
            // the profile store (Browser-B3) and the policy audit log
            // (Browser-B4) live under it.
            let profiles_root = app
                .path()
                .app_data_dir()
                .map(|dir| browser_profile_store::browser_profiles_root(&dir))
                .unwrap_or_else(|_| browser_profile_store::browser_profiles_root(std::path::Path::new(".")));

            // Browser-B1: one `WebView2RuntimeAdapter` for the whole app,
            // embedding every browser surface as a child of the "main"
            // window (Browser-B1 preflight §5 — Option A). Profile-agnostic
            // as of Browser-B3 — see that module's own doc comment.
            // Browser-B4: also given the policy audit log path (the SAME
            // file `BrowserProfileStore` writes its own audit entries to,
            // below) — see `WebView2RuntimeAdapter::policy_audit_log_path`'s
            // own doc comment for why this is a plain path, not a
            // dependency on `BrowserProfileStore` itself.
            let main_window = app
                .get_window("main")
                .expect("the \"main\" window is declared in tauri.conf.json and always exists at setup time");
            let policy_audit_log_path = profiles_root.join("audit.log");
            let browser_runtime: Arc<dyn BrowserRuntime> =
                Arc::new(WebView2RuntimeAdapter::new(main_window, policy_audit_log_path));
            app.manage(BrowserRuntimeState(browser_runtime));

            // Browser-B3: `BrowserProfileStore` owns tenant-scoped profile
            // identity/storage/locking. Never fails app startup (same
            // degrade-not-fail posture as above) — a missing/unresolvable
            // app-data directory means browser profiles fail later, at
            // profile creation, not that KORTEX itself fails to start.
            // `BrowserProfileStore::new` also performs the one-time,
            // idempotent legacy-default-profile quarantine check (Browser-B1/
            // B2's non-tenant-scoped `browser-profiles/default`, if it
            // exists) — see that function's own doc comment.
            match BrowserProfileStore::new(profiles_root) {
                Ok(store) => {
                    app.manage(BrowserProfileStoreState(Arc::new(store)));
                }
                Err(e) => {
                    // Never fails app startup (same posture as above) — a
                    // browser profile store that couldn't even initialize
                    // its root directory means every profile operation
                    // fails closed later, at first use, with a clear error;
                    // it does not mean KORTEX itself fails to start.
                    eprintln!("KORTEX: browser profile store failed to initialize: {e}");
                }
            }
            app.manage(ActiveProfileSurfaces::default());

            // Phase A: register the `kortex-auth://` scheme with the OS at
            // runtime (Windows/Linux only — macOS resolves schemes solely
            // from the bundled Info.plist, and `register_all` on macOS is a
            // documented no-op). Registering unconditionally in `.setup()`
            // — not gated behind "first run" — is itself idempotent and
            // matches this plugin's own documented usage; it re-registers
            // to the current executable path on every launch, which
            // matters after an in-place update (`kortex.update.apply`)
            // moves the binary. The frontend consumes incoming URLs via
            // `@tauri-apps/plugin-deep-link`'s own `onOpenUrl` listener —
            // no custom Rust-side relay is needed, the plugin emits its
            // event directly.
            #[cfg(any(windows, target_os = "linux"))]
            {
                app.deep_link().register_all()?;
            }

            // M7.1: resolve the real backend command and spawn it —
            // replaces the permanently-`Disabled` supervision state this
            // app shipped with through M1.2–M6. Never fails app startup:
            // see `backend_process::spawn_and_monitor`'s own docs for the
            // non-fatal degradation if spawning isn't possible.
            backend_process::spawn_and_monitor(app.handle().clone());

            Ok(())
        })
        .on_window_event(|window, event| {
            // Architecture-mandated shutdown sequence (phase3_desktop_
            // architecture.md §5): CloseRequested -> graceful sidecar
            // shutdown -> allow application close. This handler runs
            // synchronously before Tauri proceeds with closing the
            // window, so the (bounded-time) sidecar shutdown has already
            // happened by the time the window/app appears to the user to
            // have closed — we deliberately do NOT call
            // `api.prevent_close()`, since the shutdown work is already
            // done by the time this handler returns.
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let app_handle = window.app_handle();
                // Set BEFORE shutdown() — see `ShutdownIntentFlag`'s doc.
                if let Some(flag) = app_handle.try_state::<ShutdownIntentFlag>() {
                    flag.store(true, Ordering::SeqCst);
                }
                // Browser-B1: destroy every live browser surface before the
                // main window (their parent) closes — the explicit "no
                // orphaned browser runtime" requirement, not left to
                // whatever implicit teardown a closing parent window might
                // or might not cascade to its child webviews.
                if let Some(state) = app_handle.try_state::<BrowserRuntimeState>() {
                    state.0.destroy_all();
                }
                // Browser-B3: release every profile lock the just-destroyed
                // surfaces held — extends the identical "no orphaned
                // browser runtime" guarantee to profile locks, so a clean
                // shutdown never leaves a profile reporting `Locked` on the
                // next launch (only an actual crash should ever need the
                // stale-lock PID-liveness recovery path).
                release_all_profile_locks(app_handle);
                if let Some(state) = app_handle.try_state::<Mutex<SidecarSupervision>>() {
                    if let Ok(mut supervision) = state.lock() {
                        supervision.shutdown();
                    }
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building KORTEX Desktop")
        .run(|app_handle, event| {
            // Fallback safety net only. `CloseRequested` above is the
            // primary, architecture-mandated trigger and already performs
            // the graceful shutdown before the window closes. This
            // `ExitRequested` handler exists for exit paths that don't
            // originate from a window close event (e.g. a future
            // programmatic `AppHandle::exit()` call) — it must not be the
            // *only* place shutdown happens, since by the time
            // `ExitRequested` fires the window may already be gone.
            // `SidecarSupervision::shutdown` is idempotent, so running it
            // here even after `CloseRequested` already ran is harmless.
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(flag) = app_handle.try_state::<ShutdownIntentFlag>() {
                    flag.store(true, Ordering::SeqCst);
                }
                // Browser-B1: `destroy_all` is idempotent (an already-empty
                // runtime is a no-op — see its own test), so running it here
                // even after `CloseRequested` already ran is harmless, same
                // reasoning as `SidecarSupervision::shutdown` below.
                if let Some(state) = app_handle.try_state::<BrowserRuntimeState>() {
                    state.0.destroy_all();
                }
                release_all_profile_locks(app_handle);
                if let Some(state) = app_handle.try_state::<Mutex<SidecarSupervision>>() {
                    if let Ok(mut supervision) = state.lock() {
                        supervision.shutdown();
                    }
                }
            }
        });
}

/// Releases every profile lock any surface this process ever created still
/// holds — see the two call sites' own comments. `try_state` (not plain
/// `State`) because this runs from window-event/run-event closures, not a
/// command, and both states are optional here for the same reason
/// `BrowserRuntimeState`'s own lookup above already is (e.g. `.setup()`
/// could in principle not have reached the point where these were managed
/// if an earlier step returned an error first).
fn release_all_profile_locks(app_handle: &tauri::AppHandle) {
    let (Some(bindings), Some(profile_store)) = (
        app_handle.try_state::<ActiveProfileSurfaces>(),
        app_handle.try_state::<BrowserProfileStoreState>(),
    ) else {
        return;
    };
    for (tenant_id, profile_id) in bindings.take_all() {
        let _ = profile_store.0.close_profile(&tenant_id, &profile_id);
    }
}
