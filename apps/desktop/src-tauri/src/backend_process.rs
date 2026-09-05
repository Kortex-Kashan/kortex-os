//! Milestone M7.1 (dev-parity spawn) + Desktop Installers (production
//! spawn): resolves the real KORTEX backend command and connects the
//! `sidecar::SidecarManager` to the desktop app's actual startup/shutdown
//! lifecycle.
//!
//! **Two resolution paths, selected by build type (`cfg!(debug_assertions)`),
//! per implementation_plan.md Part 2 (Desktop Installers):**
//!
//! - **Dev build** (`cargo tauri dev` / debug): unchanged M7.1 behavior —
//!   `python -m uvicorn kortex.api.main:app`, from a Python interpreter,
//!   with `backend/src` on `PYTHONPATH`, cwd set to the monorepo's
//!   `backend/` directory (`backend_source_dir()`, resolved via the
//!   compile-time `CARGO_MANIFEST_DIR` — correct only for a same-machine
//!   source checkout, which is exactly what dev mode is).
//! - **Production/release build**: the frozen backend bundled via
//!   `tauri.conf.json`'s `bundle.resources` (built by
//!   `installer/pyinstaller/kortex_backend.spec`), located at runtime via
//!   `app.path().resource_dir()` — never the compile-time-baked path above,
//!   which is proven (implementation_plan.md Part 2 SS D3) to fail
//!   deterministically on any machine without the original build's monorepo
//!   checkout present. Spawned with an explicit `working_directory` and
//!   `KORTEX_STORAGE_DIR` pointed at the app-data directory
//!   (`resolve_app_data_dir`) — Control 2's single authoritative
//!   persistent-data root, so `StorageEngine`/`BackupEngine`/etc. never
//!   attempt to write under the (potentially read-only, e.g.
//!   `Program Files`) install directory.
//!
//! `KORTEX_BACKEND_COMMAND` remains the escape hatch that bypasses both
//! paths entirely, unchanged, checked first in either build type.

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

/// True in a `cargo tauri dev`/debug build, false in a `tauri build`
/// release build — the switch between the two resolution paths documented
/// at module level. Standard, dependency-free Rust idiom; no new crate
/// dependency introduced for this.
fn is_dev_build() -> bool {
    cfg!(debug_assertions)
}

/// Filename of the frozen backend executable within its bundled directory,
/// platform-appropriate (matches `installer/pyinstaller/kortex_backend.spec`'s
/// `name="kortex-backend"`).
fn frozen_backend_exe_name() -> &'static str {
    if cfg!(windows) {
        "kortex-backend.exe"
    } else {
        "kortex-backend"
    }
}

