//! Browser-B3: `BrowserProfileStore` — persistent, tenant-scoped browser
//! profile identity, storage, locking, and lifecycle.
//!
//! Sits ALONGSIDE `browser_runtime.rs`'s `BrowserRuntime` (surface
//! lifecycle: create/navigate/reload/destroy an embedded WebView2 child
//! webview), never inside it — `browser_runtime.rs`'s own module doc
//! already states persistent profiles are deliberately not its job.
//! `BrowserRuntime::create_surface` consumes a `BrowserProfileId`'s
//! resolved on-disk directory (via `resolve_profile_directory` in this
//! module) but has no knowledge of tenants, locks, or profile metadata.
//!
//! **Tenant identity boundary (OD-B7)**: every tenant-scoped operation in
//! this module requires an already-resolved `tenant_id: &str`, obtained
//! by the caller (a Tauri command) from `IpcClientState::current_tenant_id()`
//! — never accepted as a parameter from the frontend. `None` means "no
//! authoritative tenant identity available in this process yet" and every
//! public entry point in this module's caller must fail closed on that,
//! never substitute a default/shared/OS-user-scoped directory. This
//! module itself takes `tenant_id: &str` (already resolved, never
//! `Option`) precisely so it is structurally impossible to call it
//! without one — the `Option`-to-fail-closed decision lives one layer up,
//! at the Tauri command boundary, not duplicated here.
//!
//! **Opaque WebView2 data boundary**: this module owns a profile's
//! *identity* and its *directory's existence* — never what WebView2
//! writes inside `<profile>/webview2-data/`. No code here (or anywhere in
//! this crate) parses cookies, LevelDB, IndexedDB, or any other WebView2-
//! internal file format.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};

/// One file name, directly under the tenant directory (never a bare
/// tenant subdirectory of `browser-profiles/`), that a legitimate
/// `BrowserProfileId::generate()` can never collide with — every
/// generated id is prefixed `profile-`, this is not.
const LEGACY_QUARANTINE_DIRNAME: &str = "_legacy-quarantine";
const LEGACY_DEFAULT_PROFILE_DIRNAME: &str = "default";

/// Opaque, persisted profile identifier. Cryptographically random —
/// unlike `BrowserSurfaceId::generate()` (nanosecond timestamp + an
/// in-process monotonic counter, sufficient only because that id is
/// in-memory-only for one process's lifetime), THIS id is written to
/// disk, used as a directory name, and must remain unguessable across
/// process restarts — a caller enumerating plausible `BrowserProfileId`
/// values must never have a practical path to another tenant's profile.
/// Never derived from `display_name` or any other frontend-supplied
/// text.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct BrowserProfileId(String);

impl BrowserProfileId {
    /// 128 bits of OS-CSPRNG randomness, hex-encoded. Matches the exact
    /// `getrandom::getrandom(&mut bytes)` pattern `secure_keys.rs` already
    /// uses for this crate's persistent master/signing keys — same
    /// dependency (already direct in `Cargo.toml`), same call shape, no
    /// new crate for this one additional use.
    pub fn generate() -> Self {
        let mut bytes = [0u8; 16];
        getrandom::getrandom(&mut bytes).expect(
            "the OS CSPRNG must be available to generate a browser profile id; \
             this mirrors secure_keys.rs's identical, unconditional expectation",
        );
        let hex: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
        Self(format!("profile-{hex}"))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Reconstructs a `BrowserProfileId` from an on-disk directory name —
    /// valid because a profile's directory is always named exactly
    /// `profile_id.as_str()` (see `resolve_profile_directory`). Returns
    /// `None` for anything that isn't a recognized profile directory
    /// (e.g. `_legacy-quarantine`, or a `-deleted-<ts>` remnant of a
    /// prior deletion) — `list_profiles` relies on this to skip them
    /// without needing a separate allowlist.
    fn from_directory_name(name: &std::ffi::OsStr) -> Option<Self> {
        let s = name.to_str()?;
        if s.starts_with("profile-") && !s.contains("-deleted-") {
            Some(Self(s.to_string()))
        } else {
            None
        }
    }

    #[cfg(test)]
    fn from_raw_for_test(raw: &str) -> Self {
        Self(raw.to_string())
    }
}

impl std::fmt::Display for BrowserProfileId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// A profile record's own lifecycle progress — distinct from both
/// "corrupted" (a read-time failure mode, computed when `profile.json`
/// fails to parse, never itself a stored field — storing "I am
/// corrupted" inside the very file that might be corrupted is circular)
/// and "locked" (a live, transient runtime fact tracked by the lock file,
/// never persisted in metadata). `PendingDeletion` exists so a deletion
/// that removes the registry entry before the physical directory is
/// fully removed (§13's atomic-deletion requirement) never leaves a
/// half-deleted profile listed as `Active`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProfileRecordState {
    Active,
    PendingDeletion,
}

/// KORTEX-owned profile metadata — written to `profile.json`, kept
/// strictly OUTSIDE `webview2-data/` so WebView2 never sees or can
/// corrupt it (and so this file's own corruption can never be confused
/// with WebView2-internal corruption). `display_name` is presentation
/// metadata only — never used to construct a filesystem path;
/// `profile_id` alone names the directory.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProfileMetadata {
    pub profile_id: BrowserProfileId,
    pub display_name: String,
    /// Unix timestamp (seconds). A plain integer, not a calendar/locale-
    /// aware type: this crate has no existing date/time dependency
    /// convention to match, and this field is never formatted for
    /// display in Rust — the frontend formats it if/when shown.
    pub created_at: u64,
    pub last_opened_at: Option<u64>,
    /// Starts at 1. No migration logic exists yet (Browser-B3 introduces
    /// the very first schema) — this field exists so a future phase can
    /// detect and migrate an older on-disk shape without a breaking
    /// silent misread.
    pub schema_version: u32,
    pub state: ProfileRecordState,
}

pub const CURRENT_PROFILE_SCHEMA_VERSION: u32 = 1;

/// The runtime-observed availability of a profile — computed by
/// `BrowserProfileStore` when listing/opening, never itself persisted
/// (see `ProfileRecordState`'s own doc comment for why "locked" and
/// "corrupted" live here instead of in `ProfileMetadata`).
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum ProfileAvailability {
    Available,
    Locked,
    Corrupted { reason: String },
}

/// Every failure mode a `BrowserProfileStore` operation can produce.
/// Deliberately NOT folded into `browser_runtime::BrowserRuntimeError` —
/// that type's own doc comment states its boundary is surface lifecycle
/// only; profile lifecycle failures are a distinct concern with a
/// distinct, wider set of fail-closed reasons (tenant identity, storage
/// root, ACL, locking, corruption) that don't apply to a surface at all.
///
/// `#[serde(rename_all = "camelCase")]` on an enum only renames VARIANT
/// names (`"kind"`, here) — it does NOT cascade into a struct-like
/// variant's own field names, a real serde behavior confirmed empirically
/// while writing this type's own tests (a naive first draft of this enum
/// serialized `profile_id`, not `profileId`, despite the container-level
/// attribute). Every field below is therefore renamed explicitly.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum BrowserProfileError {
    /// No authoritative tenant identity is available in this process yet
    /// (`IpcClientState::current_tenant_id()` returned `None`) — the
    /// fail-closed outcome OD-B7 exists to make possible. Never
    /// substituted with a default/shared/OS-user-scoped directory.
    ProfileIdentityUnavailable,
    /// The profile storage root (under the app's data directory) could
    /// not be resolved or created.
    ProfileStorageUnavailable {
        message: String,
    },
    ProfileNotFound {
        #[serde(rename = "profileId")]
        profile_id: BrowserProfileId,
    },
    AlreadyExists {
        #[serde(rename = "profileId")]
        profile_id: BrowserProfileId,
    },
    /// A resolved path escaped the expected tenant/profile root —
    /// treated as tampering, never silently corrected.
    ProfilePathViolation {
        #[serde(rename = "profileId")]
        profile_id: BrowserProfileId,
    },
    ProfileLocked {
        #[serde(rename = "profileId")]
        profile_id: BrowserProfileId,
    },
    ProfileCorrupted {
        #[serde(rename = "profileId")]
        profile_id: BrowserProfileId,
        reason: String,
    },
    Platform {
        message: String,
    },
}

impl std::fmt::Display for BrowserProfileError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ProfileIdentityUnavailable => {
                write!(f, "no authoritative tenant identity is available")
            }
            Self::ProfileStorageUnavailable { message } => {
                write!(f, "browser profile storage is unavailable: {message}")
            }
            Self::ProfileNotFound { profile_id } => {
                write!(f, "no browser profile with id {profile_id:?}")
            }
            Self::AlreadyExists { profile_id } => {
                write!(f, "a browser profile with id {profile_id:?} already exists")
            }
            Self::ProfilePathViolation { profile_id } => {
                write!(
                    f,
                    "profile {profile_id:?} resolved outside its expected root"
                )
            }
            Self::ProfileLocked { profile_id } => {
                write!(
                    f,
                    "browser profile {profile_id:?} is already open elsewhere"
                )
            }
            Self::ProfileCorrupted { profile_id, reason } => {
                write!(f, "browser profile {profile_id:?} is corrupted: {reason}")
            }
            Self::Platform { message } => write!(f, "browser profile store error: {message}"),
        }
    }
}

impl std::error::Error for BrowserProfileError {}

