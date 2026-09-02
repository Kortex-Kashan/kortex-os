//! Milestone M7.1: resolves the real KORTEX backend command and connects
//! the existing, previously-unwired `sidecar::SidecarManager` to the
//! desktop app's actual startup/shutdown lifecycle.
//!
//! **What this module deliberately does not decide.** Backend packaging
//! (a frozen/bundled single-file binary — PyInstaller vs. Nuitka vs.
//! other) remains an explicitly open, unresolved decision (`sidecar.rs`'s
//! own module docs, `docs/adr/ADR-0002-phase3-desktop-architecture-
//! approval.md`) that belongs to installer/packaging work, not this
//! milestone. `KORTEX_BACKEND_COMMAND` is the escape hatch for that future
//! binary: when set, this module spawns exactly that command and nothing
//! about its own resolution logic needs to change once a packaging tool is
//! chosen — only which branch fires.
//!
//! **What this module does resolve**: the dev-parity invocation every
//! backend developer already runs by hand today — `python -m uvicorn
//! kortex.api.main:app`, from a Python interpreter, with `backend/src` on
//! `PYTHONPATH` (mirroring `backend/pyproject.toml`'s own pytest
//! `pythonpath` convention) — now spawned automatically by Tauri instead of
//! requiring a human to start it first. This closes the actual M7.1 gap:
//! nothing previously started the backend process at all.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{AppHandle, Manager};

use crate::secure_keys::{self, KeyStore};
use crate::sidecar::{SidecarConfig, SidecarManager, SidecarOutcome, SidecarSupervision};

const BACKEND_COMMAND_ENV: &str = "KORTEX_BACKEND_COMMAND";
const PYTHON_EXECUTABLE_ENV: &str = "KORTEX_PYTHON_EXECUTABLE";
const MONITOR_INTERVAL: Duration = Duration::from_secs(1);

fn backend_source_dir() -> Result<PathBuf, String> {
    // `CARGO_MANIFEST_DIR` is this crate's own directory
    // (`apps/desktop/src-tauri`) as it was on the machine that *built* this
    // binary — correct for `cargo run`/`tauri dev`/a same-machine `cargo
    // build`, which is exactly the dev/source-checkout scenario this
    // resolution path targets. A genuinely distributed build without the
    // monorepo source tree present fails `canonicalize()` here and falls
    // through to the caller's non-fatal degradation (see `spawn_and_monitor`).
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("backend");
    dir.canonicalize()
        .map_err(|e| format!("backend source directory not found at {}: {e}", dir.display()))
}

fn resolve_python_executable(backend_dir: &Path) -> String {
    if let Ok(explicit) = std::env::var(PYTHON_EXECUTABLE_ENV) {
        if !explicit.trim().is_empty() {
            return explicit;
        }
    }
    let venv_python = if cfg!(windows) {
        backend_dir.join(".venv").join("Scripts").join("python.exe")
    } else {
        backend_dir.join(".venv").join("bin").join("python")
    };
    if venv_python.is_file() {
        return venv_python.to_string_lossy().to_string();
    }
    // No project-local venv found — fall back to whatever `python` resolves
    // to on PATH (matches `phase3_desktop_architecture.md` §6.7's own
    // documented dev expectation: "run directly from a local Python venv").
    "python".to_string()
}

/// Resolves the command Tauri should spawn to bring the backend up, plus
/// the environment it needs: the two persistent master/signing keys (see
/// `secure_keys`) always, and — for the dev-parity path only —
/// `PYTHONPATH`. `key_store` is injected so this stays unit-testable
/// against an in-memory double, mirroring `secure_keys`'s own pattern.
pub fn resolve_backend_sidecar_config(key_store: &dyn KeyStore) -> Result<SidecarConfig, String> {
    let mut env_vars = secure_keys::load_or_generate_backend_keys(key_store)?;

    if let Ok(raw_command) = std::env::var(BACKEND_COMMAND_ENV) {
        // Deliberately naive whitespace splitting (no quoting support) —
        // this is a developer/future-packaging override, not a shell.
        let mut parts = raw_command.split_whitespace();
        let program = parts
            .next()
            .ok_or_else(|| format!("{BACKEND_COMMAND_ENV} is set but empty"))?
            .to_string();
        let args: Vec<String> = parts.map(|s| s.to_string()).collect();
        let mut config = SidecarConfig::new(program, args);
        config.env_vars = env_vars;
        return Ok(config);
    }

    let backend_dir = backend_source_dir()?;
    let python = resolve_python_executable(&backend_dir);
    let args = vec![
        "-m".to_string(),
        "uvicorn".to_string(),
        "kortex.api.main:app".to_string(),
        "--host".to_string(),
        "127.0.0.1".to_string(),
        "--port".to_string(),
        "8000".to_string(),
    ];
    env_vars.push((
        "PYTHONPATH".to_string(),
        backend_dir.join("src").to_string_lossy().to_string(),
    ));

    let mut config = SidecarConfig::new(python, args);
    config.working_directory = Some(backend_dir);
    config.env_vars = env_vars;
    Ok(config)
}