/// Resolves the command Tauri should spawn to bring the backend up, plus
/// the environment it needs: the two persistent master/signing keys (see
/// `secure_keys`) always; `PYTHONPATH` for the dev-parity path;
/// `KORTEX_STORAGE_DIR` + an explicit `working_directory` for the
/// production path (Control 2). `key_store` is injected so this stays
/// unit-testable against an in-memory double, mirroring `secure_keys`'s own
/// pattern. `bundled_backend_dir`/`app_data_dir` are pre-resolved by the
/// caller (which holds the `AppHandle` this function does not need directly,
/// keeping it testable with plain paths) — see `resolve_bundled_backend_dir`/
/// `resolve_app_data_dir`.
pub fn resolve_backend_sidecar_config(
    key_store: &dyn KeyStore,
    bundled_backend_dir: Option<&Path>,
    app_data_dir: Option<&Path>,
) -> Result<SidecarConfig, String> {
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

    if !is_dev_build() {
        if let Some(bundled_dir) = bundled_backend_dir {
            return resolve_backend_sidecar_config_production_path_with_keys(
                bundled_dir,
                app_data_dir,
                env_vars,
            );
        }
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

/// Builds the production-path `SidecarConfig` given already-resolved
/// backend keys — the actual construction logic, factored out of
/// `resolve_backend_sidecar_config` so it is directly unit-testable without
/// depending on `is_dev_build()` (which is always `true` under `cargo
/// test`, itself a debug build, and so can never be exercised by flipping
/// that flag in a test).
fn resolve_backend_sidecar_config_production_path_with_keys(
    bundled_dir: &Path,
    app_data_dir: Option<&Path>,
    mut env_vars: Vec<(String, String)>,
) -> Result<SidecarConfig, String> {
    let exe = bundled_dir.join(frozen_backend_exe_name());
    let args = vec![
        "--host".to_string(),
        "127.0.0.1".to_string(),
        "--port".to_string(),
        "8000".to_string(),
    ];
    let mut config = SidecarConfig::new(exe.to_string_lossy().to_string(), args);
    if let Some(data_dir) = app_data_dir {
        // Control 2 (implementation_plan.md Part 2 SS D9/SS D25): the ONE
        // authoritative persistent-data root. `KORTEX_STORAGE_DIR` is left as
        // the plain relative string the backend already reads
        // (kernel_bootstrap.py) -- setting an explicit cwd here is what
        // makes that relative string resolve under the app-data directory
        // instead of an undetermined (and possibly read-only, e.g.
        // `Program Files`) location.
        config.working_directory = Some(data_dir.to_path_buf());
        env_vars.push(("KORTEX_STORAGE_DIR".to_string(), "storage_data".to_string()));
        // Without this, the SQLite database resolves via the Python side's
        // OWN independent `_default_app_data_dir()` computation, which uses
        // a hardcoded "KORTEX" folder name -- a real, different directory
        // than Tauri's own `app_data_dir()` (identifier-based, e.g.
        // "com.kortex.desktop"). Discovered during this milestone's own
        // installed-artifact testing: `storage_data`/backups landed under
        // the Tauri-resolved root while the database landed under a
        // sibling-but-different one. Setting this explicitly unifies both
        // under the exact same directory, satisfying Control 2's "ONE
        // authoritative root" requirement literally, not just for storage.
        let db_path = data_dir.join("storage_data").join("kortex_local.db");
        env_vars.push((
            "KORTEX_DATABASE_URL".to_string(),
            format!("sqlite+aiosqlite:///{}", db_path.to_string_lossy().replace('\\', "/")),
        ));
    }
    config.env_vars = env_vars;
    Ok(config)
}

/// Test-only convenience wrapper: resolves keys from `key_store` and builds
/// the production-path config, without needing `is_dev_build()` to report
/// `false` (see the factoring-out note above).
#[cfg(test)]
fn resolve_backend_sidecar_config_production_path(
    key_store: &dyn KeyStore,
    bundled_dir: &Path,
    app_data_dir: &Path,
) -> Result<SidecarConfig, String> {
    let env_vars = secure_keys::load_or_generate_backend_keys(key_store)?;
    resolve_backend_sidecar_config_production_path_with_keys(bundled_dir, Some(app_data_dir), env_vars)
}

/// Directory containing the frozen backend's `--onedir` bundle, once
/// installed — resolved via Tauri's own resource-directory API, replacing
/// `backend_source_dir()`'s compile-time-baked path for production builds
/// (implementation_plan.md Part 2 SS D3/SS D7). `backend_source_dir()` itself
/// is retained, unchanged, for the dev-mode path only.
fn resolve_bundled_backend_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("could not resolve app resource directory: {e}"))?;
    let backend_dir = resource_dir.join("kortex-backend");
    if !backend_dir.is_dir() {
        return Err(format!(
            "bundled backend directory not found at {} — this build was not packaged with the \
             frozen backend resource (see installer/pyinstaller/kortex_backend.spec and \
             tauri.conf.json's bundle.resources).",
            backend_dir.display()
        ));
    }
    Ok(backend_dir)
}