/// Keeps only ASCII alphanumerics, `-`, and `_` — the exact same defense-
/// in-depth policy `browser_runtime::sanitize_profile_id` already applies
/// to the (unrelated, process-lifetime) `profile_id` concept there. Used
/// here on `tenant_id` (already a trusted, OD-B7-resolved value, never
/// frontend-supplied — this is defense-in-depth, not the primary trust
/// boundary) and is redundant-but-harmless on `BrowserProfileId`, whose
/// own `generate()` already only ever produces this charset.
fn sanitize_path_component(value: &str) -> String {
    let cleaned: String = value
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .take(128)
        .collect();
    if cleaned.is_empty() {
        "invalid".to_string()
    } else {
        cleaned
    }
}

/// Resolves the base directory every tenant's profiles live under.
pub fn browser_profiles_root(app_data_dir: &Path) -> PathBuf {
    app_data_dir.join("browser-profiles")
}

/// Resolves one tenant's own subdirectory, hardened with the same
/// canonicalize-then-verify-containment sequence
/// `backend/src/kortex/engines/storage/sandbox.py::PathSandboxValidator`
/// uses (confirmed via direct source inspection during the Browser-B3
/// architecture gate — that Python validator has no Rust equivalent
/// anywhere in this crate yet; this is the first port of its algorithm).
/// Charset sanitization alone (as `browser_runtime.rs`'s existing
/// `sanitize_profile_id` does) is necessary but not sufficient for a
/// PERSISTED, tenant-boundary-adjacent path — this additionally
/// canonicalizes the joined path and verifies it is still contained
/// within `root`, which also defeats a symlink/reparse-point planted at
/// the expected subdirectory location.
///
/// Returns `Err` (never a silently-corrected path) if containment cannot
/// be verified — fail-closed on path-traversal/tampering, matching the
/// Python validator's own behavior.
pub fn resolve_tenant_directory(
    root: &Path,
    tenant_id: &str,
) -> Result<PathBuf, BrowserProfileError> {
    resolve_child_directory(root, tenant_id)
}

/// Same hardening as `resolve_tenant_directory`, one level deeper:
/// `<tenant_root>/<profile_id>`. Every caller of this function already
/// guarantees `tenant_root` exists (via `BrowserProfileStore::tenant_dir`'s
/// own `create_dir_all` before this is ever reached), so the only failure
/// mode `resolve_child_directory` can realistically produce here is a
/// genuine containment violation — surfaced as the more specific,
/// profile-scoped `ProfilePathViolation` rather than a generic `Platform`
/// error, satisfying the "do not collapse every profile failure into
/// `Platform`" requirement.
pub fn resolve_profile_directory(
    tenant_root: &Path,
    profile_id: &BrowserProfileId,
) -> Result<PathBuf, BrowserProfileError> {
    resolve_child_directory(tenant_root, profile_id.as_str()).map_err(|_| {
        BrowserProfileError::ProfilePathViolation {
            profile_id: profile_id.clone(),
        }
    })
}

/// Resolves `<root>/<sanitize(component)>`, hardened with the same
/// canonicalize-then-verify-containment principle
/// `backend/src/kortex/engines/storage/sandbox.py::PathSandboxValidator`
/// uses (confirmed via direct source inspection during the Browser-B3
/// architecture gate — that Python validator has no Rust equivalent
/// anywhere in this crate yet; this is the first port of its algorithm).
/// `root` MUST already exist (the caller creates it ahead of time) —
/// resolving it is the one operation this function cannot recover from
/// with a fresh, empty containment check.
///
/// Two cases, handled differently and deliberately:
/// - **`root/sanitized` does not exist yet** (the common case: creating a
///   brand-new tenant/profile directory). No re-canonicalization is
///   needed or attempted: `sanitize_path_component` already guarantees
///   `sanitized` contains no path separator or `..` sequence, so joining
///   it onto an already-canonical `root` cannot itself produce a path
///   outside `root` — this holds by construction, not by re-checking a
///   path that (by definition) nothing has touched yet.
/// - **Something already exists at `root/sanitized`** (including a
///   symlink/junction/reparse point planted there ahead of time by
///   another process or user) — canonicalized and re-verified against
///   the canonical root. This is what defeats a pre-planted escape;
///   skipping it (as an earlier draft of this function did, by
///   canonicalizing a NOT-yet-existing candidate and falling back to its
///   un-canonicalized form on failure) produced a real bug on Windows:
///   `Path::canonicalize()` returns a `\\?\`-prefixed verbatim path for
///   an existing `root`, but the un-canonicalized fallback for a
///   not-yet-existing candidate never carries that prefix, so
///   `starts_with` failed even for entirely legitimate, non-malicious
///   paths — confirmed by this module's own tests before this fix.
fn resolve_child_directory(root: &Path, component: &str) -> Result<PathBuf, BrowserProfileError> {
    let sanitized = sanitize_path_component(component);
    let root_canonical = root
        .canonicalize()
        .map_err(|e| BrowserProfileError::Platform {
            message: format!("could not resolve storage root {}: {e}", root.display()),
        })?;
    let candidate = root_canonical.join(&sanitized);

    if candidate.exists() {
        let candidate_canonical =
            candidate
                .canonicalize()
                .map_err(|e| BrowserProfileError::Platform {
                    message: format!("could not resolve {}: {e}", candidate.display()),
                })?;
        if !candidate_canonical.starts_with(&root_canonical) {
            return Err(BrowserProfileError::Platform {
                message: format!(
                    "resolved path {} escapes its expected root {}",
                    candidate_canonical.display(),
                    root_canonical.display()
                ),
            });
        }
        return Ok(candidate_canonical);
    }

    Ok(candidate)
}

/// Browser-B3 (D23): interim, local, structured lifecycle audit logging.
///
/// **Why local, not backend-audited**: Browser IPC commands are plain
/// Tauri IPC with no backend hop at all (`browser_architecture.md` §2.3 —
/// that hop only exists for a future `kortex.browser.*` capability layer,
/// Browser-B5's job). Routing these events through the real
/// `AuditManager`/`UniversalAuditEntry` today would mean either adding a
/// new backend capability ahead of schedule (prematurely importing part
/// of B5's own architecture for one narrow audit-only purpose) or having
/// Rust decode/interpret the session token to authenticate a direct
/// backend call (which `token_codec.py`'s own documented principle,
/// "the Tauri/Rust layer must never evaluate business rules," rules out —
/// see the OD-B7 investigation). Local, append-only, structured (JSON
/// Lines) logging keeps this entirely within Browser-B3's own scope and
/// is explicitly superseded once Browser gains a real capability-
/// dispatcher hop.
///
/// **What is logged**: lifecycle metadata only — event kind, timestamp,
/// tenant id, profile id, result, and a short diagnostic reason where
/// applicable. Never passwords, cookies, session tokens, page content, or
/// WebView2-internal data of any kind.
///
/// **Known limitation**: this log is a single, shared, append-only file
/// under the profiles root (not split per tenant). Every entry is itself
/// tenant-labeled, and the profile *directory* hierarchy already discloses
/// tenant existence to anyone with local filesystem access to this
/// installation — sharing one log file does not meaningfully increase
/// that exposure. Documented explicitly, not silently assumed.
// The shared `Profile` prefix is intentional, not an oversight: each
// variant's `SCREAMING_SNAKE_CASE` serialization must match the exact
// required event names (`PROFILE_CREATED`, `PROFILE_OPENED`, ...)
// specified for this audit trail — dropping the prefix per clippy's
// default suggestion would silently change those literal strings.
#[allow(clippy::enum_variant_names)]
#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ProfileAuditEvent {
    ProfileCreated,
    ProfileOpened,
    ProfileClosed,
    ProfileDeleted,
    ProfileLockFailed,
    ProfileCorruptionDetected,
    ProfileRecovery,
    ProfileAccessDenied,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct AuditLogEntry<'a> {
    event: ProfileAuditEvent,
    timestamp_utc: u64,
    tenant_id: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    profile_id: Option<&'a str>,
    result: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    reason: Option<&'a str>,
}

/// Best-effort, append-only JSON Lines write to `<profiles_root>/audit.log`.
/// A logging failure (disk full, permissions) must never block the
/// profile operation it describes — this function has no `Result` return
/// for exactly that reason, matching this crate's existing convention for
/// non-essential side effects (e.g. `secure_keys.rs`'s own "a failed write
/// means the session simply won't be remembered" posture).
fn record_audit_event(
    profiles_root: &Path,
    event: ProfileAuditEvent,
    tenant_id: &str,
    profile_id: Option<&str>,
    result: &'static str,
    reason: Option<&str>,
) {
    let entry = AuditLogEntry {
        event,
        timestamp_utc: unix_now(),
        tenant_id,
        profile_id,
        result,
        reason,
    };
    let Ok(line) = serde_json::to_string(&entry) else {
        return;
    };
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(profiles_root.join("audit.log"))
    {
        use std::io::Write;
        let _ = writeln!(file, "{line}");
    }
}

fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// The full profile directory listing this store presents to a caller —
/// merges the profile's own persisted `ProfileMetadata` with its current,
/// never-persisted `ProfileAvailability` (§ this module's own doc comment
/// on why "locked"/"corrupted" are runtime-computed, not stored fields).
/// A corrupted profile has no readable `display_name`/`created_at` at
/// all — placeholder values are used, since the point of surfacing it is
/// so the frontend can offer to delete it, not to pretend its metadata
/// is intact.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProfileSummary {
    pub profile_id: BrowserProfileId,
    pub display_name: String,
    pub created_at: u64,
    pub last_opened_at: Option<u64>,
    pub availability: ProfileAvailability,
}