/// Resolves the backend command, spawns it, stores the resulting
/// `SidecarManager` as `Active` managed state, and starts a background
/// crash-monitor task applying the existing `RestartPolicy`.
///
/// Never panics and never blocks app startup on failure: if resolution or
/// the spawn itself fails (no Python found, `backend/` source tree absent
/// in a distributed build, OS process-creation failure, ...), supervision
/// is left `Disabled` and this is logged to stderr — the desktop app's
/// existing backend-unreachable UX (bounded readiness retry, then a
/// manual-retry error state — see `AuthProvider`/`backendReadiness.ts`)
/// takes over exactly as it would for any other reason the backend isn't
/// reachable. A user is never left looking at a silently-broken app with
/// no explanation.
pub fn spawn_and_monitor(app: AppHandle) {
    let config = match resolve_backend_sidecar_config(&secure_keys::KeyringKeyStore) {
        Ok(config) => config,
        Err(err) => {
            eprintln!(
                "KORTEX: could not resolve a backend command to spawn ({err}); \
                 expecting the backend to already be running at KORTEX_BACKEND_URL."
            );
            return;
        }
    };

    let mut manager = SidecarManager::new(config);
    if let Err(err) = manager.spawn() {
        eprintln!("KORTEX: failed to spawn the backend process: {err}");
        return;
    }
    eprintln!("KORTEX: backend sidecar spawned.");

    if let Some(state) = app.try_state::<Mutex<SidecarSupervision>>() {
        if let Ok(mut supervision) = state.lock() {
            *supervision = SidecarSupervision::Active(manager);
        }
    }

    tokio::spawn(monitor_loop(app));
}

fn shutdown_requested(app: &AppHandle) -> bool {
    app.try_state::<Arc<AtomicBool>>()
        .map(|flag| flag.load(Ordering::SeqCst))
        .unwrap_or(false)
}

