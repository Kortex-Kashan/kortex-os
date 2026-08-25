// Sidecar configuration injection is intentionally deferred: no backend
// binary/path exists yet (packaging tool choice and the M3 backend entry
// point are both still open). This module wires the supervision *state*
// and the lifecycle hooks that will use it once a real `SidecarConfig` is
// supplied by a later slice — see `sidecar.rs` module docs.
//
// Kept crate-private (`mod`, not `pub mod`): nothing outside this crate
// needs it — `sidecar.rs`'s tests are unit tests in the same compilation
// unit, not a separate integration-test crate, so they don't require
// public visibility either.
mod sidecar;

use std::sync::Mutex;

use sidecar::SidecarSupervision;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            app.manage(Mutex::new(SidecarSupervision::Disabled));
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
                if let Some(state) = app_handle.try_state::<Mutex<SidecarSupervision>>() {
                    if let Ok(mut supervision) = state.lock() {
                        supervision.shutdown();
                    }
                }
            }
        });
}