#[derive(Debug, Serialize, Deserialize)]
struct LockFileContents {
    pid: u32,
    acquired_at: u64,
}

/// Confirms whether `pid` still names a live process. Windows-only real
/// check (this crate's `BrowserRuntime` V1 adapter is Windows-only per
/// ADR-0019 §5) — the non-Windows fallback conservatively reports "alive"
/// so a stale lock is never auto-recovered on a platform this crate has
/// no real liveness primitive for, matching `browser_runtime.rs`'s own
/// `#[cfg(windows)]`/`#[cfg(not(windows))]` pairing convention (fail
/// closed, not fail open, when a real check isn't available).
#[cfg(windows)]
fn pid_is_alive(pid: u32) -> bool {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION};
    unsafe {
        match OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
            Ok(handle) => {
                let _ = CloseHandle(handle);
                true
            }
            Err(_) => false,
        }
    }
}

#[cfg(not(windows))]
fn pid_is_alive(_pid: u32) -> bool {
    true
}

/// Acquires the per-profile lock at `lock_path`, recovering a stale lock
/// (a dead PID, or a malformed lock file) automatically. TOCTOU-safe on
/// the common, uncontended path: `OpenOptions::create_new(true)` is a
/// single atomic OS call with no read-then-write race window at all. The
/// stale-recovery path (an existing lock file with a dead PID) uses an
/// atomic rename-over rather than a plain write, so a concurrent
/// recovery attempt from a second process can never produce a torn/
/// partial lock file. A narrow residual race remains ONLY if two
/// processes both decide the identical lock is stale in the same
/// instant (both then locally believe they hold it) — WebView2's own
/// folder-exclusivity (confirmed live in the Browser-B3 OD-B9 preflight:
/// a second environment cannot open a user-data folder already held by
/// a live WebView2 process) is the independent second layer that still
/// prevents actual concurrent access to the profile directory even in
/// that window. This lock is the primary, user-legible control (a clean
/// `ProfileLocked` error); WebView2's own behavior is defense in depth,
/// never the sole mechanism relied on.
/// `true` when this acquisition recovered a stale lock (a dead PID, or a
/// malformed lock file) — used by the caller (`open_profile`) to decide
/// whether a `PROFILE_RECOVERY` audit event applies. A re-entrant
/// acquisition (already this process's own PID) is NOT reported as a
/// recovery — it was never actually contended.
fn acquire_profile_lock(
    lock_path: &Path,
    profile_id: &BrowserProfileId,
) -> Result<bool, BrowserProfileError> {
    let my_pid = std::process::id();
    let contents = LockFileContents {
        pid: my_pid,
        acquired_at: unix_now(),
    };
    let json = serde_json::to_vec(&contents).expect("LockFileContents always serializes");

    match std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(lock_path)
    {
        Ok(mut file) => {
            use std::io::Write;
            file.write_all(&json)
                .map_err(|e| BrowserProfileError::Platform {
                    message: e.to_string(),
                })?;
            return Ok(false);
        }
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(e) => {
            return Err(BrowserProfileError::Platform {
                message: e.to_string(),
            })
        }
    }

    let existing_pid = std::fs::read(lock_path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<LockFileContents>(&bytes).ok())
        .map(|c| c.pid);

    let is_reentrant = existing_pid == Some(my_pid);
    let is_stale = match existing_pid {
        // Already ours (re-entrant open, e.g. after a crash-free restart
        // that somehow left it) counts as recoverable too, not contended.
        Some(pid) => pid == my_pid || !pid_is_alive(pid),
        None => true,
    };

    if !is_stale {
        return Err(BrowserProfileError::ProfileLocked {
            profile_id: profile_id.clone(),
        });
    }

    let tmp_path = lock_path.with_file_name(format!(
        "{}.tmp-{my_pid}",
        lock_path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(".lock")
    ));
    std::fs::write(&tmp_path, &json).map_err(|e| BrowserProfileError::Platform {
        message: e.to_string(),
    })?;
    std::fs::rename(&tmp_path, lock_path).map_err(|e| BrowserProfileError::Platform {
        message: e.to_string(),
    })?;
    Ok(!is_reentrant)
}

fn release_profile_lock(lock_path: &Path) {
    // "Already gone" is an acceptable outcome here too — the exact
    // reasoning `browser_runtime.rs`'s own `destroy` already cites for
    // `KeyringTokenStore::clear`'s precedent.
    let _ = std::fs::remove_file(lock_path);
}

/// `true` if a LIVE process currently holds this profile's lock — used
/// by `list_profiles` to report `ProfileAvailability::Locked` without
/// itself acquiring or disturbing the lock.
fn is_locked_by_a_live_process(lock_path: &Path) -> bool {
    std::fs::read(lock_path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<LockFileContents>(&bytes).ok())
        .map(|c| pid_is_alive(c.pid))
        .unwrap_or(false)
}

fn read_metadata(
    path: &Path,
    profile_id: &BrowserProfileId,
) -> Result<ProfileMetadata, BrowserProfileError> {
    let bytes = std::fs::read(path).map_err(|e| BrowserProfileError::ProfileCorrupted {
        profile_id: profile_id.clone(),
        reason: format!("could not read profile.json: {e}"),
    })?;
    serde_json::from_slice(&bytes).map_err(|e| BrowserProfileError::ProfileCorrupted {
        profile_id: profile_id.clone(),
        reason: format!("profile.json is malformed: {e}"),
    })
}

fn write_metadata_atomically(
    path: &Path,
    metadata: &ProfileMetadata,
) -> Result<(), BrowserProfileError> {
    let json = serde_json::to_vec_pretty(metadata).map_err(|e| BrowserProfileError::Platform {
        message: format!("failed to serialize profile metadata: {e}"),
    })?;
    let tmp_path = path.with_file_name(format!(
        "{}.tmp-{}",
        path.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("profile.json"),
        std::process::id()
    ));
    std::fs::write(&tmp_path, &json).map_err(|e| {
        BrowserProfileError::ProfileStorageUnavailable {
            message: format!("could not write {}: {e}", tmp_path.display()),
        }
    })?;
    std::fs::rename(&tmp_path, path).map_err(|e| BrowserProfileError::ProfileStorageUnavailable {
        message: format!("could not finalize {}: {e}", path.display()),
    })
}

const LEGACY_QUARANTINE_README: &str =
    "This directory holds a browser profile directory created by \
KORTEX Browser-B1/B2, before Browser-B3 introduced per-tenant profile \
isolation. It has been moved here automatically and left untouched: it \
was not possible to determine which KORTEX tenant it belonged to (no \
tenant concept existed when it was created), so it was never \
reassigned to any tenant's profile directory. It is safe to delete this \
directory manually if you no longer need any browsing data from before \
this KORTEX version. Nothing in KORTEX reads from this location.\n";

/// Browser-B3 (§10): restricts `dir`'s ACL to the current OS user only —
/// baseline OS-user isolation (the architecture gate's own explicit
/// scope: full AppContainer-SID isolation, matching `python_exec/
/// windows_boundary.py`'s stronger model, is NOT required for B3 and may
/// be added later if this risk class ever warrants it).
///
/// Shells out to `icacls.exe` (a standard, pre-installed Windows tool)
/// rather than hand-rolling raw SID/DACL construction via `windows-sys`'s
/// `Win32_Security` bindings (already a direct dependency, used elsewhere
/// in this crate for Job Objects): a baseline requirement does not
/// justify the risk that a subtly-wrong unsafe ACL construction silently
/// produces an INSECURE result, which would be worse than this simpler,
/// battle-tested mechanism. `/inheritance:r` removes inherited
/// permissions first (an inherited, more permissive ACL is never
/// silently retained), then `/grant:r` grants full control to exactly
/// the current user, REPLACING any existing explicit grant rather than
/// appending to it.
///
/// Fails closed by design: the caller (`create_profile`) treats any
/// failure here as fatal to the whole operation and removes the
/// just-created directory — an unprotected profile directory must never
/// be left behind, per the "ACL failure → fail closed, never continue
/// with an unprotected profile directory" requirement.
/// `username` is a parameter (not read internally via `std::env::var`)
/// specifically so this function is directly, deterministically testable
/// against a known-bad principal without mutating the process-global
/// `USERNAME` environment variable — which, under `cargo test`'s default
/// parallel execution, could otherwise race every other concurrently-
/// running test that also calls `create_profile`.
#[cfg(windows)]
fn restrict_to_current_user(dir: &Path, username: &str) -> Result<(), BrowserProfileError> {
    let output = std::process::Command::new("icacls")
        .arg(dir)
        .arg("/inheritance:r")
        .arg("/grant:r")
        .arg(format!("{username}:(OI)(CI)F"))
        .output()
        .map_err(|e| BrowserProfileError::Platform {
            message: format!("failed to run icacls: {e}"),
        })?;
    if !output.status.success() {
        return Err(BrowserProfileError::Platform {
            message: format!(
                "icacls failed to restrict {}: {}",
                dir.display(),
                String::from_utf8_lossy(&output.stderr).trim()
            ),
        });
    }
    Ok(())
}

