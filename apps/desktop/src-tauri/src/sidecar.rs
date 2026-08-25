//! Native process lifecycle management for the KORTEX backend sidecar.
//!
//! This module owns spawning, restart-on-crash, and shutdown of a child
//! process. It intentionally knows nothing about what that child process
//! is (no HTTP/health-check assumptions, no backend-specific protocol) —
//! the caller supplies the command to run via [`SidecarConfig`]. Readiness
//! probing and IPC are out of scope here and belong to later slices.
//!
//! **Sidecar configuration injection is intentionally deferred.** Nothing
//! in this M1.2 slice constructs a real [`SidecarConfig`] pointing at an
//! actual backend binary — see [`SidecarSupervision::Disabled`]. Backend
//! packaging (PyInstaller vs. Nuitka vs. other — still an open ADR item)
//! and the resulting on-disk path resolution are M3-and-later concerns;
//! this module only needs to work correctly once *some* command is
//! supplied, whatever it turns out to be.

use std::io::Write;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

/// Restart policy: bounded retry attempts with exponential backoff,
/// applied when the supervised child exits unexpectedly.
#[derive(Debug, Clone, Copy)]
pub struct RestartPolicy {
    pub max_attempts: u32,
    pub initial_backoff: Duration,
    pub backoff_multiplier: u32,
}

impl RestartPolicy {
    /// The ratified policy: max 3 attempts, 100ms/200ms/400ms backoff.
    fn ratified() -> Self {
        Self {
            max_attempts: 3,
            initial_backoff: Duration::from_millis(100),
            backoff_multiplier: 2,
        }
    }

    /// Backoff delay before restart attempt `attempt` (1-based).
    fn backoff_for_attempt(&self, attempt: u32) -> Duration {
        let exponent = attempt.saturating_sub(1);
        let multiplier = self.backoff_multiplier.saturating_pow(exponent);
        self.initial_backoff * multiplier
    }

    /// Whether restart attempt `attempt` (1-based) is still permitted.
    fn should_retry(&self, attempt: u32) -> bool {
        attempt <= self.max_attempts
    }
}

/// Configuration for a supervised sidecar child process.
///
/// The concrete program/binary is supplied by the caller — this module
/// makes no assumption about which backend it is supervising.
#[derive(Debug, Clone)]
pub struct SidecarConfig {
    pub program: String,
    pub args: Vec<String>,
    pub restart_policy: RestartPolicy,
    pub graceful_shutdown_timeout: Duration,
}

impl SidecarConfig {
    /// Builds a config with the architecture-mandated restart policy
    /// (max 3 attempts, 100ms/200ms/400ms exponential backoff) and the
    /// ratified 30 second graceful-shutdown grace period (`phase3_desktop_
    /// architecture.md` §6.4, citing `platform_runtime.md` §9).
    ///
    /// `program`/`args` are supplied by the caller — this constructor does
    /// not resolve or assume a backend binary path; that resolution is
    /// deferred to whichever later milestone supplies a real sidecar
    /// (packaging tool choice is still an open ADR item).
    pub fn new(program: impl Into<String>, args: Vec<String>) -> Self {
        Self {
            program: program.into(),
            args,
            restart_policy: RestartPolicy::ratified(),
            graceful_shutdown_timeout: Duration::from_secs(30),
        }
    }
}

/// Internal lifecycle state of a supervised sidecar. Transition methods
/// live directly on this type (below) rather than behind a generic
/// `(state, event) -> state` dispatch table — there are exactly five
/// transitions, each with exactly one call site in [`SidecarManager`], so
/// a named method per transition is simpler than a general-purpose event
/// abstraction with a single internal consumer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SidecarState {
    NotStarted,
    Running,
    Restarting,
    ShuttingDown,
    Terminated,
    Failed,
}

impl SidecarState {
    fn after_spawn_succeeded(self) -> Self {
        match self {
            SidecarState::NotStarted | SidecarState::Restarting => SidecarState::Running,
            other => other,
        }
    }

    fn after_unexpected_exit(self) -> Self {
        match self {
            SidecarState::Running | SidecarState::Restarting => SidecarState::Restarting,
            other => other,
        }
    }

    fn after_restarts_exhausted(self) -> Self {
        match self {
            SidecarState::Restarting => SidecarState::Failed,
            other => other,
        }
    }

    fn after_shutdown_requested(self) -> Self {
        match self {
            SidecarState::NotStarted => SidecarState::Terminated,
            SidecarState::Running | SidecarState::Restarting => SidecarState::ShuttingDown,
            other => other,
        }
    }

