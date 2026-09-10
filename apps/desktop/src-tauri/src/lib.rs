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

// M3 IPC bridge (`invoke_capability`) and event relay
// (`connect_event_stream`) — see each module's own docs for the exact
// transport contract. `ipc.rs` talks to the backend at a configured
// loopback URL; it does not itself spawn that backend — `backend_process.rs`
// is what does, as of M7.1.
mod events;
mod ipc;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

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
            ipc::get_system_health,
            events::connect_event_stream,
        ])
        .setup(|app| {
            app.manage(Mutex::new(SidecarSupervision::Disabled));
            app.manage(ShutdownIntentFlag::new(AtomicBool::new(false)));
            app.manage(Arc::new(IpcClientState::new(Arc::new(KeyringTokenStore))));
            app.manage(Arc::new(EventRelayState::default()));

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
                if let Some(state) = app_handle.try_state::<Mutex<SidecarSupervision>>() {
                    if let Ok(mut supervision) = state.lock() {
                        supervision.shutdown();
                    }
                }
            }
        });
}