/// No non-Windows `BrowserRuntime` adapter exists yet (ADR-0019 §5), so
/// there is no non-Windows profile directory to protect yet either — a
/// no-op, not a silently-skipped requirement, matching this crate's
/// established `#[cfg(windows)]`/`#[cfg(not(windows))]` pairing
/// convention.
#[cfg(not(windows))]
fn restrict_to_current_user(_dir: &Path, _username: &str) -> Result<(), BrowserProfileError> {
    Ok(())
}

/// Moves `<profiles_root>/default` (Browser-B1/B2's single, non-tenant-
/// scoped profile directory) to `<profiles_root>/_legacy-quarantine/
/// default-pre-b3-<unix-timestamp>/`, if it exists — never deletes it,
/// never parses its contents, never assigns it to any tenant. Idempotent
/// by construction: once moved, `default` no longer exists, so every
/// subsequent call is a no-op. A failed move (e.g. a transient
/// permissions issue) leaves the legacy directory exactly where it was
/// — every B3 code path only ever looks under `<tenant_id>/`, so an
/// un-quarantined legacy directory is simply invisible to B3, never
/// mistaken for a tenant's profile — and this function tries again on
/// the next call.
fn quarantine_legacy_default_profile(profiles_root: &Path) {
    let legacy = profiles_root.join(LEGACY_DEFAULT_PROFILE_DIRNAME);
    if !legacy.is_dir() {
        return;
    }
    let quarantine_root = profiles_root.join(LEGACY_QUARANTINE_DIRNAME);
    if std::fs::create_dir_all(&quarantine_root).is_err() {
        return;
    }
    let destination = quarantine_root.join(format!("default-pre-b3-{}", unix_now()));
    if std::fs::rename(&legacy, &destination).is_ok() {
        let _ = std::fs::write(destination.join("README.txt"), LEGACY_QUARANTINE_README);
    }
}

/// Persistent, tenant-scoped browser profile storage. See this module's
/// own doc comment for the boundary with `browser_runtime::BrowserRuntime`
/// (surfaces) and the opaque-WebView2-data boundary.
pub struct BrowserProfileStore {
    profiles_root: PathBuf,
}

impl BrowserProfileStore {
    /// `profiles_root` is `browser_profile_store::browser_profiles_root(
    /// app_data_dir)`. Creates it if missing, and performs the legacy-
    /// default quarantine check (a one-time, idempotent action per
    /// installation).
    pub fn new(profiles_root: PathBuf) -> Result<Self, BrowserProfileError> {
        std::fs::create_dir_all(&profiles_root).map_err(|e| {
            BrowserProfileError::ProfileStorageUnavailable {
                message: format!(
                    "could not create browser profiles root {}: {e}",
                    profiles_root.display()
                ),
            }
        })?;
        quarantine_legacy_default_profile(&profiles_root);
        Ok(Self { profiles_root })
    }

    fn tenant_dir(&self, tenant_id: &str) -> Result<PathBuf, BrowserProfileError> {
        let dir = resolve_tenant_directory(&self.profiles_root, tenant_id)?;
        std::fs::create_dir_all(&dir).map_err(|e| {
            BrowserProfileError::ProfileStorageUnavailable {
                message: format!("could not create tenant directory {}: {e}", dir.display()),
            }
        })?;
        Ok(dir)
    }

    fn profile_dir(
        &self,
        tenant_id: &str,
        profile_id: &BrowserProfileId,
    ) -> Result<PathBuf, BrowserProfileError> {
        let tenant_dir = self.tenant_dir(tenant_id)?;
        resolve_profile_directory(&tenant_dir, profile_id)
    }

    fn metadata_path(
        &self,
        tenant_id: &str,
        profile_id: &BrowserProfileId,
    ) -> Result<PathBuf, BrowserProfileError> {
        Ok(self
            .profile_dir(tenant_id, profile_id)?
            .join("profile.json"))
    }

    fn lock_path(
        &self,
        tenant_id: &str,
        profile_id: &BrowserProfileId,
    ) -> Result<PathBuf, BrowserProfileError> {
        Ok(self.profile_dir(tenant_id, profile_id)?.join(".lock"))
    }

    /// The directory Browser-B3 hands to `browser_runtime::BrowserRuntime`
    /// as the WebView2 `data_directory` — opaque to this store beyond its
    /// existence; never parsed, never read.
    pub fn webview2_data_directory(
        &self,
        tenant_id: &str,
        profile_id: &BrowserProfileId,
    ) -> Result<PathBuf, BrowserProfileError> {
        Ok(self
            .profile_dir(tenant_id, profile_id)?
            .join("webview2-data"))
    }

    /// Audits a `ProfileIdentityUnavailable` rejection (§ D23/OD-B7) — the
    /// one command-layer failure this store's own methods can never see
    /// directly, since it happens BEFORE a `tenant_id` is even resolved.
    /// `tenant_id` is deliberately not part of this event: there is no
    /// authoritative one to attribute it to, and fabricating one would
    /// violate the same principle OD-B7 exists to uphold.
    pub fn record_access_denied(&self, reason: &str) {
        record_audit_event(
            &self.profiles_root,
            ProfileAuditEvent::ProfileAccessDenied,
            "(unresolved)",
            None,
            "failure",
            Some(reason),
        );
    }

    pub fn create_profile(
        &self,
        tenant_id: &str,
        display_name: &str,
    ) -> Result<BrowserProfileId, BrowserProfileError> {
        let profile_id = BrowserProfileId::generate();
        let dir = self.profile_dir(tenant_id, &profile_id)?;
        // Belt-and-suspenders, not load-bearing: `BrowserProfileId::
        // generate()`'s 128 bits of CSPRNG randomness makes a real
        // collision astronomically unreachable in practice — the same
        // "unreachable in practice, still checked" precedent
        // `browser_runtime.rs`'s own `create_surface` established for
        // `BrowserSurfaceId`. Without this check, a hypothetical collision
        // would silently reuse (and corrupt) an existing, unrelated
        // profile's directory instead of failing loudly.
        if dir.exists() {
            return Err(BrowserProfileError::AlreadyExists { profile_id });
        }
        // Resolved BEFORE creating anything: if the current user can't
        // even be determined, there is no point creating a directory this
        // process could not then protect.
        let username = std::env::var("USERNAME").map_err(|_| BrowserProfileError::Platform {
            message: "could not determine the current Windows username (USERNAME env var unset)"
                .to_string(),
        })?;
        std::fs::create_dir_all(&dir).map_err(|e| {
            BrowserProfileError::ProfileStorageUnavailable {
                message: format!("could not create profile directory {}: {e}", dir.display()),
            }
        })?;
        // Fail closed (§10): an ACL failure must never leave an
        // unprotected profile directory behind — remove what was just
        // created rather than continue with it.
        if let Err(err) = restrict_to_current_user(&dir, &username) {
            let _ = std::fs::remove_dir_all(&dir);
            return Err(err);
        }
        let metadata = ProfileMetadata {
            profile_id: profile_id.clone(),
            display_name: display_name.to_string(),
            created_at: unix_now(),
            last_opened_at: None,
            schema_version: CURRENT_PROFILE_SCHEMA_VERSION,
            state: ProfileRecordState::Active,
        };
        write_metadata_atomically(&dir.join("profile.json"), &metadata)?;
        record_audit_event(
            &self.profiles_root,
            ProfileAuditEvent::ProfileCreated,
            tenant_id,
            Some(profile_id.as_str()),
            "success",
            None,
        );
        Ok(profile_id)
    }

    pub fn rename_profile(
        &self,
        tenant_id: &str,
        profile_id: &BrowserProfileId,
        new_display_name: &str,
    ) -> Result<(), BrowserProfileError> {
        let path = self.metadata_path(tenant_id, profile_id)?;
        let mut metadata = read_metadata(&path, profile_id)?;
        metadata.display_name = new_display_name.to_string();
        write_metadata_atomically(&path, &metadata)
    }

    /// Acquires the profile's lock and returns its WebView2 data
    /// directory — the caller (a Tauri command, then
    /// `browser_runtime::BrowserRuntime::create_surface`) uses the
    /// returned path exactly as B1/B2 already use a `data_directory`;
    /// this store has no knowledge of surfaces/webviews at all.
    pub fn open_profile(
        &self,
        tenant_id: &str,
        profile_id: &BrowserProfileId,
    ) -> Result<PathBuf, BrowserProfileError> {
        let dir = self.profile_dir(tenant_id, profile_id)?;
        let metadata_path = dir.join("profile.json");
        if !metadata_path.exists() {
            return Err(BrowserProfileError::ProfileNotFound {
                profile_id: profile_id.clone(),
            });
        }
        let mut metadata = match read_metadata(&metadata_path, profile_id) {
            Ok(metadata) => metadata,
            Err(BrowserProfileError::ProfileCorrupted { reason, .. }) => {
                record_audit_event(
                    &self.profiles_root,
                    ProfileAuditEvent::ProfileCorruptionDetected,
                    tenant_id,
                    Some(profile_id.as_str()),
                    "failure",
                    Some(&reason),
                );
                return Err(BrowserProfileError::ProfileCorrupted {
                    profile_id: profile_id.clone(),
                    reason,
                });
            }
            Err(other) => return Err(other),
        };
        if metadata.state != ProfileRecordState::Active {
            return Err(BrowserProfileError::ProfileNotFound {
                profile_id: profile_id.clone(),
            });
        }

        let recovered_stale = match acquire_profile_lock(&dir.join(".lock"), profile_id) {
            Ok(recovered) => recovered,
            Err(err) => {
                record_audit_event(
                    &self.profiles_root,
                    ProfileAuditEvent::ProfileLockFailed,
                    tenant_id,
                    Some(profile_id.as_str()),
                    "failure",
                    None,
                );
                return Err(err);
            }
        };
        if recovered_stale {
            record_audit_event(
                &self.profiles_root,
                ProfileAuditEvent::ProfileRecovery,
                tenant_id,
                Some(profile_id.as_str()),
                "success",
                Some("stale lock (dead pid or malformed lock file) recovered"),
            );
        }

        metadata.last_opened_at = Some(unix_now());
        // A failure to record `last_opened_at` must not fail the open
        // itself — the lock is already held and the caller is already
        // committed to using this profile; this is presentation metadata
        // only, never load-bearing for correctness or security.
        let _ = write_metadata_atomically(&metadata_path, &metadata);

        record_audit_event(
            &self.profiles_root,
            ProfileAuditEvent::ProfileOpened,
            tenant_id,
            Some(profile_id.as_str()),
            "success",
            None,
        );
        self.webview2_data_directory(tenant_id, profile_id)
    }