/// Control 2 (implementation_plan.md Part 2 SS D9/SS D25): the ONE
/// authoritative persistent-application-data directory for production
/// desktop mode, resolved via Tauri's own `app_data_dir()` — already
/// platform-correct (`%APPDATA%\<identifier>\` on Windows, etc.), the same
/// *kind* of OS-app-data path `_default_app_data_dir()` already uses on the
/// Python side (independently computed, agreeing in kind, not by
/// coincidence). Created if it does not yet exist. Every engine's own
/// relative default resolves consistently under this one root because it
/// becomes the spawned backend process's `working_directory` — no engine's
/// internal default changes, and no second storage abstraction is
/// introduced.
fn resolve_app_data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("could not resolve app-data directory: {e}"))?;
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("could not create app-data directory {}: {e}", dir.display()))?;
    Ok(dir)
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
    // Best-effort: absent in dev builds (never consulted there) and
    // harmless to omit in a production build missing the bundled resource
    // — resolution falls through to the existing dev-parity path, which
    // will itself fail with the same, already-handled error UX.
    let bundled_backend_dir = resolve_bundled_backend_dir(&app).ok();
    let app_data_dir = resolve_app_data_dir(&app).ok();

    let config = match resolve_backend_sidecar_config(
        &secure_keys::KeyringKeyStore,
        bundled_backend_dir.as_deref(),
        app_data_dir.as_deref(),
    ) {
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

    // `tokio::spawn` requires an *ambient* Tokio runtime reachable via
    // thread-local `Handle::current()` -- calling it from inside Tauri's
    // synchronous `.setup()` closure (this function's actual caller,
    // `lib.rs`) has no such context and panics at runtime with "there is no
    // reactor running, must be called from the context of a Tokio 1.x
    // runtime". This was a real, previously-unexercised defect: `cargo
    // check`/`cargo test`/`cargo clippy` all pass regardless (the panic is
    // a *runtime* condition, not a compile-time one), and it was only
    // caught by actually launching a real installed build -- proof that
    // "the build succeeded" is not the same evidence as "the installed
    // artifact works" (implementation_plan.md Part 2, hard acceptance
    // requirement). `tauri::async_runtime::spawn` is Tauri's own runtime-
    // agnostic wrapper, submitting the task to whichever async runtime
    // Tauri itself manages regardless of the calling context -- the
    // documented, correct way to spawn a background task from `.setup()`.
    tauri::async_runtime::spawn(monitor_loop(app));
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
        fn load(&self, user: &str) -> secure_keys::KeyLoadResult {
            match self.values.lock().unwrap().get(user).cloned() {
                Some(value) => secure_keys::KeyLoadResult::Found(value),
                None => secure_keys::KeyLoadResult::ConfirmedAbsent,
            }
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
        let result = resolve_backend_sidecar_config(&store, None, None);
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
        // `is_dev_build()` is `cfg!(debug_assertions)` -- true for `cargo
        // test`'s own build, so passing `Some(...)` bundled dirs here would
        // never actually be consulted; this test exercises exactly the path
        // it names regardless of what's passed, but passing `None` keeps the
        // scenario unambiguous.
        let result = resolve_backend_sidecar_config(&store, None, None);

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

    #[test]
    fn production_path_with_bundled_backend_uses_frozen_exe_and_sets_storage_root() {
        // Exercises the production-path construction logic directly via
        // `resolve_backend_sidecar_config_production_path`, factored out
        // specifically so this doesn't depend on `is_dev_build()` reporting
        // `false` (impossible under `cargo test`, itself a debug build).
        let _guard = ENV_LOCK.lock().unwrap();
        std::env::remove_var(BACKEND_COMMAND_ENV);
        let store = MemoryKeyStore::default();
        let bundled_dir = PathBuf::from("C:/Program Files/KORTEX Desktop/resources/kortex-backend");
        let app_data_dir = PathBuf::from("C:/Users/test/AppData/Roaming/KORTEX Desktop");

        let config =
            resolve_backend_sidecar_config_production_path(&store, &bundled_dir, &app_data_dir)
                .expect("resolution should succeed");

        assert!(config.program.ends_with(frozen_backend_exe_name()));
        assert!(config.program.contains("kortex-backend"));
        assert_eq!(config.working_directory.as_deref(), Some(app_data_dir.as_path()));
        assert!(
            config
                .env_vars
                .iter()
                .any(|(k, v)| k == "KORTEX_STORAGE_DIR" && v == "storage_data"),
            "must set KORTEX_STORAGE_DIR so it resolves under the app-data root, not an \
             undetermined (possibly read-only) directory"
        );
        assert!(
            config
                .env_vars
                .iter()
                .any(|(k, v)| k == "KORTEX_DATABASE_URL" && v.contains("storage_data")),
            "the database must be unified under the SAME app-data root as storage_data, not a \
             separately-computed default (a real divergence found during installed-artifact testing)"
        );
        assert!(config.env_vars.iter().any(|(k, _)| k == secure_keys::MASTER_KEY_ENV_VAR));
    }
}