    fn after_process_confirmed_terminated(self) -> Self {
        match self {
            SidecarState::ShuttingDown => SidecarState::Terminated,
            other => other,
        }
    }
}

/// Outcome of handling an unexpected sidecar exit.
#[derive(Debug, PartialEq, Eq)]
pub enum SidecarOutcome {
    /// Caller should wait `backoff` and then call [`SidecarManager::restart`].
    WillRestart { attempt: u32, backoff: Duration },
    /// Restart attempts are exhausted; the sidecar is considered failed.
    Failed,
}

/// Owns and supervises a single sidecar child process.
///
/// `SidecarManager` is the sole owner of the child's lifecycle: spawning,
/// restart-on-crash bookkeeping, and graceful/forced shutdown. It does not
/// perform readiness polling, IPC, or make any assumption about what the
/// child process is.
pub struct SidecarManager {
    config: SidecarConfig,
    child: Option<Child>,
    state: SidecarState,
    restart_count: u32,
}

impl SidecarManager {
    pub fn new(config: SidecarConfig) -> Self {
        Self {
            config,
            child: None,
            state: SidecarState::NotStarted,
            restart_count: 0,
        }
    }

    /// Spawns the configured child process. Stdin is piped so a graceful
    /// shutdown can signal the child by closing it (EOF).
    pub fn spawn(&mut self) -> std::io::Result<()> {
        let child = Command::new(&self.config.program)
            .args(&self.config.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;

        self.child = Some(child);
        self.state = self.state.after_spawn_succeeded();
        Ok(())
    }

    /// Returns true if the child is still running. Reaps the child handle
    /// if it has exited.
    pub fn is_running(&mut self) -> std::io::Result<bool> {
        match self.child.as_mut() {
            None => Ok(false),
            Some(child) => match child.try_wait()? {
                Some(_status) => {
                    self.child = None;
                    Ok(false)
                }
                None => Ok(true),
            },
        }
    }

    /// Applies the restart policy to an unexpected child exit: increments
    /// the restart counter and reports whether/how long to wait before the
    /// caller should invoke [`SidecarManager::restart`]. Does not sleep or
    /// spawn itself, keeping this logic synchronous and side-effect-free.
    pub fn handle_unexpected_exit(&mut self) -> SidecarOutcome {
        self.state = self.state.after_unexpected_exit();
        self.restart_count += 1;

        if self.config.restart_policy.should_retry(self.restart_count) {
            SidecarOutcome::WillRestart {
                attempt: self.restart_count,
                backoff: self
                    .config
                    .restart_policy
                    .backoff_for_attempt(self.restart_count),
            }
        } else {
            self.state = self.state.after_restarts_exhausted();
            SidecarOutcome::Failed
        }
    }

    /// Re-spawns the child after a restart decision from
    /// [`SidecarManager::handle_unexpected_exit`].
    pub fn restart(&mut self) -> std::io::Result<()> {
        self.spawn()
    }

    /// Requests graceful termination: closes the child's stdin as a
    /// process-agnostic shutdown cue, waits up to
    /// `graceful_shutdown_timeout`, and force-terminates if the child has
    /// not exited by then. Guarantees no orphaned process is left behind.
    pub fn graceful_shutdown(&mut self) -> std::io::Result<()> {
        self.state = self.state.after_shutdown_requested();

        let Some(child) = self.child.as_mut() else {
            self.state = self.state.after_process_confirmed_terminated();
            return Ok(());
        };

        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.flush();
            drop(stdin);
        }

        let deadline = Instant::now() + self.config.graceful_shutdown_timeout;
        loop {
            if child.try_wait()?.is_some() {
                self.child = None;
                self.state = self.state.after_process_confirmed_terminated();
                return Ok(());
            }
            if Instant::now() >= deadline {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }

        self.force_terminate()
    }

    /// Immediately kills the child process and waits for it to exit.
    /// Used as the fallback when graceful shutdown times out, and as the
    /// safety net on [`Drop`].
    pub fn force_terminate(&mut self) -> std::io::Result<()> {
        if let Some(child) = self.child.as_mut() {
            // Killing an already-exited process is a benign no-op error on
            // both Windows and Unix; ignore it rather than surface a false
            // failure from shutdown.
            let _ = child.kill();
            let _ = child.wait();
        }
        self.child = None;
        self.state = self.state.after_process_confirmed_terminated();
        Ok(())
    }

    /// Convenience wrapper for callers (e.g. the app exit handler) that
    /// don't need to observe shutdown errors.
    pub fn shutdown(&mut self) {
        let _ = self.graceful_shutdown();
    }
}

impl Drop for SidecarManager {
    fn drop(&mut self) {
        // Safety net: never leave an orphaned child process behind, even if
        // an explicit shutdown call was missed on some exit path.
        if self.child.is_some() {
            let _ = self.force_terminate();
        }
    }
}

/// Explicit sidecar supervision state, meant to be registered as Tauri
/// managed app state (see `lib.rs`).
///
/// `Disabled` and "a `SidecarManager` exists but has no child running yet"
/// are deliberately different states — collapsing them into a single
/// `Option<SidecarManager>` would make "supervision is turned off"
/// indistinguishable from "supervision exists but hasn't started", which
/// is not the same fact. For the whole of this M1.2 slice the app state is
/// always `Disabled`: sidecar configuration injection is intentionally
/// deferred (see module docs) until a real backend binary/path exists.
pub enum SidecarSupervision {
    Disabled,
    Active(SidecarManager),
}

impl SidecarSupervision {
    /// Requests graceful shutdown if a sidecar is actively supervised.
    /// A no-op when `Disabled`. Also safe to call more than once — a
    /// second call against an already-terminated manager is itself a
    /// no-op (see [`SidecarManager::graceful_shutdown`]), which is what
    /// lets both the `CloseRequested` and `ExitRequested` hooks in
    /// `lib.rs` call this without needing to coordinate who goes first.
    pub fn shutdown(&mut self) {
        if let SidecarSupervision::Active(manager) = self {
            manager.shutdown();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_config() -> SidecarConfig {
        SidecarConfig::new("kortex-backend-placeholder", vec![])
    }

    // --- backoff calculation ---

    #[test]
    fn backoff_follows_100_200_400ms_policy() {
        let policy = RestartPolicy::ratified();
        assert_eq!(policy.backoff_for_attempt(1), Duration::from_millis(100));
        assert_eq!(policy.backoff_for_attempt(2), Duration::from_millis(200));
        assert_eq!(policy.backoff_for_attempt(3), Duration::from_millis(400));
    }

    #[test]
    fn backoff_respects_custom_multiplier_and_base() {
        let policy = RestartPolicy {
            max_attempts: 3,
            initial_backoff: Duration::from_millis(50),
            backoff_multiplier: 3,
        };
        assert_eq!(policy.backoff_for_attempt(1), Duration::from_millis(50));
        assert_eq!(policy.backoff_for_attempt(2), Duration::from_millis(150));
        assert_eq!(policy.backoff_for_attempt(3), Duration::from_millis(450));
    }

    // --- retry count ---

    #[test]
    fn retry_allowed_up_to_max_attempts() {
        let policy = RestartPolicy::ratified(); // max_attempts = 3
        assert!(policy.should_retry(1));
        assert!(policy.should_retry(2));
        assert!(policy.should_retry(3));
        assert!(!policy.should_retry(4));
    }

    #[test]
    fn handle_unexpected_exit_reports_backoff_then_fails_after_max_attempts() {
        let mut manager = SidecarManager::new(test_config());

        let first = manager.handle_unexpected_exit();
        assert_eq!(
            first,
            SidecarOutcome::WillRestart {
                attempt: 1,
                backoff: Duration::from_millis(100)
            }
        );

        let second = manager.handle_unexpected_exit();
        assert_eq!(
            second,
            SidecarOutcome::WillRestart {
                attempt: 2,
                backoff: Duration::from_millis(200)
            }
        );

        let third = manager.handle_unexpected_exit();
        assert_eq!(
            third,
            SidecarOutcome::WillRestart {
                attempt: 3,
                backoff: Duration::from_millis(400)
            }
        );

        let fourth = manager.handle_unexpected_exit();
        assert_eq!(fourth, SidecarOutcome::Failed);
    }

    // --- state transitions ---

    #[test]
    fn state_transitions_through_normal_lifecycle() {
        let mut state = SidecarState::NotStarted;

        state = state.after_spawn_succeeded();
        assert_eq!(state, SidecarState::Running);

        state = state.after_shutdown_requested();
        assert_eq!(state, SidecarState::ShuttingDown);

        state = state.after_process_confirmed_terminated();
        assert_eq!(state, SidecarState::Terminated);
    }

    #[test]
    fn state_transitions_through_crash_and_restart() {
        let mut state = SidecarState::NotStarted;
        state = state.after_spawn_succeeded();
        assert_eq!(state, SidecarState::Running);

        state = state.after_unexpected_exit();
        assert_eq!(state, SidecarState::Restarting);

        state = state.after_spawn_succeeded();
        assert_eq!(state, SidecarState::Running);
    }

    #[test]
    fn state_transitions_to_failed_after_restarts_exhausted() {
        let mut state = SidecarState::NotStarted;
        state = state.after_spawn_succeeded();
        state = state.after_unexpected_exit();
        assert_eq!(state, SidecarState::Restarting);

        state = state.after_restarts_exhausted();
        assert_eq!(state, SidecarState::Failed);
    }

    #[test]
    fn invalid_transition_is_a_no_op() {
        // A terminated sidecar ignores a stray "unexpected exit" event
        // instead of moving to an undefined state.
        let state = SidecarState::Terminated;
        assert_eq!(state.after_unexpected_exit(), SidecarState::Terminated);
    }

    // --- explicit supervision state ---

    #[test]
    fn disabled_supervision_shutdown_is_a_no_op() {
        // Must not panic or attempt to touch a process that doesn't exist.
        let mut supervision = SidecarSupervision::Disabled;
        supervision.shutdown();
    }

    // --- process lifecycle (spawn / graceful shutdown / forced termination) ---
    //
    // Uses always-present OS utilities rather than a custom-compiled test
    // binary. A separate `[[bin]]` target (auto-discovered from
    // `src/bin/*.rs`) would be built by a plain `cargo build`/`tauri
    // build` by default, which is a real risk of a test-only helper
    // ending up in release build output. `sort`/`cat` and
    // `timeout`/`sleep` are stable, always-available OS commands with
    // well-known stdin behavior, so there's no such risk here, and no
    // extra binary target or public API surface is needed to reach them.

    /// A command that blocks reading stdin and exits promptly once stdin
    /// reaches EOF — used to test that graceful shutdown succeeds when the
    /// child responds to its stdin being closed.
    fn stdin_responsive_command() -> (String, Vec<String>) {
        if cfg!(windows) {
            ("sort".to_string(), vec![])
        } else {
            ("cat".to_string(), vec![])
        }
    }

    /// A command that ignores stdin and runs on its own timer — used to
    /// test that graceful shutdown falls back to forced termination.
    fn stdin_unresponsive_command() -> (String, Vec<String>) {
        if cfg!(windows) {
            (
                "cmd".to_string(),
                vec![
                    "/C".to_string(),
                    "timeout".to_string(),
                    "/T".to_string(),
                    "30".to_string(),
                    "/NOBREAK".to_string(),
                ],
            )
        } else {
            (
                "sh".to_string(),
                vec!["-c".to_string(), "sleep 30".to_string()],
            )
        }
    }

    #[test]
    fn spawn_then_force_terminate_leaves_no_running_child() {
        let (program, args) = stdin_unresponsive_command();
        let mut manager = SidecarManager::new(SidecarConfig::new(program, args));

        manager.spawn().expect("failed to spawn test process");
        assert!(manager.is_running().expect("is_running failed"));

        manager.force_terminate().expect("force_terminate failed");
        assert!(!manager.is_running().expect("is_running failed"));
    }

    #[test]
    fn graceful_shutdown_succeeds_when_child_responds_to_stdin_close() {
        let (program, args) = stdin_responsive_command();
        let mut config = SidecarConfig::new(program, args);
        config.graceful_shutdown_timeout = Duration::from_secs(5);
        let mut manager = SidecarManager::new(config);

        manager.spawn().expect("failed to spawn test process");
        assert!(manager.is_running().expect("is_running failed"));

        manager
            .graceful_shutdown()
            .expect("graceful_shutdown failed");

        assert!(!manager.is_running().expect("is_running failed"));
    }

    #[test]
    fn graceful_shutdown_falls_back_to_forced_termination_on_timeout() {
        let (program, args) = stdin_unresponsive_command();
        let mut config = SidecarConfig::new(program, args);
        // This command ignores stdin closing, so graceful shutdown must
        // fall back to a forced kill; keep the timeout short so the test
        // stays fast.
        config.graceful_shutdown_timeout = Duration::from_millis(100);
        let mut manager = SidecarManager::new(config);

        manager.spawn().expect("failed to spawn test process");
        manager
            .graceful_shutdown()
            .expect("graceful_shutdown failed");

        assert!(!manager.is_running().expect("is_running failed"));
    }
}