    pub fn close_profile(
        &self,
        tenant_id: &str,
        profile_id: &BrowserProfileId,
    ) -> Result<(), BrowserProfileError> {
        let lock_path = self.lock_path(tenant_id, profile_id)?;
        release_profile_lock(&lock_path);
        record_audit_event(
            &self.profiles_root,
            ProfileAuditEvent::ProfileClosed,
            tenant_id,
            Some(profile_id.as_str()),
            "success",
            None,
        );
        Ok(())
    }

    /// Removes `profile_id` from what `list_profiles` will ever show
    /// again, then attempts (best-effort) to physically remove its
    /// directory. Refuses while a live process holds the profile's lock
    /// — deletion must never race an open surface out from under it.
    pub fn delete_profile(
        &self,
        tenant_id: &str,
        profile_id: &BrowserProfileId,
    ) -> Result<(), BrowserProfileError> {
        let dir = self.profile_dir(tenant_id, profile_id)?;
        if !dir.is_dir() {
            return Err(BrowserProfileError::ProfileNotFound {
                profile_id: profile_id.clone(),
            });
        }
        if is_locked_by_a_live_process(&dir.join(".lock")) {
            return Err(BrowserProfileError::ProfileLocked {
                profile_id: profile_id.clone(),
            });
        }

        // Mark PendingDeletion FIRST (best-effort — a profile.json that's
        // already corrupted can't be updated, and that's fine: an
        // unreadable profile.json already excludes it from
        // `list_profiles`'s output today). A partial/failed physical
        // delete below must never leave this profile listed as `Active`
        // again.
        let metadata_path = dir.join("profile.json");
        if let Ok(mut metadata) = read_metadata(&metadata_path, profile_id) {
            metadata.state = ProfileRecordState::PendingDeletion;
            let _ = write_metadata_atomically(&metadata_path, &metadata);
        }

        // Rename-then-remove: even if the recursive remove below fails
        // partway (a file locked by an antivirus scan, a handle briefly
        // still open), the profile is already out of its expected
        // `<tenant>/<profile_id>` location, so `list_profiles`'s
        // directory scan will never see it again regardless of the
        // physical-delete outcome — a future cleanup pass can retry.
        let quarantined_name = format!(
            "{}-deleted-{}",
            dir.file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("profile"),
            unix_now()
        );
        let removal_target = match std::fs::rename(&dir, dir.with_file_name(&quarantined_name)) {
            Ok(()) => dir.with_file_name(&quarantined_name),
            Err(_) => dir,
        };
        let _ = std::fs::remove_dir_all(&removal_target);
        record_audit_event(
            &self.profiles_root,
            ProfileAuditEvent::ProfileDeleted,
            tenant_id,
            Some(profile_id.as_str()),
            "success",
            None,
        );
        Ok(())
    }

    /// Every non-deleted profile for `tenant_id`, each merged with its
    /// current (never-persisted) availability. A profile whose
    /// `profile.json` fails to parse is still listed — as `Corrupted` —
    /// rather than silently vanishing from the user's view.
    pub fn list_profiles(
        &self,
        tenant_id: &str,
    ) -> Result<Vec<ProfileSummary>, BrowserProfileError> {
        let tenant_dir = self.tenant_dir(tenant_id)?;
        let entries = std::fs::read_dir(&tenant_dir).map_err(|e| {
            BrowserProfileError::ProfileStorageUnavailable {
                message: format!(
                    "could not read tenant directory {}: {e}",
                    tenant_dir.display()
                ),
            }
        })?;

        let mut summaries = Vec::new();
        for entry in entries.flatten() {
            if !entry.path().is_dir() {
                continue;
            }
            let Some(profile_id) = BrowserProfileId::from_directory_name(&entry.file_name()) else {
                continue; // not a recognized profile directory (e.g. a `-deleted-*` remnant)
            };
            let metadata_path = entry.path().join("profile.json");
            match read_metadata(&metadata_path, &profile_id) {
                Ok(metadata) if metadata.state == ProfileRecordState::Active => {
                    let availability = if is_locked_by_a_live_process(&entry.path().join(".lock")) {
                        ProfileAvailability::Locked
                    } else {
                        ProfileAvailability::Available
                    };
                    summaries.push(ProfileSummary {
                        profile_id: metadata.profile_id,
                        display_name: metadata.display_name,
                        created_at: metadata.created_at,
                        last_opened_at: metadata.last_opened_at,
                        availability,
                    });
                }
                Ok(_) => { /* PendingDeletion -- never listed again */ }
                Err(BrowserProfileError::ProfileCorrupted { reason, .. }) => {
                    record_audit_event(
                        &self.profiles_root,
                        ProfileAuditEvent::ProfileCorruptionDetected,
                        tenant_id,
                        Some(profile_id.as_str()),
                        "failure",
                        Some(&reason),
                    );
                    summaries.push(ProfileSummary {
                        profile_id,
                        display_name: "(corrupted profile)".to_string(),
                        created_at: 0,
                        last_opened_at: None,
                        availability: ProfileAvailability::Corrupted { reason },
                    });
                }
                Err(_) => { /* unreachable: read_metadata only ever returns ProfileCorrupted */ }
            }
        }
        Ok(summaries)
    }
}

/// Tauri-managed app state wrapping the single, process-wide
/// [`BrowserProfileStore`] — mirrors `browser_runtime::BrowserRuntimeState`'s
/// own convention.
pub struct BrowserProfileStoreState(pub Arc<BrowserProfileStore>);

/// Tracks which live `browser_runtime::BrowserSurfaceId` is using which
/// tenant-scoped profile, purely so its profile's lock can be released when
/// the surface is destroyed. Lives HERE, not inside `WebView2RuntimeAdapter`
/// — `BrowserRuntime`'s own documented boundary is surface lifecycle only,
/// with no concept of tenants or profiles at all (see that module's doc
/// comment); this is glue state the Tauri command layer
/// (`browser_create_surface`/`browser_destroy`) maintains between the two
/// otherwise-independent subsystems.
#[derive(Default)]
pub struct ActiveProfileSurfaces {
    bindings: Mutex<
        std::collections::HashMap<
            crate::browser_runtime::BrowserSurfaceId,
            (String, BrowserProfileId),
        >,
    >,
}

impl ActiveProfileSurfaces {
    pub fn record(
        &self,
        surface_id: crate::browser_runtime::BrowserSurfaceId,
        tenant_id: String,
        profile_id: BrowserProfileId,
    ) {
        self.bindings
            .lock()
            .unwrap()
            .insert(surface_id, (tenant_id, profile_id));
    }

    /// Removes and returns the binding, if any — `browser_destroy` uses
    /// this to know which profile to `close_profile` after the surface
    /// itself is gone. A surface with no recorded binding simply has
    /// nothing to release (never panics).
    pub fn take(
        &self,
        surface_id: &crate::browser_runtime::BrowserSurfaceId,
    ) -> Option<(String, BrowserProfileId)> {
        self.bindings.lock().unwrap().remove(surface_id)
    }

    /// Every currently-tracked binding, removing them all — used by the
    /// app-shutdown handlers (`lib.rs`'s `CloseRequested`/`ExitRequested`)
    /// to release every held profile lock alongside
    /// `BrowserRuntime::destroy_all`'s own existing "no orphaned browser
    /// runtime" guarantee.
    pub fn take_all(&self) -> Vec<(String, BrowserProfileId)> {
        self.bindings
            .lock()
            .unwrap()
            .drain()
            .map(|(_, v)| v)
            .collect()
    }
}

/// Resolves the authoritative tenant identity (OD-B7) or audits and denies
/// — the one shared step every Browser-B3 command below (and
/// `browser_runtime::browser_create_surface`) performs before touching
/// the store. `action` is the command name, recorded only as a
/// diagnostic `reason` string, never as the (nonexistent) tenant id.
pub fn resolve_tenant_or_deny(
    ipc_state: &crate::ipc::IpcClientState,
    store: &BrowserProfileStore,
    action: &str,
) -> Result<String, BrowserProfileError> {
    ipc_state.current_tenant_id().ok_or_else(|| {
        store.record_access_denied(action);
        BrowserProfileError::ProfileIdentityUnavailable
    })
}