/// Polls the supervised sidecar's liveness once per `MONITOR_INTERVAL` and
/// applies the existing `RestartPolicy` on an unexpected exit.
///
/// Stops permanently the moment the app's shutdown-intent flag is set
/// (`lib.rs`'s `CloseRequested`/`ExitRequested` handlers set it *before*
/// calling `SidecarSupervision::shutdown`) — never by reading
/// `is_running()` alone. A clean, intentional shutdown also makes
/// `is_running()` return `false`, and must never be mistaken for a crash
/// needing a restart; checking the flag first is what keeps those two
/// cases distinguishable.
async fn monitor_loop(app: AppHandle) {
    loop {
        tokio::time::sleep(MONITOR_INTERVAL).await;

        if shutdown_requested(&app) {
            return;
        }

        let outcome = {
            let Some(state) = app.try_state::<Mutex<SidecarSupervision>>() else {
                return;
            };
            let Ok(mut supervision) = state.lock() else {
                return;
            };
            let SidecarSupervision::Active(manager) = &mut *supervision else {
                // Not (or no longer) actively supervised — nothing to watch.
                return;
            };
            match manager.is_running() {
                Ok(true) => None,
                Ok(false) => Some(manager.handle_unexpected_exit()),
                // A transient OS-level liveness-check error is not itself
                // evidence of a crash — try again next tick rather than
                // triggering a restart on a spurious read failure.
                Err(_) => None,
            }
        };

        match outcome {
            None => continue,
            Some(SidecarOutcome::Failed) => {
                eprintln!("KORTEX: backend sidecar exited and exhausted its restart attempts.");
                return;
            }
            Some(SidecarOutcome::WillRestart { attempt, backoff }) => {
                eprintln!(
                    "KORTEX: backend sidecar exited unexpectedly; restart attempt {attempt} in {backoff:?}."
                );
                tokio::time::sleep(backoff).await;
                if shutdown_requested(&app) {
                    return;
                }
                if let Some(state) = app.try_state::<Mutex<SidecarSupervision>>() {
                    if let Ok(mut supervision) = state.lock() {
                        if let SidecarSupervision::Active(manager) = &mut *supervision {
                            if let Err(err) = manager.restart() {
                                eprintln!("KORTEX: failed to restart backend sidecar: {err}");
                                return;
                            }
                        }
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Mutex as StdMutex;

    /// `cargo test` runs test functions concurrently on multiple threads by
    /// default; process environment variables are global state. Every test
    /// below that reads/sets/removes `BACKEND_COMMAND_ENV`/
    /// `PYTHON_EXECUTABLE_ENV` acquires this lock first, so at most one of
    /// them touches the environment at a time — without it, two such tests
    /// interleaving could each observe the other's in-progress mutation.
    static ENV_LOCK: StdMutex<()> = StdMutex::new(());

    #[derive(Default)]
    struct MemoryKeyStore {
        values: StdMutex<HashMap<String, String>>,
    }

    impl KeyStore for MemoryKeyStore {
        fn load(&self, user: &str) -> Option<String> {
            self.values.lock().unwrap().get(user).cloned()
        }

        fn store(&self, user: &str, value: &str) {
            self.values.lock().unwrap().insert(user.to_string(), value.to_string());
        }
    }

    #[test]
    fn resolve_python_executable_falls_back_to_plain_python_when_no_venv_present() {
        let _guard = ENV_LOCK.lock().unwrap();
        std::env::remove_var(PYTHON_EXECUTABLE_ENV);
        let nonexistent = PathBuf::from("this-directory-does-not-exist-12345");
        assert_eq!(resolve_python_executable(&nonexistent), "python");
    }

    #[test]
    fn explicit_python_executable_env_var_takes_priority() {
        let _guard = ENV_LOCK.lock().unwrap();
        std::env::set_var(PYTHON_EXECUTABLE_ENV, "/custom/python3");
        let nonexistent = PathBuf::from("this-directory-does-not-exist-12345");
        let resolved = resolve_python_executable(&nonexistent);
        std::env::remove_var(PYTHON_EXECUTABLE_ENV);
        assert_eq!(resolved, "/custom/python3");
    }

    #[test]
    fn backend_command_override_is_used_verbatim_when_set() {
        let _guard = ENV_LOCK.lock().unwrap();
        std::env::remove_var(BACKEND_COMMAND_ENV);
        std::env::set_var(BACKEND_COMMAND_ENV, "my-frozen-backend --flag value");
        let store = MemoryKeyStore::default();
        let result = resolve_backend_sidecar_config(&store);
        std::env::remove_var(BACKEND_COMMAND_ENV);

        let config = result.expect("resolution should succeed");
        assert_eq!(config.program, "my-frozen-backend");
        assert_eq!(config.args, vec!["--flag".to_string(), "value".to_string()]);
        // The override path must still carry the persistent keys.
        assert!(config.env_vars.iter().any(|(k, _)| k == secure_keys::MASTER_KEY_ENV_VAR));
        assert!(config.env_vars.iter().any(|(k, _)| k == secure_keys::SIGNING_KEY_ENV_VAR));
    }

    #[test]
    fn dev_path_resolution_carries_pythonpath_and_persistent_keys() {
        let _guard = ENV_LOCK.lock().unwrap();
        std::env::remove_var(BACKEND_COMMAND_ENV);
        let store = MemoryKeyStore::default();
        let result = resolve_backend_sidecar_config(&store);

        // This assertion is environment-dependent (it requires the
        // monorepo's `backend/` directory to exist relative to this crate,
        // true for every dev/CI checkout this crate is ever built from) —
        // skip gracefully rather than fail spuriously in an unusual layout.
        let Ok(config) = result else { return };
        assert!(config.args.contains(&"uvicorn".to_string()));
        assert!(config.working_directory.is_some());
        assert!(config.env_vars.iter().any(|(k, _)| k == "PYTHONPATH"));
        assert!(config.env_vars.iter().any(|(k, _)| k == secure_keys::MASTER_KEY_ENV_VAR));
    }
}