/// Browser-B3 profile-lifecycle commands. Same transport as
/// `browser_runtime.rs`'s own commands (plain Tauri IPC, no backend hop —
/// there is no capability/governance layer for Browser yet). Every command
/// resolves the authoritative tenant identity itself
/// (`IpcClientState::current_tenant_id()`, OD-B7) — the frontend supplies
/// only a `BrowserProfileId`/`display_name`, never a tenant identifier.
#[tauri::command]
pub async fn browser_list_profiles(
    state: tauri::State<'_, BrowserProfileStoreState>,
    ipc_state: tauri::State<'_, Arc<crate::ipc::IpcClientState>>,
) -> Result<Vec<ProfileSummary>, BrowserProfileError> {
    let tenant_id = resolve_tenant_or_deny(&ipc_state, &state.0, "browser_list_profiles")?;
    state.0.list_profiles(&tenant_id)
}

#[tauri::command]
pub async fn browser_create_profile(
    state: tauri::State<'_, BrowserProfileStoreState>,
    ipc_state: tauri::State<'_, Arc<crate::ipc::IpcClientState>>,
    display_name: String,
) -> Result<BrowserProfileId, BrowserProfileError> {
    let tenant_id = resolve_tenant_or_deny(&ipc_state, &state.0, "browser_create_profile")?;
    state.0.create_profile(&tenant_id, &display_name)
}

#[tauri::command]
pub async fn browser_rename_profile(
    state: tauri::State<'_, BrowserProfileStoreState>,
    ipc_state: tauri::State<'_, Arc<crate::ipc::IpcClientState>>,
    profile_id: BrowserProfileId,
    display_name: String,
) -> Result<(), BrowserProfileError> {
    let tenant_id = resolve_tenant_or_deny(&ipc_state, &state.0, "browser_rename_profile")?;
    state
        .0
        .rename_profile(&tenant_id, &profile_id, &display_name)
}

/// Refuses (via the store's own `ProfileLocked` check) while a live
/// process holds the profile open — never races an open surface out from
/// under it.
#[tauri::command]
pub async fn browser_delete_profile(
    state: tauri::State<'_, BrowserProfileStoreState>,
    ipc_state: tauri::State<'_, Arc<crate::ipc::IpcClientState>>,
    profile_id: BrowserProfileId,
) -> Result<(), BrowserProfileError> {
    let tenant_id = resolve_tenant_or_deny(&ipc_state, &state.0, "browser_delete_profile")?;
    state.0.delete_profile(&tenant_id, &profile_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn browser_profile_id_generate_never_collides_across_many_calls() {
        let ids: Vec<BrowserProfileId> =
            (0..10_000).map(|_| BrowserProfileId::generate()).collect();
        let unique: std::collections::HashSet<_> = ids.iter().collect();
        assert_eq!(
            unique.len(),
            ids.len(),
            "every generated profile id must be distinct"
        );
    }

    #[test]
    fn browser_profile_id_generate_has_sufficient_length_for_128_bits_of_entropy() {
        let id = BrowserProfileId::generate();
        // "profile-" (8 chars) + 32 hex chars (16 bytes * 2).
        assert_eq!(id.as_str().len(), 8 + 32);
        assert!(id.as_str().starts_with("profile-"));
    }

    #[test]
    fn browser_profile_id_round_trips_through_json() {
        let id = BrowserProfileId::generate();
        let json = serde_json::to_string(&id).unwrap();
        let round_tripped: BrowserProfileId = serde_json::from_str(&json).unwrap();
        assert_eq!(id, round_tripped);
    }

    #[test]
    fn profile_metadata_serializes_in_camel_case() {
        let metadata = ProfileMetadata {
            profile_id: BrowserProfileId::from_raw_for_test("profile-abc"),
            display_name: "Work".to_string(),
            created_at: 1_700_000_000,
            last_opened_at: None,
            schema_version: CURRENT_PROFILE_SCHEMA_VERSION,
            state: ProfileRecordState::Active,
        };
        let json = serde_json::to_value(&metadata).unwrap();
        assert_eq!(json["profileId"], "profile-abc");
        assert_eq!(json["displayName"], "Work");
        assert_eq!(json["createdAt"], 1_700_000_000);
        assert!(json["lastOpenedAt"].is_null());
        assert_eq!(json["schemaVersion"], 1);
        assert_eq!(json["state"], "active");
    }

    #[test]
    fn browser_profile_error_serializes_with_its_kind_tag() {
        let error = BrowserProfileError::ProfileIdentityUnavailable;
        let json = serde_json::to_value(&error).unwrap();
        assert_eq!(json["kind"], "profileIdentityUnavailable");

        let error = BrowserProfileError::ProfileLocked {
            profile_id: BrowserProfileId::from_raw_for_test("profile-xyz"),
        };
        let json = serde_json::to_value(&error).unwrap();
        assert_eq!(json["kind"], "profileLocked");
        // `#[serde(rename_all = "camelCase")]` on the enum does NOT
        // cascade into a struct-variant's own fields (see this type's
        // own doc comment) -- proves the explicit per-field
        // `#[serde(rename = "profileId")]` actually takes effect.
        assert_eq!(json["profileId"], "profile-xyz");
        assert!(
            json.get("profile_id").is_none(),
            "must not ALSO emit the raw snake_case key"
        );
    }

    /// Adversarial (§21 D5): a symlink/junction already planted at the
    /// exact path a legitimate tenant/profile directory would resolve to,
    /// pointing OUTSIDE the expected root. Requires the OS to let this
    /// process create a directory symlink (Developer Mode or admin on
    /// Windows) -- skips the assertion (rather than failing the whole
    /// suite) on a machine/CI runner without that privilege, since the
    /// thing under test here is `resolve_child_directory`'s own logic,
    /// not whether this environment can create symlinks at all.
    #[test]
    fn resolve_child_directory_rejects_a_preplanted_symlink_escaping_the_root() {
        let temp =
            std::env::temp_dir().join(format!("kortex-b3-test-{}", BrowserProfileId::generate()));
        let root = temp.join("browser-profiles");
        let outside = temp.join("outside-target");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::create_dir_all(&outside).unwrap();

        let escape_point = root.join(sanitize_path_component("tenant-evil"));
        #[cfg(windows)]
        let symlink_result = std::os::windows::fs::symlink_dir(&outside, &escape_point);
        #[cfg(not(windows))]
        let symlink_result = std::os::unix::fs::symlink(&outside, &escape_point);

        if symlink_result.is_ok() {
            let result = resolve_tenant_directory(&root, "tenant-evil");
            assert!(
                result.is_err(),
                "a pre-planted symlink escaping the root must be rejected, not silently followed"
            );
        }
        // else: this environment cannot create symlinks without elevated
        // privilege -- not something this test can exercise here.

        std::fs::remove_dir_all(&temp).ok();
    }

    #[test]
    fn resolve_child_directory_is_idempotent_when_the_directory_already_exists() {
        let temp =
            std::env::temp_dir().join(format!("kortex-b3-test-{}", BrowserProfileId::generate()));
        let root = temp.join("browser-profiles");
        std::fs::create_dir_all(&root).unwrap();

        let first = resolve_tenant_directory(&root, "tenant-1").unwrap();
        std::fs::create_dir_all(&first).unwrap();
        let second = resolve_tenant_directory(&root, "tenant-1").unwrap();

        assert_eq!(first.canonicalize().unwrap(), second);

        std::fs::remove_dir_all(&temp).ok();
    }

    #[test]
    fn sanitize_path_component_strips_traversal_and_separators() {
        assert_eq!(sanitize_path_component("tenant-42_abc"), "tenant-42_abc");
        assert_eq!(sanitize_path_component("../../etc/passwd"), "etcpasswd");
        assert_eq!(sanitize_path_component(""), "invalid");
        assert_eq!(sanitize_path_component("../"), "invalid");
        assert_eq!(
            sanitize_path_component("C:\\Windows\\System32"),
            "CWindowsSystem32"
        );
    }

    #[test]
    fn resolve_tenant_directory_stays_within_the_root_for_a_normal_tenant_id() {
        let temp =
            std::env::temp_dir().join(format!("kortex-b3-test-{}", BrowserProfileId::generate()));
        let root = temp.join("browser-profiles");
        std::fs::create_dir_all(&root).unwrap();
        let root_canonical = root.canonicalize().unwrap();

        let resolved = resolve_tenant_directory(&root, "tenant-1").unwrap();
        assert_eq!(resolved, root_canonical.join("tenant-1"));
        assert!(resolved.starts_with(&root_canonical));

        std::fs::remove_dir_all(&temp).ok();
    }

    #[test]
    fn resolve_tenant_directory_sanitizes_traversal_attempts_in_the_tenant_id() {
        let temp =
            std::env::temp_dir().join(format!("kortex-b3-test-{}", BrowserProfileId::generate()));
        let root = temp.join("browser-profiles");
        std::fs::create_dir_all(&root).unwrap();
        let root_canonical = root.canonicalize().unwrap();

        // Even though `sanitize_path_component` already strips `../`, this
        // proves the resolved path is still contained even when the input
        // is adversarial — the sanitizer and the containment check are
        // independent, defense-in-depth layers.
        let resolved = resolve_tenant_directory(&root, "../../../windows/system32").unwrap();
        assert!(resolved.starts_with(&root_canonical));
        assert_eq!(resolved, root_canonical.join("windowssystem32"));

        std::fs::remove_dir_all(&temp).ok();
    }

    #[test]
    fn resolve_profile_directory_keeps_distinct_profiles_in_distinct_directories() {
        let temp =
            std::env::temp_dir().join(format!("kortex-b3-test-{}", BrowserProfileId::generate()));
        std::fs::create_dir_all(&temp).unwrap();
        let temp_canonical = temp.canonicalize().unwrap();
        let a = resolve_profile_directory(&temp, &BrowserProfileId::from_raw_for_test("profile-a"))
            .unwrap();
        let b = resolve_profile_directory(&temp, &BrowserProfileId::from_raw_for_test("profile-b"))
            .unwrap();
        assert_ne!(a, b);
        assert!(a.starts_with(&temp_canonical));
        assert!(b.starts_with(&temp_canonical));

        std::fs::remove_dir_all(&temp).ok();
    }

    #[test]
    fn browser_profiles_root_is_a_dedicated_subdirectory_of_the_app_data_dir() {
        let app_data_dir = PathBuf::from("C:/fake/app-data");
        let root = browser_profiles_root(&app_data_dir);
        assert_eq!(root, app_data_dir.join("browser-profiles"));
    }

    // -- BrowserProfileStore: CRUD, locking, quarantine, isolation --------

    /// A fresh, isolated `BrowserProfileStore` rooted under a unique temp
    /// directory, plus that directory for cleanup — every test gets its
    /// own root so tests can run concurrently without interfering.
    fn test_store() -> (BrowserProfileStore, PathBuf) {
        let root = std::env::temp_dir().join(format!(
            "kortex-b3-store-test-{}",
            BrowserProfileId::generate()
        ));
        let store = BrowserProfileStore::new(root.clone()).unwrap();
        (store, root)
    }

    /// Spawns and waits on a real short-lived child process, returning its
    /// PID — guaranteed dead by the time this returns, unlike a made-up
    /// large integer (which could theoretically, if astronomically
    /// unlikely, collide with something actually running).
    fn a_definitely_dead_pid() -> u32 {
        let child = std::process::Command::new("cmd")
            .args(["/C", "exit 0"])
            .spawn()
            .expect("spawning a trivial child process must succeed on Windows CI/dev machines");
        let pid = child.id();
        let mut child = child;
        child.wait().expect("the child process must exit");
        pid
    }

    /// Browser-B3 (§10): a REAL, live check — not just "the function
    /// returned Ok" — that `create_profile` actually tightened the
    /// directory's ACL, by shelling out to `icacls` a second time (read-
    /// only) and confirming inheritance was disabled and the current
    /// user's grant is present. `icacls`'s own output is locale-dependent
    /// in general, but the specific markers checked here
    /// ("(OI)(CI)(F)"/"Successfully processed") are principal/permission
    /// data, not localized prose, and this test runs on the same locale
    /// as the icacls call it verifies.
    #[test]
    fn create_profile_actually_restricts_the_directory_acl() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        let dir = store.profile_dir("tenant-1", &id).unwrap();

        let output = std::process::Command::new("icacls")
            .arg(&dir)
            .output()
            .unwrap();
        let listing = String::from_utf8_lossy(&output.stdout);
        let username = std::env::var("USERNAME").unwrap();

        assert!(
            listing.contains(&username) && listing.contains("(F)"),
            "expected the current user to hold full control on {}, got: {listing}",
            dir.display()
        );
        // `icacls <path>` alone (no /inheritance flag) doesn't echo
        // inheritance state directly, but a bare listing with exactly one
        // principal (no `BUILTIN\Users`/`Everyone`/inherited entries) is
        // itself the observable evidence `/inheritance:r` took effect.
        assert!(
            !listing.to_lowercase().contains("everyone")
                && !listing.to_lowercase().contains("\\users:"),
            "inherited broad grants must not remain after restriction, got: {listing}"
        );

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn restrict_to_current_user_fails_against_a_nonexistent_principal() {
        let temp = std::env::temp_dir().join(format!(
            "kortex-b3-acl-test-{}",
            BrowserProfileId::generate()
        ));
        std::fs::create_dir_all(&temp).unwrap();

        let result = restrict_to_current_user(&temp, "this-account-does-not-exist-kortex-b3-test");

        assert!(
            result.is_err(),
            "icacls must fail against a nonexistent principal"
        );

        std::fs::remove_dir_all(&temp).ok();
    }

    #[test]
    fn create_profile_makes_it_visible_and_available_via_list_profiles() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();

        let profiles = store.list_profiles("tenant-1").unwrap();
        assert_eq!(profiles.len(), 1);
        assert_eq!(profiles[0].profile_id, id);
        assert_eq!(profiles[0].display_name, "Work");
        assert!(matches!(
            profiles[0].availability,
            ProfileAvailability::Available
        ));

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn rename_profile_updates_the_display_name_and_nothing_else() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();

        store.rename_profile("tenant-1", &id, "Personal").unwrap();

        let profiles = store.list_profiles("tenant-1").unwrap();
        assert_eq!(profiles[0].display_name, "Personal");
        assert_eq!(profiles[0].profile_id, id);

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn rename_profile_fails_with_profile_not_found_for_an_unknown_id() {
        let (store, root) = test_store();
        let bogus = BrowserProfileId::from_raw_for_test("profile-does-not-exist");

        let result = store.rename_profile("tenant-1", &bogus, "x");

        assert!(matches!(
            result,
            Err(BrowserProfileError::ProfileCorrupted { .. })
        ));
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn open_profile_acquires_the_lock_and_close_profile_releases_it() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();

        let data_dir = store.open_profile("tenant-1", &id).unwrap();
        assert!(data_dir.ends_with("webview2-data"));

        let profiles = store.list_profiles("tenant-1").unwrap();
        assert!(matches!(
            profiles[0].availability,
            ProfileAvailability::Locked
        ));

        store.close_profile("tenant-1", &id).unwrap();
        let profiles = store.list_profiles("tenant-1").unwrap();
        assert!(matches!(
            profiles[0].availability,
            ProfileAvailability::Available
        ));

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn open_profile_records_last_opened_at() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();

        store.open_profile("tenant-1", &id).unwrap();

        let profiles = store.list_profiles("tenant-1").unwrap();
        assert!(profiles[0].last_opened_at.is_some());

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn open_profile_is_reentrant_for_the_same_process() {
        // The SAME process opening the same profile twice (e.g. a second
        // tab against the same profile) must never be rejected as
        // contended — this is exactly the existing, already-proven B2
        // multi-surface-per-profile behavior; the profile-level lock must
        // not regress it.
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();

        store.open_profile("tenant-1", &id).unwrap();
        let second = store.open_profile("tenant-1", &id);

        assert!(
            second.is_ok(),
            "opening the same profile twice from this process must not be treated as contention"
        );

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn open_profile_rejects_contention_from_a_genuinely_live_other_process() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        let lock_path = store.profile_dir("tenant-1", &id).unwrap().join(".lock");

        // A real, still-running child process this test itself spawns
        // (never PID 4/"System" -- `OpenProcess` can fail with access
        // denied against a privileged system process even though it IS
        // alive, which would make such a test pass for the wrong reason
        // -- a same-user-session child process avoids that entirely and
        // genuinely exercises the "held by another live process" branch
        // rather than the re-entrant one).
        let mut child = std::process::Command::new("cmd")
            .args(["/C", "ping -n 30 127.0.0.1 >nul"])
            .spawn()
            .expect("spawning a long-lived child process must succeed on Windows CI/dev machines");
        let fake_contents = LockFileContents {
            pid: child.id(),
            acquired_at: unix_now(),
        };
        std::fs::write(&lock_path, serde_json::to_vec(&fake_contents).unwrap()).unwrap();

        let result = store.open_profile("tenant-1", &id);

        assert!(matches!(
            result,
            Err(BrowserProfileError::ProfileLocked { .. })
        ));

        let _ = child.kill();
        let _ = child.wait();
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn open_profile_recovers_a_stale_lock_from_a_genuinely_dead_process() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        let lock_path = store.profile_dir("tenant-1", &id).unwrap().join(".lock");

        // Retried with a FRESH dead PID each attempt: Windows can recycle a
        // just-exited PID to an unrelated, genuinely-live process within a
        // short window when many short-lived child processes are being
        // spawned concurrently (this test suite's own process-spawning
        // tests run in parallel by default) -- a real, narrow race in the
        // TEST'S OWN pid selection, not in the production stale-lock-
        // recovery logic under test. A genuine logic bug would fail on
        // every attempt, since recovery doesn't depend on which specific
        // PID value was chosen -- only a PID-reuse coincidence would ever
        // make this pass on retry.
        let mut last_result = None;
        for _ in 0..5 {
            let dead_pid = a_definitely_dead_pid();
            let stale_contents = LockFileContents {
                pid: dead_pid,
                acquired_at: 0,
            };
            std::fs::write(&lock_path, serde_json::to_vec(&stale_contents).unwrap()).unwrap();
            let result = store.open_profile("tenant-1", &id);
            let succeeded = result.is_ok();
            last_result = Some(result);
            if succeeded {
                store.close_profile("tenant-1", &id).unwrap();
                break;
            }
        }

        assert!(
            matches!(last_result, Some(Ok(_))),
            "a lock held by a dead PID must be recovered, not treated as contention (even across retries)"
        );
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn open_profile_recovers_a_malformed_lock_file() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        let lock_path = store.profile_dir("tenant-1", &id).unwrap().join(".lock");
        std::fs::write(&lock_path, b"not valid json at all").unwrap();

        let result = store.open_profile("tenant-1", &id);

        assert!(
            result.is_ok(),
            "a malformed lock file must be recovered, not wedge the profile forever"
        );
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn delete_profile_refuses_while_a_live_process_holds_the_lock() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        store.open_profile("tenant-1", &id).unwrap();

        let result = store.delete_profile("tenant-1", &id);

        assert!(matches!(
            result,
            Err(BrowserProfileError::ProfileLocked { .. })
        ));
        // Must still be listed, unharmed, after the refused delete.
        let profiles = store.list_profiles("tenant-1").unwrap();
        assert_eq!(profiles.len(), 1);

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn delete_profile_removes_it_from_list_profiles_and_from_disk() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        let dir = store.profile_dir("tenant-1", &id).unwrap();
        assert!(dir.exists());

        store.delete_profile("tenant-1", &id).unwrap();

        let profiles = store.list_profiles("tenant-1").unwrap();
        assert!(profiles.is_empty());
        assert!(
            !dir.exists(),
            "the physical directory must actually be gone, not merely unlisted"
        );

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn a_profile_with_malformed_metadata_is_listed_as_corrupted_not_silently_dropped() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        let metadata_path = store.metadata_path("tenant-1", &id).unwrap();
        std::fs::write(&metadata_path, b"{ not valid json").unwrap();

        let profiles = store.list_profiles("tenant-1").unwrap();

        assert_eq!(profiles.len(), 1);
        assert!(matches!(
            profiles[0].availability,
            ProfileAvailability::Corrupted { .. }
        ));

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn profiles_created_under_one_tenant_are_invisible_to_another_tenant() {
        let (store, root) = test_store();
        store.create_profile("tenant-1", "Alice's profile").unwrap();
        store.create_profile("tenant-2", "Bob's profile").unwrap();

        let tenant_1_profiles = store.list_profiles("tenant-1").unwrap();
        let tenant_2_profiles = store.list_profiles("tenant-2").unwrap();

        assert_eq!(tenant_1_profiles.len(), 1);
        assert_eq!(tenant_1_profiles[0].display_name, "Alice's profile");
        assert_eq!(tenant_2_profiles.len(), 1);
        assert_eq!(tenant_2_profiles[0].display_name, "Bob's profile");

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn a_path_traversal_tenant_id_never_escapes_the_profiles_root() {
        let (store, root) = test_store();

        let id = store
            .create_profile("../../../windows/system32", "evil")
            .unwrap();

        let dir = store.profile_dir("../../../windows/system32", &id).unwrap();
        assert!(dir.starts_with(root.canonicalize().unwrap()));

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn legacy_default_profile_is_quarantined_not_deleted_and_the_quarantine_is_idempotent() {
        let root = std::env::temp_dir().join(format!(
            "kortex-b3-legacy-test-{}",
            BrowserProfileId::generate()
        ));
        std::fs::create_dir_all(&root).unwrap();
        let legacy_dir = root.join("default");
        std::fs::create_dir_all(&legacy_dir).unwrap();
        std::fs::write(legacy_dir.join("Cookies"), b"pretend-webview2-cookie-db").unwrap();

        let _store = BrowserProfileStore::new(root.clone()).unwrap();

        assert!(
            !legacy_dir.exists(),
            "the legacy directory must be moved out of its original location"
        );
        let quarantine_root = root.join(LEGACY_QUARANTINE_DIRNAME);
        let quarantined_entries: Vec<_> = std::fs::read_dir(&quarantine_root)
            .unwrap()
            .flatten()
            .collect();
        assert_eq!(quarantined_entries.len(), 1);
        assert!(quarantined_entries[0]
            .file_name()
            .to_string_lossy()
            .starts_with("default-pre-b3-"));
        // The original content must be intact, never parsed/modified.
        assert!(quarantined_entries[0].path().join("Cookies").exists());
        assert!(quarantined_entries[0].path().join("README.txt").exists());

        // Idempotent: constructing a SECOND store against the same root
        // (simulating a second app launch) must not error, and must not
        // create a second quarantine entry -- there is nothing left to
        // quarantine.
        let _second_store = BrowserProfileStore::new(root.clone()).unwrap();
        let quarantined_entries_after: Vec<_> = std::fs::read_dir(&quarantine_root)
            .unwrap()
            .flatten()
            .collect();
        assert_eq!(
            quarantined_entries_after.len(),
            1,
            "a second run must not quarantine anything a second time"
        );

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn a_clean_install_with_no_legacy_directory_is_a_no_op() {
        let root = std::env::temp_dir().join(format!(
            "kortex-b3-clean-test-{}",
            BrowserProfileId::generate()
        ));

        let store = BrowserProfileStore::new(root.clone()).unwrap();

        assert!(!root.join(LEGACY_QUARANTINE_DIRNAME).exists());
        assert!(store.list_profiles("tenant-1").unwrap().is_empty());

        std::fs::remove_dir_all(&root).ok();
    }

    // -- Browser-B3 (D23/B3.7): interim local structured audit logging ---

    fn read_audit_log_lines(root: &Path) -> Vec<serde_json::Value> {
        std::fs::read_to_string(root.join("audit.log"))
            .unwrap_or_default()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }

    #[test]
    fn create_profile_records_a_profile_created_event() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();

        let lines = read_audit_log_lines(&root);
        let entry = lines
            .iter()
            .find(|e| e["event"] == "PROFILE_CREATED")
            .unwrap();
        assert_eq!(entry["tenantId"], "tenant-1");
        assert_eq!(entry["profileId"], id.as_str());
        assert_eq!(entry["result"], "success");

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn open_and_close_profile_record_their_own_events() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        store.open_profile("tenant-1", &id).unwrap();
        store.close_profile("tenant-1", &id).unwrap();

        let lines = read_audit_log_lines(&root);
        assert!(lines
            .iter()
            .any(|e| e["event"] == "PROFILE_OPENED" && e["profileId"] == id.as_str()));
        assert!(lines
            .iter()
            .any(|e| e["event"] == "PROFILE_CLOSED" && e["profileId"] == id.as_str()));

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn delete_profile_records_a_profile_deleted_event() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        store.delete_profile("tenant-1", &id).unwrap();

        let lines = read_audit_log_lines(&root);
        assert!(lines
            .iter()
            .any(|e| e["event"] == "PROFILE_DELETED" && e["profileId"] == id.as_str()));

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn contention_records_a_profile_lock_failed_event() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        let lock_path = store.profile_dir("tenant-1", &id).unwrap().join(".lock");
        let mut child = std::process::Command::new("cmd")
            .args(["/C", "ping -n 30 127.0.0.1 >nul"])
            .spawn()
            .unwrap();
        std::fs::write(
            &lock_path,
            serde_json::to_vec(&LockFileContents {
                pid: child.id(),
                acquired_at: unix_now(),
            })
            .unwrap(),
        )
        .unwrap();

        let result = store.open_profile("tenant-1", &id);
        assert!(matches!(
            result,
            Err(BrowserProfileError::ProfileLocked { .. })
        ));

        let lines = read_audit_log_lines(&root);
        assert!(lines
            .iter()
            .any(|e| e["event"] == "PROFILE_LOCK_FAILED" && e["result"] == "failure"));

        let _ = child.kill();
        let _ = child.wait();
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn stale_lock_recovery_records_a_profile_recovery_event() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        let lock_path = store.profile_dir("tenant-1", &id).unwrap().join(".lock");
        std::fs::write(&lock_path, b"not valid json").unwrap();

        store.open_profile("tenant-1", &id).unwrap();

        let lines = read_audit_log_lines(&root);
        assert!(lines.iter().any(|e| e["event"] == "PROFILE_RECOVERY"));

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn corrupted_metadata_records_a_profile_corruption_detected_event() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        std::fs::write(store.metadata_path("tenant-1", &id).unwrap(), b"{ not json").unwrap();

        let _ = store.list_profiles("tenant-1").unwrap();

        let lines = read_audit_log_lines(&root);
        assert!(lines
            .iter()
            .any(|e| e["event"] == "PROFILE_CORRUPTION_DETECTED" && e["profileId"] == id.as_str()));

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn access_denied_is_recorded_without_a_fabricated_tenant_id() {
        let (store, root) = test_store();
        store.record_access_denied("browser_list_profiles");

        let lines = read_audit_log_lines(&root);
        let entry = lines
            .iter()
            .find(|e| e["event"] == "PROFILE_ACCESS_DENIED")
            .unwrap();
        // Must never claim a real tenant id it doesn't have.
        assert_eq!(entry["tenantId"], "(unresolved)");
        assert_eq!(entry["reason"], "browser_list_profiles");

        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn audit_log_entries_never_contain_secret_like_field_names() {
        let (store, root) = test_store();
        let id = store.create_profile("tenant-1", "Work").unwrap();
        store.open_profile("tenant-1", &id).unwrap();
        store.close_profile("tenant-1", &id).unwrap();
        store.rename_profile("tenant-1", &id, "Renamed").unwrap();
        store.delete_profile("tenant-1", &id).unwrap();

        let raw = std::fs::read_to_string(root.join("audit.log")).unwrap();
        for forbidden in ["password", "cookie", "token", "secret", "credential"] {
            assert!(
                !raw.to_lowercase().contains(forbidden),
                "audit log must never mention '{forbidden}'"
            );
        }

        std::fs::remove_dir_all(&root).ok();
    }
}
