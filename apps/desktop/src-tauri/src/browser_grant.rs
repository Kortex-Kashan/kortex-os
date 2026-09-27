//! Browser-B5.4: desktop-side verification and single-use tracking for a
//! backend-minted Capability Execution Grant (Browser-B5.3,
//! `backend/src/kortex/engines/browser/grant.py`).
//!
//! **What this module's pure verification functions deliberately do NOT
//! do**: invoke `BrowserRuntime`'s mutating methods directly — only
//! `execute_granted_action` (the orchestration function backing
//! `browser_execute_granted_action`, this file's own Tauri command) does,
//! and only after `mint`/`verify_signature`/`verify_expiry`/
//! `RedeemedGrantTracker`/live surface-tenant-profile-generation binding
//! have ALL already succeeded. As of Browser-B5 (navigate/screenshot
//! execution), `NAVIGATE_CAPABILITY`/`SCREENSHOT_CAPABILITY` additionally
//! recompute and verify the parameter hash (OD-01,
//! `docs/architecture/browser_b5_5_architecture_gate.md`), then reach
//! `BrowserRuntime` through the EXACT SAME trait methods
//! (`navigate`/`arm_navigation_waiter`/`capture_screenshot`) a human click
//! already goes through — so B4's `BrowserPolicyEngine` remains the final,
//! unbypassable gate regardless of anything decided in this file. Every
//! OTHER registered `kortex.browser.*` capability still stops at
//! `NotYetEnabled`, exactly as B5.0-B5.4 shipped it (see this module's own
//! `execute_granted_action` doc comment).
//!
//! **Signature payload reconstruction is byte-for-byte, not
//! datetime-parsed-and-reformatted.** `issued_at`/`expires_at` are kept as
//! the exact strings the backend sent (never round-tripped through a
//! parsed `OffsetDateTime` and re-serialized) when rebuilding the canonical
//! payload the signature covers — reformatting a parsed timestamp risks a
//! byte-for-byte mismatch against what the backend's own
//! `datetime.isoformat()` actually produced and signed, which would make
//! every otherwise-valid grant fail signature verification. `time`'s
//! RFC3339 parser is used only for the separate, semantic expiry
//! comparison, never for reconstructing the signed payload.
//!
//! **Public key trust**: verification never trusts "this arrived over an
//! authenticated connection" alone. The desktop fetches and caches the
//! backend's grant-signing public key via the ordinary, authenticated
//! `kortex.browser.grant_verification_key` capability (ordinary
//! `invoke_capability` transport — see `GrantVerificationKeyCache`) and
//! verifies every grant's signature against that cached key locally,
//! independent of transport-layer trust.

use std::collections::HashSet;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::browser_policy::DenyReason;
use crate::browser_runtime::{BrowserRuntime, BrowserSurfaceId, NavigationOutcome};
use crate::ipc::{forward_capability_request, IpcCapabilityRequest, IpcClientState};

const GRANT_VERIFICATION_KEY_CAPABILITY: &str = "kortex.browser.grant_verification_key";
/// Browser-B5 (navigate/screenshot execution): the two `capability_name`
/// values this redeem command actually executes for real. Every other
/// registered `kortex.browser.*` capability still redeems successfully
/// (every B5.0-B5.4 verification check still runs and still fails closed)
/// but returns `NotYetEnabled` — unchanged from B5.0-B5.4's own posture,
/// see this file's own module doc.
const NAVIGATE_CAPABILITY: &str = "kortex.browser.navigate";
const SCREENSHOT_CAPABILITY: &str = "kortex.browser.screenshot";
/// Mirrors `backend/src/kortex/engines/browser/engine.py`'s own
/// `_DEFAULT_INTERACTION_TIMEOUT_SECONDS`-style bound for the screenshot
/// capability specifically — `BrowserScreenshotParams` (Python) has no
/// `timeout_ms` field of its own (unlike navigate), so this is a fixed,
/// desktop-side bound rather than one derived from caller-supplied
/// parameters. Generous enough for a real capture (proven live, not just
/// assumed) while still bounded — see `capture_screenshot`'s own doc.
const SCREENSHOT_TIMEOUT: Duration = Duration::from_secs(10);

/// Mirrors `backend/src/kortex/engines/browser/models.py::BrowserCapabilityExecutionGrant`
/// field-for-field. Deserialized directly from a capability handler's
/// `{"grant": {...}}` result (`engine.py::_grant_result`).
#[derive(Debug, Clone, Deserialize)]
pub struct CapabilityExecutionGrant {
    pub grant_id: String,
    pub tenant_id: String,
    pub principal_id: String,
    pub capability_name: String,
    pub browser_profile_id: String,
    pub surface_id: String,
    pub navigation_generation: Option<u64>,
    pub canonicalized_parameters_hash: String,
    pub issued_at: String,
    pub expires_at: String,
    pub signature: String,
}

/// Browser-B5 (navigate/screenshot execution): the capability-specific
/// parameters a redeem call supplies ALONGSIDE a Grant — never carried BY
/// the Grant itself, which holds only their hash (see
/// `CapabilityExecutionGrant::canonicalized_parameters_hash`'s own doc
/// comment and `browser_b5_5_architecture_gate.md` OD-01). The explicit
/// `capability` tag is checked against `grant.capability_name` by
/// `execute_granted_action`'s own pattern match — a Screenshot-shaped
/// `params` can never be silently accepted for a Navigate grant, or vice
/// versa; supplying the wrong shape is indistinguishable from supplying
/// none at all (`BrowserGrantExecutionError::ParametersRequired`).
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "capability")]
pub enum BrowserCapabilityParamsWire {
    #[serde(rename = "kortex.browser.navigate")]
    Navigate { url: String, timeout_ms: u64 },
    #[serde(rename = "kortex.browser.screenshot")]
    Screenshot { full_page: bool },
}

/// Every way a Grant can be rejected. Deliberately as granular as B5.3's
/// own `verify_grant` docstring says a *caller-facing* API must NOT be
/// (that Python function's own `GrantVerificationResult` never exposes
/// `reason` past its own module boundary, for the same "never tell a
/// probing caller which check failed" reasoning `BrowserGrantInvalidError`
/// states) — this granularity exists for this module's own tests and logs
/// only; `lib.rs`'s redeem command collapses every variant below into the
/// single, generic `BrowserActionErrorCode::GrantInvalid`/`GrantExpired`
/// pair before anything crosses back to an AI caller.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum GrantRejectionReason {
    MalformedSignature,
    InvalidSignature,
    MalformedTimestamp,
    Expired,
    NotYetValid,
    AlreadyRedeemed,
}

/// Reconstructs the exact byte sequence
/// `backend/src/kortex/engines/browser/grant.py::_canonical_grant_payload`
/// signs — colon-joined, same field order, `navigation_generation`
/// rendered as its decimal string or empty (never `"None"`/`"null"`),
/// matching Python's own `"" if grant.navigation_generation is None else
/// str(grant.navigation_generation)` exactly.
fn canonical_payload(grant: &CapabilityExecutionGrant) -> Vec<u8> {
    let generation = grant
        .navigation_generation
        .map(|value| value.to_string())
        .unwrap_or_default();
    [
        grant.grant_id.as_str(),
        grant.tenant_id.as_str(),
        grant.principal_id.as_str(),
        grant.capability_name.as_str(),
        grant.browser_profile_id.as_str(),
        grant.surface_id.as_str(),
        generation.as_str(),
        grant.canonicalized_parameters_hash.as_str(),
        grant.issued_at.as_str(),
        grant.expires_at.as_str(),
    ]
    .join(":")
    .into_bytes()
}

/// Minimal, dependency-free hex decoder — this module owns both ends of
/// this specific, fixed hex encoding (the backend's `bytes.hex()` /
/// `bytes.fromhex()`), so a small purpose-built decoder is safer and
/// smaller than pulling in a general-purpose hex crate for one call site.
fn decode_hex(value: &str) -> Result<Vec<u8>, ()> {
    if value.len() % 2 != 0 {
        return Err(());
    }
    (0..value.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&value[i..i + 2], 16).map_err(|_| ()))
        .collect()
}

/// The encoding counterpart to `decode_hex` — used by
/// `canonicalize_and_hash` to render a SHA-256 digest the same lowercase
/// hex shape as the backend's own `hashlib.sha256(...).hexdigest()`.
fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Browser-B5 (navigate/screenshot execution): Rust port of
/// `backend/src/kortex/engines/browser/grant.py::canonicalize_and_hash`.
/// `serde_json::to_string` on a `serde_json::Value` built from a
/// `serde_json::Map` (this crate does not enable the `preserve_order`
/// feature, so `Map` is `BTreeMap`-backed — keys sort automatically) is
/// both compact and key-sorted by construction — matching Python's own
/// `json.dumps(payload, sort_keys=True, separators=(",", ":"))` byte-for-
/// byte, which is WHY that Python-side separator was changed to compact
/// alongside this port landing (see `grant.py`'s own updated doc comment)
/// rather than this function trying to hand-roll Python's OLD spaced
/// format. Proven, not assumed: `real_rust_hash_matches_real_python_hash`
/// below embeds real digests captured from the actual Python function for
/// several fixtures, including one specifically constructed to prove a
/// substituted parameter changes the hash.
fn canonicalize_and_hash(capability_name: &str, parameters: &serde_json::Value) -> String {
    let payload = serde_json::json!({
        "capability": capability_name,
        "parameters": parameters,
    });
    let encoded = serde_json::to_string(&payload)
        .expect("a serde_json::Value built from this module's own types always serializes");
    let mut hasher = Sha256::new();
    hasher.update(encoded.as_bytes());
    hex_encode(&hasher.finalize())
}

/// Reconstructs the exact `parameters` object
/// `backend/src/kortex/engines/browser/engine.py`'s `navigate` handler
/// passed to `canonicalize_and_hash` at mint time. `target` is built from
/// the Grant's OWN already live-verified `browser_profile_id`/`surface_id`/
/// `navigation_generation` fields — never from a second, caller-supplied
/// target the wire params might carry, since there would be nothing
/// independent left to check such a value against (`browser_b5_5_
/// architecture_gate.md` OD-01).
fn navigate_parameters_value(
    grant: &CapabilityExecutionGrant,
    url: &str,
    timeout_ms: u64,
) -> serde_json::Value {
    serde_json::json!({
        "target": {
            "browser_profile_id": grant.browser_profile_id,
            "surface_id": grant.surface_id,
            "navigation_generation": grant.navigation_generation,
        },
        "url": url,
        "timeout_ms": timeout_ms,
    })
}

/// Screenshot's own counterpart to `navigate_parameters_value` — see that
/// function's doc comment for why `target` always comes from the Grant.
fn screenshot_parameters_value(
    grant: &CapabilityExecutionGrant,
    full_page: bool,
) -> serde_json::Value {
    serde_json::json!({
        "target": {
            "browser_profile_id": grant.browser_profile_id,
            "surface_id": grant.surface_id,
            "navigation_generation": grant.navigation_generation,
        },
        "full_page": full_page,
    })
}

/// Independently re-verifies a Grant's Ed25519 signature against
/// `verification_public_key` — the cached public key from
/// `GrantVerificationKeyCache`, never a key the grant itself supplies.
pub fn verify_signature(
    grant: &CapabilityExecutionGrant,
    verification_public_key: &[u8; 32],
) -> Result<(), GrantRejectionReason> {
    let signature_bytes =
        decode_hex(&grant.signature).map_err(|_| GrantRejectionReason::MalformedSignature)?;
    let signature_array: [u8; 64] = signature_bytes
        .try_into()
        .map_err(|_| GrantRejectionReason::MalformedSignature)?;
    let signature = Signature::from_bytes(&signature_array);
    let verifying_key = VerifyingKey::from_bytes(verification_public_key)
        .map_err(|_| GrantRejectionReason::MalformedSignature)?;
    let payload = canonical_payload(grant);
    verifying_key
        .verify(&payload, &signature)
        .map_err(|_| GrantRejectionReason::InvalidSignature)
}

/// Parses `issued_at`/`expires_at` for the expiry comparison ONLY — never
/// for reconstructing the signed payload (see this module's own doc
/// comment on why). Accepts the exact RFC3339 shape Python's
/// `datetime.now(UTC).isoformat()` produces (e.g.
/// `"2026-09-26T15:30:45.123456+00:00"`).
fn parse_rfc3339_to_unix_seconds(value: &str) -> Result<i64, ()> {
    use time::format_description::well_known::Rfc3339;
    time::OffsetDateTime::parse(value, &Rfc3339)
        .map(|parsed| parsed.unix_timestamp())
        .map_err(|_| ())
}

/// Checks `now` is within `[issued_at, expires_at)` — a grant timestamped
/// in the future is rejected exactly like an already-expired one (never
/// treated as "not yet valid but fine"), matching
/// `grant.py::verify_grant`'s own identical rule.
pub fn verify_expiry(grant: &CapabilityExecutionGrant) -> Result<(), GrantRejectionReason> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0);
    let issued_at = parse_rfc3339_to_unix_seconds(&grant.issued_at)
        .map_err(|_| GrantRejectionReason::MalformedTimestamp)?;
    let expires_at = parse_rfc3339_to_unix_seconds(&grant.expires_at)
        .map_err(|_| GrantRejectionReason::MalformedTimestamp)?;
    if now >= expires_at {
        return Err(GrantRejectionReason::Expired);
    }
    if now < issued_at {
        return Err(GrantRejectionReason::NotYetValid);
    }
    Ok(())
}

/// Process-lifetime, in-memory single-use tracker. Not a durable store —
/// grants are seconds-scale short-lived (`DEFAULT_GRANT_TTL_SECONDS`,
/// backend `grant.py`) and this app's own `BrowserSurfaceId`s are already
/// process-lifetime-only (`browser_runtime.rs`), so a process restart
/// invalidates every outstanding grant anyway (its signature no longer
/// verifies against a freshly-generated backend signing key — see
/// `grant.py::generate_grant_signing_keypair`'s own disclosed-limitation
/// doc) — there is nothing this tracker needs to remember across a
/// restart.
#[derive(Default)]
pub struct RedeemedGrantTracker {
    redeemed: Mutex<HashSet<String>>,
}

impl RedeemedGrantTracker {
    pub fn new() -> Self {
        Self::default()
    }

    /// Atomically checks-and-marks `grant_id` as redeemed under a single
    /// lock acquisition — never a separate check-then-insert, which would
    /// let two concurrent redeem attempts for the identical grant both
    /// observe "not yet redeemed" and both proceed.
    pub fn try_mark_redeemed(&self, grant_id: &str) -> Result<(), GrantRejectionReason> {
        let mut redeemed = self.redeemed.lock().unwrap();
        if redeemed.contains(grant_id) {
            return Err(GrantRejectionReason::AlreadyRedeemed);
        }
        redeemed.insert(grant_id.to_string());
        Ok(())
    }
}

/// Fetches and caches (for this process's own lifetime) the backend's
/// current Grant-signing public key, via the *ordinary*, already-
/// authenticated `invoke_capability` transport — never a bespoke channel.
/// `kortex.browser.grant_verification_key` is itself an ordinary,
/// authenticated capability (`requires_authentication=True`, per its own
/// registration in `engine.py`), so this reuses exactly the same trust
/// path every other capability call already goes through; the point of
/// caching it independently is only so grant *signature* verification
/// never has to re-trust the transport on every single redeem call.
#[derive(Default)]
pub struct GrantVerificationKeyCache {
    cached: Mutex<Option<[u8; 32]>>,
}

impl GrantVerificationKeyCache {
    pub fn new() -> Self {
        Self::default()
    }

    /// Returns the cached key if present; otherwise fetches it once via
    /// `kortex.browser.grant_verification_key` and caches the result.
    /// Never re-fetches merely because a redeem attempt failed for an
    /// unrelated reason (an expired or tampered grant does not mean the
    /// key rotated) — a caller that suspects real key rotation (e.g. every
    /// redeem attempt now fails signature verification) should call
    /// `invalidate()` explicitly first.
    pub async fn get_or_fetch(&self, ipc_state: &IpcClientState) -> Result<[u8; 32], String> {
        if let Some(key) = *self.cached.lock().unwrap() {
            return Ok(key);
        }
        let envelope = forward_capability_request(
            ipc_state,
            IpcCapabilityRequest {
                request_id: uuid_like_request_id(),
                capability_name: GRANT_VERIFICATION_KEY_CAPABILITY.to_string(),
                parameters: serde_json::Value::Object(Default::default()),
                correlation_id: None,
                idempotency_key: None,
                timeout_ms: None,
            },
        )
        .await;
        if envelope.status != "SUCCESS" {
            return Err(format!(
                "kortex.browser.grant_verification_key failed: status={}",
                envelope.status
            ));
        }
        let public_key_hex = envelope
            .payload
            .as_ref()
            .and_then(|payload| payload.get("result"))
            .and_then(|result| result.get("public_key_hex"))
            .and_then(|value| value.as_str())
            .ok_or_else(|| "grant_verification_key response missing public_key_hex".to_string())?;
        let key_bytes = decode_hex(public_key_hex).map_err(|_| {
            "grant_verification_key response public_key_hex is not valid hex".to_string()
        })?;
        let key_array: [u8; 32] = key_bytes.try_into().map_err(|_| {
            "grant_verification_key response public_key_hex is not 32 bytes".to_string()
        })?;
        *self.cached.lock().unwrap() = Some(key_array);
        Ok(key_array)
    }

    /// Forces the next `get_or_fetch` call to re-fetch rather than reuse a
    /// cached key — for a caller that has independent reason to believe
    /// the backend's signing key rotated (e.g. a backend restart, which
    /// `grant.py::generate_grant_signing_keypair`'s own doc discloses as
    /// the one condition that changes it).
    pub fn invalidate(&self) {
        *self.cached.lock().unwrap() = None;
    }
}

/// Not a real UUID — mirrors `ipc.rs::uuid_like_id`'s own reasoning
/// (already established in this crate): only needs to be unique enough
/// for this one-shot request's own `request_id`/audit correlation, never
/// parsed as a UUID by anything.
fn uuid_like_request_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("browser-grant-key-fetch-{nanos:x}")
}

/// Browser-B5.4 §17 (concurrency): per-surface serialization for
/// AI-originated redeem attempts — never a single global lock (different
/// surfaces, profiles, and tenants all proceed fully concurrently; only
/// two redeem attempts racing for the identical surface are serialized).
/// A `tokio::sync::Mutex` (not `std::sync::Mutex`) because the redeem
/// command is `async` and must hold this lock across an `.await` point
/// without blocking the executor thread.
#[derive(Default)]
pub struct SurfaceRedeemLocks {
    locks: Mutex<std::collections::HashMap<String, std::sync::Arc<tokio::sync::Mutex<()>>>>,
}

impl SurfaceRedeemLocks {
    pub fn new() -> Self {
        Self::default()
    }

    /// Returns (creating on first use) the lock for `surface_id`. Never
    /// removes an entry once created — surfaces are few and process-
    /// lifetime-bounded already (`browser_runtime.rs`), so this map's
    /// worst-case size is bounded by "every surface ever opened this
    /// process", not a real leak concern.
    pub fn lock_for(&self, surface_id: &str) -> std::sync::Arc<tokio::sync::Mutex<()>> {
        let mut locks = self.locks.lock().unwrap();
        locks
            .entry(surface_id.to_string())
            .or_insert_with(|| std::sync::Arc::new(tokio::sync::Mutex::new(())))
            .clone()
    }
}

/// Every typed outcome `browser_execute_granted_action` can report to its
/// caller — mirrors `backend/src/kortex/engines/browser/models.py::
/// BrowserActionErrorCode`'s vocabulary exactly where applicable, so the
/// same error names mean the same thing on both sides of the wire.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum BrowserGrantExecutionError {
    GrantInvalid,
    GrantExpired,
    SurfaceNotFound,
    ProfileNotFound,
    StaleReference,
    InternalError {
        message: String,
    },
    /// Browser-B5: `grant.capability_name` is `NAVIGATE_CAPABILITY`/
    /// `SCREENSHOT_CAPABILITY` (a capability this redeem command actually
    /// executes) but `params` was `None`, or its `capability` tag did not
    /// match `grant.capability_name` — see `BrowserCapabilityParamsWire`'s
    /// own doc comment.
    ParametersRequired,
    /// A capability-specific parameter this desktop cannot honor was
    /// requested — currently only `BrowserScreenshotParamsWire::full_page
    /// == true` (WebView2's `CapturePreview` has no native full-scrollable-
    /// page capture; see `BrowserRuntime::capture_screenshot`'s own doc).
    /// Refusing explicitly, rather than silently substituting a
    /// viewport-only image for a caller who asked for the whole page.
    FullPageNotYetSupported,
    /// `NavigationStarting` evaluated Browser-B4 policy as `Deny` for an
    /// AI-originated navigation — the SAME policy engine, the SAME
    /// enforcement point, a human click already goes through
    /// (`browser_policy.rs`); never a second, weaker check.
    PolicyDenied {
        reason: DenyReason,
    },
    /// `NavigationCompleted` fired with `IsSuccess == false` — the
    /// navigation was policy-allowed and started, but failed at the
    /// network/TLS/DNS layer (or a `-1` sentinel if even that detail could
    /// not be read — see `navigation_completed_outcome`'s own doc).
    NavigationFailed {
        web_error_status: i32,
    },
    /// `CapturePreview` itself reported failure, or its completed stream
    /// could not be read back — `message` is a Rust-side diagnostic only
    /// (a COM error string), never page content.
    ScreenshotFailed {
        message: String,
    },
    /// Neither `NavigationStarting`'s Deny arm nor `NavigationCompleted`
    /// (for navigate), or `CapturePreview`'s own completion handler (for
    /// screenshot), resolved within the operation's timeout budget. Per
    /// `docs/architecture/browser_b5_5_architecture_gate.md` T15: this is
    /// an UNKNOWN outcome, not a proof that nothing happened — a caller
    /// must not assume the underlying WebView2 operation did not occur.
    Timeout,
}

impl From<GrantRejectionReason> for BrowserGrantExecutionError {
    fn from(reason: GrantRejectionReason) -> Self {
        match reason {
            GrantRejectionReason::Expired | GrantRejectionReason::NotYetValid => Self::GrantExpired,
            GrantRejectionReason::MalformedSignature
            | GrantRejectionReason::InvalidSignature
            | GrantRejectionReason::MalformedTimestamp
            | GrantRejectionReason::AlreadyRedeemed => Self::GrantInvalid,
        }
    }
}

/// Browser-B5: the five Grant-redemption/execution-lifecycle audit events
/// `backend/src/kortex/engines/browser/audit.py`'s own
/// `DESKTOP_LOCAL_RECORDED_EVENTS` names and documents as this desktop
/// redeem command's responsibility to record — into the SAME local,
/// JSON-Lines `<profiles_root>/audit.log` `browser_policy.rs` already
/// writes its own B4 policy-enforcement events to (one place to look),
/// using these exact `SCREAMING_SNAKE_CASE` names as the shared
/// backend/desktop audit vocabulary.
// The shared `Browser` prefix is deliberate, not accidental repetition:
// these variant names must map 1:1 (via `rename_all`) onto
// `backend/src/kortex/engines/browser/audit.py`'s own fixed constant
// strings (`BROWSER_GRANT_REDEEMED`, etc.) — stripping the prefix here
// would silently change the wire vocabulary this file's own doc comment
// says must stay shared between the two audit logs.
#[allow(clippy::enum_variant_names)]
#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum BrowserExecutionAuditEvent {
    BrowserGrantRedeemed,
    BrowserGrantRejected,
    BrowserExecutionStarted,
    BrowserExecutionSucceeded,
    BrowserExecutionFailed,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct BrowserExecutionAuditLogEntry<'a> {
    event: BrowserExecutionAuditEvent,
    timestamp_utc: u64,
    surface_id: &'a str,
    capability: &'a str,
    /// A short, fixed, safe token only (e.g. `"signature"`, `"timeout"`,
    /// a `DenyReason`'s own `Debug` rendering) — NEVER a raw parameter
    /// value, URL, or page content. Every call site in this file passes
    /// only already-safe, already-typed information; see
    /// `record_browser_execution_audit_event`'s own doc comment.
    detail: Option<String>,
}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Best-effort, append-only JSON Lines write — a logging failure must
/// never block or influence the redemption/execution decision it
/// describes, matching `browser_policy::record_policy_audit_event`'s own
/// convention exactly (same file, same format, same non-blocking
/// posture). Callers must only ever pass a `detail` built from already-
/// typed, already-safe data (a fixed literal, an error `kind`, a
/// `DenyReason`) — this function performs no redaction of its own, the
/// same "never pass a secret in" discipline `audit.py::
/// record_browser_audit_event` itself documents.
fn record_browser_execution_audit_event(
    audit_log_path: &std::path::Path,
    event: BrowserExecutionAuditEvent,
    surface_id: &str,
    capability: &str,
    detail: Option<String>,
) {
    let entry = BrowserExecutionAuditLogEntry {
        event,
        timestamp_utc: unix_now(),
        surface_id,
        capability,
        detail,
    };
    let Ok(line) = serde_json::to_string(&entry) else {
        return;
    };
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(audit_log_path)
    {
        use std::io::Write;
        let _ = writeln!(file, "{line}");
    }
}

/// Redemption result. `Success`/`Screenshot` are only ever returned for
/// `NAVIGATE_CAPABILITY`/`SCREENSHOT_CAPABILITY` respectively — every
/// other registered `kortex.browser.*` capability still redeems
/// successfully (every check below still runs and still fails closed) but
/// reports `NotYetEnabled`, unchanged from B5.0-B5.4's own deliberate,
/// disclosed placeholder (see this file's own module doc) — never a
/// silent no-op a caller could mistake for real execution.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "status", rename_all = "camelCase")]
pub enum BrowserGrantExecutionResult {
    Success {
        capability_name: String,
    },
    /// `image_base64` is the captured viewport PNG, standard (not
    /// URL-safe) base64 — never written to disk by this desktop process
    /// (`browser_b5_5_architecture_gate.md` §15's "never store
    /// unnecessarily" posture); it exists only for the duration of this
    /// one IPC response.
    Screenshot {
        capability_name: String,
        image_base64: String,
    },
    NotYetEnabled {
        capability_name: String,
    },
}

/// Browser-B5.4: the single AI-originated redeem command — never
/// repurposes a human-facing command (`browser_navigate`/etc.), never
/// accepts a raw URL/script/selector-free instruction. Thin wrapper only:
/// unwraps Tauri-managed state and delegates to `execute_granted_action`,
/// which takes plain references so it can be exercised directly, with a
/// fake `BrowserRuntime`, by this module's own tests — the same "no live
/// Window/Webview in this test binary" constraint the crate's `test`
/// feature already forces elsewhere (see `browser_runtime.rs`'s own test
/// module doc) means the FULL command can only ever be proven correct
/// against a real WebView2 surface via the disposable live-preflight
/// mechanism, never an automated test — extracting the orchestration
/// logic into a plain, dependency-injected function is what makes
/// everything BUT the raw COM calls testable at all.
// Argument count is dictated by Tauri-managed state (one parameter per
// independently-owned piece of state this command touches) plus the two
// real inputs (`grant`, `params`) — bundling them into an artificial
// struct would not reduce real complexity, only hide it.
#[allow(clippy::too_many_arguments)]
#[tauri::command]
pub async fn browser_execute_granted_action(
    runtime_state: tauri::State<'_, crate::browser_runtime::BrowserRuntimeState>,
    ipc_state: tauri::State<'_, std::sync::Arc<IpcClientState>>,
    active_profile_surfaces: tauri::State<'_, crate::browser_profile_store::ActiveProfileSurfaces>,
    grant_key_cache: tauri::State<'_, GrantVerificationKeyCache>,
    redeemed_tracker: tauri::State<'_, RedeemedGrantTracker>,
    surface_locks: tauri::State<'_, SurfaceRedeemLocks>,
    audit_log_path: tauri::State<'_, crate::browser_runtime::PolicyAuditLogPath>,
    grant: CapabilityExecutionGrant,
    params: Option<BrowserCapabilityParamsWire>,
) -> Result<BrowserGrantExecutionResult, BrowserGrantExecutionError> {
    let public_key = grant_key_cache
        .get_or_fetch(&ipc_state)
        .await
        .map_err(|message| BrowserGrantExecutionError::InternalError { message })?;
    if verify_signature(&grant, &public_key).is_err() {
        // A genuinely valid Grant failing signature verification against
        // the CACHED key most plausibly means the backend restarted and
        // rotated its process-lifetime signing key since the cache was
        // populated (`grant.py::generate_grant_signing_keypair`'s own
        // disclosed limitation) — invalidate so the *next* attempt
        // re-fetches, rather than requiring an app restart to recover.
        // Never retried within this same call: a caller with a genuinely
        // tampered Grant must still see it rejected now, not silently
        // retried against a freshly-fetched key.
        grant_key_cache.invalidate();
    }
    execute_granted_action(
        &*runtime_state.0,
        &public_key,
        &active_profile_surfaces,
        &redeemed_tracker,
        &surface_locks,
        &audit_log_path.0,
        grant,
        params,
    )
    .await
}

/// The full redeem-and-execute orchestration, dependency-injected so it
/// can be exercised without a live Tauri/WebView2 process. Verifies, in
/// order, every one of the independent checks §6/§8/§17 of the B5
/// architecture gate require, failing closed on the first one that
/// doesn't hold — signature, expiry, single-use, live surface existence,
/// live tenant/profile binding, live navigation generation — then, for
/// `NAVIGATE_CAPABILITY`/`SCREENSHOT_CAPABILITY` only, independently
/// recomputes the parameter hash (`browser_b5_5_architecture_gate.md`
/// OD-01) before ever touching `runtime`, then executes through the
/// SAME `BrowserRuntime` trait method a human-driven command already goes
/// through — B4's `BrowserPolicyEngine` remains the final,
/// structurally-unbypassable gate regardless of anything decided here.
/// All of the above (from the surface lock acquisition onward) runs while
/// holding `SurfaceRedeemLocks`' per-surface lock for the FULL duration,
/// including the navigate/screenshot wait — not just the pre-execution
/// checks — so two concurrent redeem attempts for the same surface can
/// never interleave, and neither can two calls race for the same
/// navigation-outcome waiter slot (`browser_b5_5_architecture_gate.md`
/// T11).
#[allow(clippy::too_many_arguments)]
async fn execute_granted_action(
    runtime: &dyn BrowserRuntime,
    verification_public_key: &[u8; 32],
    active_profile_surfaces: &crate::browser_profile_store::ActiveProfileSurfaces,
    redeemed_tracker: &RedeemedGrantTracker,
    surface_locks: &SurfaceRedeemLocks,
    audit_log_path: &std::path::Path,
    grant: CapabilityExecutionGrant,
    params: Option<BrowserCapabilityParamsWire>,
) -> Result<BrowserGrantExecutionResult, BrowserGrantExecutionError> {
    let reject = |detail: &str| {
        record_browser_execution_audit_event(
            audit_log_path,
            BrowserExecutionAuditEvent::BrowserGrantRejected,
            &grant.surface_id,
            &grant.capability_name,
            Some(detail.to_string()),
        );
    };

    if let Err(reason) = verify_signature(&grant, verification_public_key) {
        reject("signature");
        return Err(reason.into());
    }
    if let Err(reason) = verify_expiry(&grant) {
        reject("expiry");
        return Err(reason.into());
    }
    if let Err(reason) = redeemed_tracker.try_mark_redeemed(&grant.grant_id) {
        reject("replay");
        return Err(reason.into());
    }

    let surface_lock = surface_locks.lock_for(&grant.surface_id);
    let _guard = surface_lock.lock().await;

    let surface_id = BrowserSurfaceId::from_string(grant.surface_id.clone());
    if !runtime.surface_exists(&surface_id) {
        reject("surface_not_found");
        return Err(BrowserGrantExecutionError::SurfaceNotFound);
    }

    match active_profile_surfaces.lookup(&surface_id) {
        None => {
            reject("profile_not_found");
            return Err(BrowserGrantExecutionError::ProfileNotFound);
        }
        Some((live_tenant_id, live_profile_id)) => {
            if live_tenant_id != grant.tenant_id
                || live_profile_id.as_str() != grant.browser_profile_id
            {
                // Never distinguished for the caller (tenant mismatch vs.
                // profile mismatch vs. legitimately gone) — same
                // "don't tell a probing caller which check failed"
                // posture as `BrowserGrantInvalidError` (backend
                // `exceptions.py`). `ProfileNotFound` is the closest
                // existing typed error to "this surface is not bound to
                // the tenant/profile you claimed" without inventing a new
                // error variant for what is, from the caller's side,
                // indistinguishable from the profile simply not existing.
                reject("profile_mismatch");
                return Err(BrowserGrantExecutionError::ProfileNotFound);
            }
        }
    }

    if let Some(claimed_generation) = grant.navigation_generation {
        let live_generation = runtime
            .navigation_generation(&surface_id)
            .map_err(|_| BrowserGrantExecutionError::SurfaceNotFound)?;
        if live_generation != claimed_generation {
            reject("generation_mismatch");
            return Err(BrowserGrantExecutionError::StaleReference);
        }
    }

    record_browser_execution_audit_event(
        audit_log_path,
        BrowserExecutionAuditEvent::BrowserGrantRedeemed,
        &grant.surface_id,
        &grant.capability_name,
        None,
    );

    match grant.capability_name.as_str() {
        NAVIGATE_CAPABILITY => {
            execute_navigate(runtime, audit_log_path, &grant, params, &surface_id).await
        }
        SCREENSHOT_CAPABILITY => {
            execute_screenshot(runtime, audit_log_path, &grant, params, &surface_id).await
        }
        _ => Ok(BrowserGrantExecutionResult::NotYetEnabled {
            capability_name: grant.capability_name,
        }),
    }
}

async fn execute_navigate(
    runtime: &dyn BrowserRuntime,
    audit_log_path: &std::path::Path,
    grant: &CapabilityExecutionGrant,
    params: Option<BrowserCapabilityParamsWire>,
    surface_id: &BrowserSurfaceId,
) -> Result<BrowserGrantExecutionResult, BrowserGrantExecutionError> {
    let Some(BrowserCapabilityParamsWire::Navigate { url, timeout_ms }) = params else {
        return Err(BrowserGrantExecutionError::ParametersRequired);
    };

    // Browser-B5.5 OD-01: independently recompute the parameter hash
    // BEFORE touching `runtime` at all — a validly-signed, validly-scoped
    // Grant redeemed with a DIFFERENT url than it was minted for must be
    // rejected here, never merely audited after the fact.
    let expected_hash = canonicalize_and_hash(
        NAVIGATE_CAPABILITY,
        &navigate_parameters_value(grant, &url, timeout_ms),
    );
    if expected_hash != grant.canonicalized_parameters_hash {
        record_browser_execution_audit_event(
            audit_log_path,
            BrowserExecutionAuditEvent::BrowserGrantRejected,
            &grant.surface_id,
            &grant.capability_name,
            Some("parameter_hash_mismatch".to_string()),
        );
        return Err(BrowserGrantExecutionError::GrantInvalid);
    }

    record_browser_execution_audit_event(
        audit_log_path,
        BrowserExecutionAuditEvent::BrowserExecutionStarted,
        &grant.surface_id,
        &grant.capability_name,
        None,
    );

    // Browser-B5.5 OD-02: the waiter MUST be armed before `navigate()` is
    // issued — arming after would leave a window where a fast
    // `NavigationStarting`/`NavigationCompleted` pair could fire and find
    // no waiter yet listening (`browser_b5_5_architecture_gate.md` §7).
    let receiver = runtime.arm_navigation_waiter(surface_id).map_err(|e| {
        BrowserGrantExecutionError::InternalError {
            message: e.to_string(),
        }
    })?;
    if let Err(e) = runtime.navigate(surface_id, &url) {
        record_browser_execution_audit_event(
            audit_log_path,
            BrowserExecutionAuditEvent::BrowserExecutionFailed,
            &grant.surface_id,
            &grant.capability_name,
            Some("issue_failed".to_string()),
        );
        return Err(BrowserGrantExecutionError::InternalError {
            message: e.to_string(),
        });
    }

    // Bounded on BOTH ends, never trusting the wire value alone — the
    // backend's own `BrowserNavigateParams.timeout_ms` Pydantic field
    // already enforces `100..=300_000`, but the desktop is a separate
    // trust boundary and must not assume a caller-supplied value already
    // respects that range (found during this milestone's own adversarial
    // review: the original code clamped only the upper bound, leaving a
    // tiny/zero `timeout_ms` free to cause a near-instant, spurious
    // `Timeout` before `NavigationStarting` could plausibly even fire).
    let bounded_timeout = Duration::from_millis(timeout_ms.clamp(100, 300_000));
    match tokio::time::timeout(bounded_timeout, receiver).await {
        Ok(Ok(NavigationOutcome::Success)) => {
            record_browser_execution_audit_event(
                audit_log_path,
                BrowserExecutionAuditEvent::BrowserExecutionSucceeded,
                &grant.surface_id,
                &grant.capability_name,
                None,
            );
            Ok(BrowserGrantExecutionResult::Success {
                capability_name: grant.capability_name.clone(),
            })
        }
        Ok(Ok(NavigationOutcome::PolicyDenied(reason))) => {
            record_browser_execution_audit_event(
                audit_log_path,
                BrowserExecutionAuditEvent::BrowserExecutionFailed,
                &grant.surface_id,
                &grant.capability_name,
                Some(format!("policy_denied:{reason:?}")),
            );
            Err(BrowserGrantExecutionError::PolicyDenied { reason })
        }
        Ok(Ok(NavigationOutcome::Failed { web_error_status })) => {
            record_browser_execution_audit_event(
                audit_log_path,
                BrowserExecutionAuditEvent::BrowserExecutionFailed,
                &grant.surface_id,
                &grant.capability_name,
                Some(format!("navigation_failed:{web_error_status}")),
            );
            Err(BrowserGrantExecutionError::NavigationFailed { web_error_status })
        }
        Ok(Err(_)) => {
            record_browser_execution_audit_event(
                audit_log_path,
                BrowserExecutionAuditEvent::BrowserExecutionFailed,
                &grant.surface_id,
                &grant.capability_name,
                Some("waiter_dropped".to_string()),
            );
            Err(BrowserGrantExecutionError::InternalError {
                message: "navigation outcome channel closed unexpectedly".to_string(),
            })
        }
        Err(_) => {
            record_browser_execution_audit_event(
                audit_log_path,
                BrowserExecutionAuditEvent::BrowserExecutionFailed,
                &grant.surface_id,
                &grant.capability_name,
                Some("timeout".to_string()),
            );
            Err(BrowserGrantExecutionError::Timeout)
        }
    }
}

async fn execute_screenshot(
    runtime: &dyn BrowserRuntime,
    audit_log_path: &std::path::Path,
    grant: &CapabilityExecutionGrant,
    params: Option<BrowserCapabilityParamsWire>,
    surface_id: &BrowserSurfaceId,
) -> Result<BrowserGrantExecutionResult, BrowserGrantExecutionError> {
    let Some(BrowserCapabilityParamsWire::Screenshot { full_page }) = params else {
        return Err(BrowserGrantExecutionError::ParametersRequired);
    };
    if full_page {
        // Checked BEFORE the hash comparison deliberately: a caller asking
        // for an unsupported capture mode is refused on that basis alone,
        // never left to wonder whether it was instead a hash/tamper
        // problem.
        return Err(BrowserGrantExecutionError::FullPageNotYetSupported);
    }

    let expected_hash = canonicalize_and_hash(
        SCREENSHOT_CAPABILITY,
        &screenshot_parameters_value(grant, full_page),
    );
    if expected_hash != grant.canonicalized_parameters_hash {
        record_browser_execution_audit_event(
            audit_log_path,
            BrowserExecutionAuditEvent::BrowserGrantRejected,
            &grant.surface_id,
            &grant.capability_name,
            Some("parameter_hash_mismatch".to_string()),
        );
        return Err(BrowserGrantExecutionError::GrantInvalid);
    }

    record_browser_execution_audit_event(
        audit_log_path,
        BrowserExecutionAuditEvent::BrowserExecutionStarted,
        &grant.surface_id,
        &grant.capability_name,
        None,
    );

    let receiver = runtime.capture_screenshot(surface_id).map_err(|e| {
        BrowserGrantExecutionError::InternalError {
            message: e.to_string(),
        }
    })?;
    match tokio::time::timeout(SCREENSHOT_TIMEOUT, receiver).await {
        Ok(Ok(Ok(bytes))) => {
            record_browser_execution_audit_event(
                audit_log_path,
                BrowserExecutionAuditEvent::BrowserExecutionSucceeded,
                &grant.surface_id,
                &grant.capability_name,
                None,
            );
            Ok(BrowserGrantExecutionResult::Screenshot {
                capability_name: grant.capability_name.clone(),
                image_base64: base64::engine::general_purpose::STANDARD.encode(bytes),
            })
        }
        Ok(Ok(Err(message))) => {
            record_browser_execution_audit_event(
                audit_log_path,
                BrowserExecutionAuditEvent::BrowserExecutionFailed,
                &grant.surface_id,
                &grant.capability_name,
                Some("capture_failed".to_string()),
            );
            Err(BrowserGrantExecutionError::ScreenshotFailed { message })
        }
        Ok(Err(_)) => {
            record_browser_execution_audit_event(
                audit_log_path,
                BrowserExecutionAuditEvent::BrowserExecutionFailed,
                &grant.surface_id,
                &grant.capability_name,
                Some("waiter_dropped".to_string()),
            );
            Err(BrowserGrantExecutionError::InternalError {
                message: "screenshot outcome channel closed unexpectedly".to_string(),
            })
        }
        Err(_) => {
            record_browser_execution_audit_event(
                audit_log_path,
                BrowserExecutionAuditEvent::BrowserExecutionFailed,
                &grant.surface_id,
                &grant.capability_name,
                Some("timeout".to_string()),
            );
            Err(BrowserGrantExecutionError::Timeout)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Test-only keypair generation via `SigningKey::from_bytes` over a
    /// `getrandom`-sourced 32-byte seed (`getrandom` is already a real,
    /// direct dependency of this crate — `secure_keys.rs`) — deliberately
    /// avoids `SigningKey::generate`, which requires a `rand_core` RNG
    /// trait bound this crate does not otherwise depend on for one
    /// test-only call site.
    fn signing_keypair() -> (ed25519_dalek::SigningKey, [u8; 32]) {
        let mut seed = [0u8; 32];
        getrandom::getrandom(&mut seed).expect("OS RNG must be available for a test-only keypair");
        let signing_key = ed25519_dalek::SigningKey::from_bytes(&seed);
        let verifying_key = signing_key.verifying_key().to_bytes();
        (signing_key, verifying_key)
    }

    fn sign(signing_key: &ed25519_dalek::SigningKey, grant: &CapabilityExecutionGrant) -> String {
        use ed25519_dalek::Signer;
        let signature = signing_key.sign(&canonical_payload(grant));
        hex_encode(&signature.to_bytes())
    }

    /// Real output of `backend/src/kortex/engines/browser/grant.py::mint_grant`,
    /// captured live against the actual `LocalCrypto`/`VerificationService`
    /// stack (not hand-constructed) — proves this module's signature
    /// verification and canonical-payload reconstruction are genuinely
    /// cross-language-compatible with the backend's own Ed25519 signing,
    /// not merely self-consistent within this file's own sign/verify round
    /// trip.
    ///
    /// This exact test caught a real, would-have-shipped bug on its first
    /// run: an earlier version of `BrowserCapabilityExecutionGrant` typed
    /// `issued_at`/`expires_at` as `datetime`, and `_canonical_grant_payload`
    /// (Python) called `.isoformat()` on them directly, producing
    /// `"...+00:00"` — but Pydantic's own `model_dump(mode="json")`
    /// serialization of a `datetime` field produces `"...Z"` instead. The
    /// signature therefore covered a DIFFERENT string than the one that
    /// ever actually crossed the wire, so every real grant would have
    /// failed signature verification the moment the desktop tried to
    /// verify it — a 100% reproducible bug this self-consistent Rust-only
    /// test suite could never have caught on its own, only a real
    /// cross-language fixture could. Fixed by making the fields plain
    /// `str` (see `models.py`'s own doc comment) so there is only ever one
    /// representation, used identically for signing and for JSON output.
    #[test]
    fn real_python_minted_grant_verifies_cross_language() {
        let grant = CapabilityExecutionGrant {
            grant_id: "e848fac4-0a05-4533-97c9-126a83027813".to_string(),
            tenant_id: "tenant-a".to_string(),
            principal_id: "ai-system".to_string(),
            capability_name: "kortex.browser.navigate".to_string(),
            browser_profile_id: "profile-1".to_string(),
            surface_id: "surface-1".to_string(),
            navigation_generation: Some(3),
            canonicalized_parameters_hash: "4ba5d86493e8ef2ba115e280dd577d60e63859c63a867227d0573a23e20e9560"
                .to_string(),
            issued_at: "2026-09-26T17:36:09.873517+00:00".to_string(),
            expires_at: "2026-09-26T17:38:09.873517+00:00".to_string(),
            signature: "6f1896962c05709c5c82c89496f40e3f7ce8266f4640c62c5b68a0f229c6f7777aa177628694bdd7580f1a58b52b7e56239c628a9eb7c832bdeb31f516a97909".to_string(),
        };
        let public_key =
            decode_hex("e26135fbfb2862df36f2001aa33c4c80610a98af778d2c804c196b1017bbdf01")
                .expect("fixture public key must be valid hex");
        let public_key_array: [u8; 32] = public_key
            .try_into()
            .expect("fixture public key must be 32 bytes");

        assert!(
            verify_signature(&grant, &public_key_array).is_ok(),
            "a real, Python-minted grant must verify against Rust's own Ed25519 implementation"
        );

        // Whether this fixture's 120-second TTL has elapsed by the time this
        // test actually runs is a timing accident, not the point of this
        // assertion -- what matters is that the RFC3339 timestamp parses at
        // all. `MalformedTimestamp` is the one outcome that would prove it
        // didn't; `Ok(())` (not yet elapsed) and `Err(Expired)` (elapsed)
        // both prove it parsed correctly.
        assert_ne!(
            verify_expiry(&grant),
            Err(GrantRejectionReason::MalformedTimestamp)
        );
    }

    fn base_grant() -> CapabilityExecutionGrant {
        CapabilityExecutionGrant {
            grant_id: "grant-1".to_string(),
            tenant_id: "tenant-a".to_string(),
            principal_id: "ai-system".to_string(),
            capability_name: "kortex.browser.navigate".to_string(),
            browser_profile_id: "profile-1".to_string(),
            surface_id: "surface-1".to_string(),
            navigation_generation: Some(0),
            canonicalized_parameters_hash: "deadbeef".to_string(),
            issued_at: "2026-01-01T00:00:00.000000+00:00".to_string(),
            expires_at: "2026-01-01T00:00:20.000000+00:00".to_string(),
            signature: "00".repeat(64),
        }
    }

    #[test]
    fn valid_signature_verifies() {
        let (signing_key, verifying_key) = signing_keypair();
        let mut grant = base_grant();
        grant.signature = sign(&signing_key, &grant);
        assert!(verify_signature(&grant, &verifying_key).is_ok());
    }

    #[test]
    fn wrong_verification_key_rejected() {
        let (signing_key, _verifying_key) = signing_keypair();
        let (_other_signing_key, other_verifying_key) = signing_keypair();
        let mut grant = base_grant();
        grant.signature = sign(&signing_key, &grant);
        assert_eq!(
            verify_signature(&grant, &other_verifying_key),
            Err(GrantRejectionReason::InvalidSignature)
        );
    }

    #[test]
    fn malformed_signature_hex_rejected() {
        let (_signing_key, verifying_key) = signing_keypair();
        let mut grant = base_grant();
        grant.signature = "not-hex!!".to_string();
        assert_eq!(
            verify_signature(&grant, &verifying_key),
            Err(GrantRejectionReason::MalformedSignature)
        );
    }

    #[test]
    fn wrong_length_signature_rejected() {
        let (_signing_key, verifying_key) = signing_keypair();
        let mut grant = base_grant();
        grant.signature = "ab".to_string();
        assert_eq!(
            verify_signature(&grant, &verifying_key),
            Err(GrantRejectionReason::MalformedSignature)
        );
    }

    #[test]
    fn tampered_field_fails_signature_verification() {
        let (signing_key, verifying_key) = signing_keypair();
        let mut grant = base_grant();
        grant.signature = sign(&signing_key, &grant);
        // Tamper with a single field after signing -- the canonical
        // payload now differs from what was actually signed.
        grant.surface_id = "surface-attacker-controlled".to_string();
        assert_eq!(
            verify_signature(&grant, &verifying_key),
            Err(GrantRejectionReason::InvalidSignature)
        );
    }

    #[test]
    fn tampered_navigation_generation_fails_signature_verification() {
        let (signing_key, verifying_key) = signing_keypair();
        let mut grant = base_grant();
        grant.signature = sign(&signing_key, &grant);
        grant.navigation_generation = Some(999);
        assert_eq!(
            verify_signature(&grant, &verifying_key),
            Err(GrantRejectionReason::InvalidSignature)
        );
    }

    #[test]
    fn expired_grant_rejected() {
        let mut grant = base_grant();
        grant.issued_at = "2000-01-01T00:00:00.000000+00:00".to_string();
        grant.expires_at = "2000-01-01T00:00:20.000000+00:00".to_string();
        assert_eq!(verify_expiry(&grant), Err(GrantRejectionReason::Expired));
    }

    #[test]
    fn not_yet_valid_grant_rejected() {
        let mut grant = base_grant();
        grant.issued_at = "2999-01-01T00:00:00.000000+00:00".to_string();
        grant.expires_at = "2999-01-01T00:00:20.000000+00:00".to_string();
        assert_eq!(
            verify_expiry(&grant),
            Err(GrantRejectionReason::NotYetValid)
        );
    }

    #[test]
    fn malformed_timestamp_rejected() {
        let mut grant = base_grant();
        grant.expires_at = "not-a-timestamp".to_string();
        assert_eq!(
            verify_expiry(&grant),
            Err(GrantRejectionReason::MalformedTimestamp)
        );
    }

    #[test]
    fn null_navigation_generation_renders_as_empty_not_the_literal_string_none() {
        let (signing_key, verifying_key) = signing_keypair();
        let mut grant = base_grant();
        grant.navigation_generation = None;
        grant.signature = sign(&signing_key, &grant);
        assert!(verify_signature(&grant, &verifying_key).is_ok());
        // A grant differing only in navigation_generation (Some(0) vs
        // None) must sign differently -- proves "" and "0" are not
        // confused by the canonical payload.
        let mut other = base_grant();
        other.navigation_generation = Some(0);
        other.signature = grant.signature.clone();
        assert_eq!(
            verify_signature(&other, &verifying_key),
            Err(GrantRejectionReason::InvalidSignature)
        );
    }

    #[test]
    fn first_redemption_succeeds_second_is_rejected() {
        let tracker = RedeemedGrantTracker::new();
        assert!(tracker.try_mark_redeemed("grant-1").is_ok());
        assert_eq!(
            tracker.try_mark_redeemed("grant-1"),
            Err(GrantRejectionReason::AlreadyRedeemed)
        );
    }

    #[test]
    fn distinct_grant_ids_do_not_interfere() {
        let tracker = RedeemedGrantTracker::new();
        assert!(tracker.try_mark_redeemed("grant-1").is_ok());
        assert!(tracker.try_mark_redeemed("grant-2").is_ok());
    }

    // -- canonicalize_and_hash cross-language fixtures ----------------------

    /// Real digests captured from the actual, currently-shipping
    /// `backend/src/kortex/engines/browser/grant.py::canonicalize_and_hash`
    /// (not hand-computed or reasoned about) — the exact same discipline
    /// `real_python_minted_grant_verifies_cross_language` above already
    /// established for the Grant's SIGNATURE payload, applied here to its
    /// PARAMETER HASH. This is what actually caught D37 the first time;
    /// trusting "the algorithms look equivalent" without this fixture is
    /// exactly the mistake that test suite exists to prevent from
    /// recurring silently for a second cross-language computation.
    #[test]
    fn real_rust_hash_matches_real_python_hash() {
        let navigate_fresh = navigate_parameters_value(
            &grant_with(|g| g.surface_id = "surface-1".to_string()),
            "https://example.com/page",
            30_000,
        );
        assert_eq!(
            canonicalize_and_hash("kortex.browser.navigate", &navigate_fresh),
            "6c9ed91711494969ef38b003bc7f26deea4edae2f8d5fdddadd3b6805236d784"
        );

        let mut grant_with_generation = grant_with(|g| g.surface_id = "surface-1".to_string());
        grant_with_generation.navigation_generation = Some(3);
        let navigate_with_generation =
            navigate_parameters_value(&grant_with_generation, "https://example.com/other", 5_000);
        assert_eq!(
            canonicalize_and_hash("kortex.browser.navigate", &navigate_with_generation),
            "ccea7014736346a78421d606cbde163f798f0af28c992ecdc15ebc513866c848"
        );

        let mut grant_for_screenshot = grant_with(|g| g.surface_id = "surface-1".to_string());
        grant_for_screenshot.navigation_generation = Some(7);
        let screenshot_params = screenshot_parameters_value(&grant_for_screenshot, true);
        assert_eq!(
            canonicalize_and_hash("kortex.browser.screenshot", &screenshot_params),
            "d5cf259454efcc9e261db3c3be3ac6a04e465dd36f41dcc2b8559fc7ad1eb25b"
        );

        // A substituted URL — everything else identical to the first
        // fixture — must hash differently. This is the exact property
        // `execute_navigate`'s own parameter-substitution rejection relies
        // on (OD-01).
        let navigate_substituted = navigate_parameters_value(
            &grant_with(|g| g.surface_id = "surface-1".to_string()),
            "https://attacker.example/evil",
            30_000,
        );
        assert_eq!(
            canonicalize_and_hash("kortex.browser.navigate", &navigate_substituted),
            "c056f19e099d53a290eb7622abb6b669f783273531106ab5424668bbaef94a78"
        );
    }

    /// `grant_with` fixture used only to supply `browser_profile_id`/
    /// `surface_id`/`navigation_generation` to `navigate_parameters_value`/
    /// `screenshot_parameters_value` above — those two functions read only
    /// those three fields, so every other field is an arbitrary, fixed
    /// placeholder matching the real Python fixture's own inputs exactly
    /// (`browser_profile_id: "profile-1"`, `surface_id: "surface-1"`).
    fn grant_with(
        customize: impl FnOnce(&mut CapabilityExecutionGrant),
    ) -> CapabilityExecutionGrant {
        let mut grant = base_grant();
        grant.browser_profile_id = "profile-1".to_string();
        grant.navigation_generation = None;
        customize(&mut grant);
        grant
    }

    // -- execute_granted_action orchestration (Browser-B5 navigate/screenshot execution) --

    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering as AtomicOrdering};
    use std::sync::Arc as StdArc;

    use crate::browser_runtime::BrowserRuntimeError;
    use tokio::sync::oneshot;

    /// Test-only `BrowserRuntime` double. Every method NOT exercised by
    /// `execute_granted_action`'s own orchestration (`create_surface`,
    /// `reload`, `go_back`, `go_forward`, `set_bounds`, `query_state`,
    /// `destroy`, `destroy_all`) panics loudly if ever called — a test
    /// that accidentally reaches one of them should fail obviously, never
    /// silently succeed against a meaningless stub. This is this crate's
    /// established way of proving the redeem command's OWN logic (hash
    /// verification, dispatch, concurrency, timeout) without a live
    /// WebView2 process — see this file's own module doc and
    /// `browser_runtime.rs`'s test-module doc on why the FULL command can
    /// only ever be live-preflighted, never automated end-to-end.
    #[derive(Default)]
    struct FakeBrowserRuntime {
        surface_exists: AtomicBool,
        navigation_generation: AtomicU64,
        navigate_calls: Mutex<Vec<String>>,
        navigate_error: Mutex<Option<String>>,
        /// `None` = the navigation waiter is armed but deliberately never
        /// resolved (keeps the sender alive in `keepalive` instead) — used
        /// by the timeout test. `Some(outcome)` resolves immediately.
        navigate_outcome: Mutex<Option<NavigationOutcome>>,
        screenshot_outcome: Mutex<Option<Result<Vec<u8>, String>>>,
        /// Manual-resolution mode for the concurrency test: when `true`,
        /// `arm_navigation_waiter` never auto-resolves regardless of
        /// `navigate_outcome` — it pushes its sender here and notifies
        /// `armed_notify`, and the TEST resolves it explicitly, at a
        /// moment of its own choosing.
        manual_navigate_resolution: AtomicBool,
        pending_navigate_senders: Mutex<Vec<oneshot::Sender<NavigationOutcome>>>,
        armed_notify: tokio::sync::Notify,
        keepalive: Mutex<Vec<Box<dyn std::any::Any + Send>>>,
    }

    impl FakeBrowserRuntime {
        fn new() -> Self {
            Self {
                surface_exists: AtomicBool::new(true),
                navigate_outcome: Mutex::new(Some(NavigationOutcome::Success)),
                screenshot_outcome: Mutex::new(Some(Ok(vec![1, 2, 3, 4]))),
                ..Default::default()
            }
        }

        fn manual() -> Self {
            let mut runtime = Self::new();
            runtime.manual_navigate_resolution = AtomicBool::new(true);
            runtime
        }
    }

    impl BrowserRuntime for FakeBrowserRuntime {
        fn create_surface(
            &self,
            _request: crate::browser_runtime::CreateSurfaceRequest,
        ) -> Result<BrowserSurfaceId, BrowserRuntimeError> {
            unimplemented!("not exercised by execute_granted_action tests")
        }

        fn navigate(
            &self,
            _surface_id: &BrowserSurfaceId,
            url: &str,
        ) -> Result<(), BrowserRuntimeError> {
            self.navigate_calls.lock().unwrap().push(url.to_string());
            match &*self.navigate_error.lock().unwrap() {
                Some(message) => Err(BrowserRuntimeError::Platform {
                    message: message.clone(),
                }),
                None => Ok(()),
            }
        }

        fn reload(&self, _surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
            unimplemented!("not exercised by execute_granted_action tests")
        }

        fn go_back(&self, _surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
            unimplemented!("not exercised by execute_granted_action tests")
        }

        fn go_forward(&self, _surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
            unimplemented!("not exercised by execute_granted_action tests")
        }

        fn set_bounds(
            &self,
            _surface_id: &BrowserSurfaceId,
            _bounds: crate::browser_runtime::SurfaceBounds,
        ) -> Result<(), BrowserRuntimeError> {
            unimplemented!("not exercised by execute_granted_action tests")
        }

        fn query_state(
            &self,
            _surface_id: &BrowserSurfaceId,
        ) -> Result<crate::browser_runtime::BrowserSurfaceState, BrowserRuntimeError> {
            unimplemented!("not exercised by execute_granted_action tests")
        }

        fn destroy(&self, _surface_id: &BrowserSurfaceId) -> Result<(), BrowserRuntimeError> {
            unimplemented!("not exercised by execute_granted_action tests")
        }

        fn surface_exists(&self, _surface_id: &BrowserSurfaceId) -> bool {
            self.surface_exists.load(AtomicOrdering::SeqCst)
        }

        fn navigation_generation(
            &self,
            _surface_id: &BrowserSurfaceId,
        ) -> Result<u64, BrowserRuntimeError> {
            Ok(self.navigation_generation.load(AtomicOrdering::SeqCst))
        }

        fn arm_navigation_waiter(
            &self,
            _surface_id: &BrowserSurfaceId,
        ) -> Result<oneshot::Receiver<NavigationOutcome>, BrowserRuntimeError> {
            let (tx, rx) = oneshot::channel();
            if self.manual_navigate_resolution.load(AtomicOrdering::SeqCst) {
                self.pending_navigate_senders.lock().unwrap().push(tx);
                self.armed_notify.notify_one();
            } else {
                match self.navigate_outcome.lock().unwrap().clone() {
                    Some(outcome) => {
                        let _ = tx.send(outcome);
                    }
                    None => self.keepalive.lock().unwrap().push(Box::new(tx)),
                }
            }
            Ok(rx)
        }

        fn capture_screenshot(
            &self,
            _surface_id: &BrowserSurfaceId,
        ) -> Result<oneshot::Receiver<Result<Vec<u8>, String>>, BrowserRuntimeError> {
            let (tx, rx) = oneshot::channel();
            match self.screenshot_outcome.lock().unwrap().clone() {
                Some(outcome) => {
                    let _ = tx.send(outcome);
                }
                None => self.keepalive.lock().unwrap().push(Box::new(tx)),
            }
            Ok(rx)
        }

        fn destroy_all(&self) {
            unimplemented!("not exercised by execute_granted_action tests")
        }
    }

    /// Test harness: a fresh `FakeBrowserRuntime` plus every other piece
    /// `execute_granted_action` needs, with ONE surface already registered
    /// as live/existing/bound to `tenant_id`/`browser_profile_id`.
    struct Harness {
        runtime: FakeBrowserRuntime,
        active_profile_surfaces: crate::browser_profile_store::ActiveProfileSurfaces,
        redeemed_tracker: RedeemedGrantTracker,
        surface_locks: SurfaceRedeemLocks,
        audit_log_path: std::path::PathBuf,
        signing_key: ed25519_dalek::SigningKey,
        verifying_key: [u8; 32],
    }

    impl Harness {
        fn new(surface_id: &str, tenant_id: &str, browser_profile_id: &str) -> Self {
            let (signing_key, verifying_key) = signing_keypair();
            let active_profile_surfaces =
                crate::browser_profile_store::ActiveProfileSurfaces::default();
            active_profile_surfaces.record(
                BrowserSurfaceId::from_string(surface_id.to_string()),
                tenant_id.to_string(),
                crate::browser_profile_store::BrowserProfileId::from_raw_for_test(
                    browser_profile_id,
                ),
            );
            let audit_log_path = std::env::temp_dir().join(format!(
                "kortex-b5-execute-granted-action-test-{}-{}",
                unix_now(),
                surface_id
            ));
            Self {
                runtime: FakeBrowserRuntime::new(),
                active_profile_surfaces,
                redeemed_tracker: RedeemedGrantTracker::new(),
                surface_locks: SurfaceRedeemLocks::new(),
                audit_log_path,
                signing_key,
                verifying_key,
            }
        }

        /// Mints a validly-signed, currently-fresh (real, dynamic
        /// timestamps — never `base_grant`'s fixed 2026-01-01 fixture,
        /// which is already expired relative to real wall-clock time) test
        /// grant whose `canonicalized_parameters_hash` is computed from
        /// `parameters` via the SAME production `canonicalize_and_hash`
        /// this module's own execution path uses — so a test can
        /// deliberately mismatch it by hashing something else, or leave it
        /// consistent, exactly as a real caller's parameters either match
        /// or don't.
        #[allow(clippy::too_many_arguments)]
        fn mint_grant(
            &self,
            grant_id: &str,
            capability_name: &str,
            surface_id: &str,
            tenant_id: &str,
            browser_profile_id: &str,
            navigation_generation: Option<u64>,
            parameters: &serde_json::Value,
        ) -> CapabilityExecutionGrant {
            use time::format_description::well_known::Rfc3339;
            let now = time::OffsetDateTime::now_utc();
            let issued_at = now.format(&Rfc3339).unwrap();
            let expires_at = (now + Duration::from_secs(30)).format(&Rfc3339).unwrap();
            let mut grant = CapabilityExecutionGrant {
                grant_id: grant_id.to_string(),
                tenant_id: tenant_id.to_string(),
                principal_id: "ai-system".to_string(),
                capability_name: capability_name.to_string(),
                browser_profile_id: browser_profile_id.to_string(),
                surface_id: surface_id.to_string(),
                navigation_generation,
                canonicalized_parameters_hash: canonicalize_and_hash(capability_name, parameters),
                issued_at,
                expires_at,
                signature: "00".repeat(64),
            };
            grant.signature = sign(&self.signing_key, &grant);
            grant
        }

        async fn execute(
            &self,
            grant: CapabilityExecutionGrant,
            params: Option<BrowserCapabilityParamsWire>,
        ) -> Result<BrowserGrantExecutionResult, BrowserGrantExecutionError> {
            execute_granted_action(
                &self.runtime,
                &self.verifying_key,
                &self.active_profile_surfaces,
                &self.redeemed_tracker,
                &self.surface_locks,
                &self.audit_log_path,
                grant,
                params,
            )
            .await
        }
    }

    impl Drop for Harness {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.audit_log_path);
        }
    }

    #[tokio::test]
    async fn execute_navigate_success_calls_runtime_and_records_success() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let params_value = navigate_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            "https://example.com/ok",
            30_000,
        );
        let grant = harness.mint_grant(
            "grant-success",
            NAVIGATE_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &params_value,
        );
        let result = harness
            .execute(
                grant,
                Some(BrowserCapabilityParamsWire::Navigate {
                    url: "https://example.com/ok".to_string(),
                    timeout_ms: 30_000,
                }),
            )
            .await;
        assert!(matches!(
            result,
            Ok(BrowserGrantExecutionResult::Success { .. })
        ));
        assert_eq!(
            harness.runtime.navigate_calls.lock().unwrap().as_slice(),
            ["https://example.com/ok"]
        );
    }

    #[tokio::test]
    async fn execute_navigate_rejects_parameter_substitution_before_touching_runtime() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let authorized_params = navigate_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            "https://example.com/authorized",
            30_000,
        );
        let grant = harness.mint_grant(
            "grant-substitution",
            NAVIGATE_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &authorized_params,
        );
        // Redeemed with a DIFFERENT url than what the Grant's own hash
        // authorizes — the central property OD-01 exists to close.
        let result = harness
            .execute(
                grant,
                Some(BrowserCapabilityParamsWire::Navigate {
                    url: "https://attacker.example/evil".to_string(),
                    timeout_ms: 30_000,
                }),
            )
            .await;
        assert!(matches!(
            result,
            Err(BrowserGrantExecutionError::GrantInvalid)
        ));
        assert!(
            harness.runtime.navigate_calls.lock().unwrap().is_empty(),
            "BrowserRuntime::navigate must never be reached when the parameter hash does not match"
        );
    }

    #[tokio::test]
    async fn execute_navigate_without_params_is_rejected() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let params_value = navigate_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            "https://example.com/ok",
            30_000,
        );
        let grant = harness.mint_grant(
            "grant-no-params",
            NAVIGATE_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &params_value,
        );
        let result = harness.execute(grant, None).await;
        assert!(matches!(
            result,
            Err(BrowserGrantExecutionError::ParametersRequired)
        ));
        assert!(harness.runtime.navigate_calls.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn execute_navigate_with_screenshot_shaped_params_is_rejected() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let params_value = navigate_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            "https://example.com/ok",
            30_000,
        );
        let grant = harness.mint_grant(
            "grant-wrong-shape",
            NAVIGATE_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &params_value,
        );
        let result = harness
            .execute(
                grant,
                Some(BrowserCapabilityParamsWire::Screenshot { full_page: false }),
            )
            .await;
        assert!(matches!(
            result,
            Err(BrowserGrantExecutionError::ParametersRequired)
        ));
        assert!(harness.runtime.navigate_calls.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn execute_navigate_policy_denied_propagates_typed_error() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        *harness.runtime.navigate_outcome.lock().unwrap() = Some(NavigationOutcome::PolicyDenied(
            DenyReason::PrivateNetworkAccess {
                classification: crate::browser_policy::NetworkClassification::Loopback,
            },
        ));
        let params_value = navigate_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            "http://127.0.0.1/",
            30_000,
        );
        let grant = harness.mint_grant(
            "grant-denied",
            NAVIGATE_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &params_value,
        );
        let result = harness
            .execute(
                grant,
                Some(BrowserCapabilityParamsWire::Navigate {
                    url: "http://127.0.0.1/".to_string(),
                    timeout_ms: 30_000,
                }),
            )
            .await;
        assert!(matches!(
            result,
            Err(BrowserGrantExecutionError::PolicyDenied { .. })
        ));
    }

    #[tokio::test]
    async fn execute_navigate_failure_propagates_web_error_status() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        *harness.runtime.navigate_outcome.lock().unwrap() = Some(NavigationOutcome::Failed {
            web_error_status: 12029,
        });
        let params_value = navigate_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            "https://example.com/unreachable",
            30_000,
        );
        let grant = harness.mint_grant(
            "grant-failed",
            NAVIGATE_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &params_value,
        );
        let result = harness
            .execute(
                grant,
                Some(BrowserCapabilityParamsWire::Navigate {
                    url: "https://example.com/unreachable".to_string(),
                    timeout_ms: 30_000,
                }),
            )
            .await;
        assert!(matches!(
            result,
            Err(BrowserGrantExecutionError::NavigationFailed {
                web_error_status: 12029
            })
        ));
    }

    #[tokio::test]
    async fn execute_navigate_timeout_when_runtime_never_resolves() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        *harness.runtime.navigate_outcome.lock().unwrap() = None;
        let params_value = navigate_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            "https://example.com/slow",
            100,
        );
        let grant = harness.mint_grant(
            "grant-timeout",
            NAVIGATE_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &params_value,
        );
        let result = harness
            .execute(
                grant,
                Some(BrowserCapabilityParamsWire::Navigate {
                    url: "https://example.com/slow".to_string(),
                    timeout_ms: 100,
                }),
            )
            .await;
        assert!(matches!(result, Err(BrowserGrantExecutionError::Timeout)));
    }

    #[tokio::test]
    async fn execute_screenshot_success_returns_base64() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let params_value = screenshot_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            false,
        );
        let grant = harness.mint_grant(
            "grant-screenshot",
            SCREENSHOT_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &params_value,
        );
        let result = harness
            .execute(
                grant,
                Some(BrowserCapabilityParamsWire::Screenshot { full_page: false }),
            )
            .await;
        match result {
            Ok(BrowserGrantExecutionResult::Screenshot { image_base64, .. }) => {
                assert_eq!(
                    base64::engine::general_purpose::STANDARD
                        .decode(image_base64)
                        .unwrap(),
                    vec![1, 2, 3, 4]
                );
            }
            other => panic!("expected Screenshot result, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn execute_screenshot_full_page_rejected_before_hash_check() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        // Grant minted for full_page=false; redeemed asking for
        // full_page=true — would ALSO fail the hash check, but must be
        // reported as `FullPageNotYetSupported`, never a confusing
        // `GrantInvalid`, for a capability this desktop cannot honor
        // regardless of hash correctness.
        let params_value = screenshot_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            false,
        );
        let grant = harness.mint_grant(
            "grant-full-page",
            SCREENSHOT_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &params_value,
        );
        let result = harness
            .execute(
                grant,
                Some(BrowserCapabilityParamsWire::Screenshot { full_page: true }),
            )
            .await;
        assert!(matches!(
            result,
            Err(BrowserGrantExecutionError::FullPageNotYetSupported)
        ));
    }

    #[tokio::test]
    async fn expired_grant_never_reaches_capability_dispatch() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let params_value = navigate_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            "https://example.com/ok",
            30_000,
        );
        let mut grant = harness.mint_grant(
            "grant-expired",
            NAVIGATE_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &params_value,
        );
        // Overwrite with an already-expired window, then re-sign so
        // signature verification (which runs FIRST) still passes —
        // isolating this test to the expiry check specifically.
        use time::format_description::well_known::Rfc3339;
        let past = time::OffsetDateTime::now_utc() - Duration::from_secs(3600);
        grant.issued_at = (past - Duration::from_secs(60)).format(&Rfc3339).unwrap();
        grant.expires_at = past.format(&Rfc3339).unwrap();
        grant.signature = sign(&harness.signing_key, &grant);

        let result = harness
            .execute(
                grant,
                Some(BrowserCapabilityParamsWire::Navigate {
                    url: "https://example.com/ok".to_string(),
                    timeout_ms: 30_000,
                }),
            )
            .await;
        assert!(matches!(
            result,
            Err(BrowserGrantExecutionError::GrantExpired)
        ));
        assert!(harness.runtime.navigate_calls.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn unregistered_surface_never_reaches_capability_dispatch() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        harness
            .runtime
            .surface_exists
            .store(false, AtomicOrdering::SeqCst);
        let params_value = navigate_parameters_value(
            &grant_with(|g| {
                g.surface_id = "surface-1".to_string();
                g.browser_profile_id = "profile-1".to_string();
            }),
            "https://example.com/ok",
            30_000,
        );
        let grant = harness.mint_grant(
            "grant-no-surface",
            NAVIGATE_CAPABILITY,
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &params_value,
        );
        let result = harness
            .execute(
                grant,
                Some(BrowserCapabilityParamsWire::Navigate {
                    url: "https://example.com/ok".to_string(),
                    timeout_ms: 30_000,
                }),
            )
            .await;
        assert!(matches!(
            result,
            Err(BrowserGrantExecutionError::SurfaceNotFound)
        ));
        assert!(harness.runtime.navigate_calls.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn unrecognized_capability_still_returns_not_yet_enabled() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        // `navigation_generation: None` deliberately — this test is only
        // about capability dispatch, not generation binding (which every
        // OTHER test above already covers); claiming `Some(_)` here would
        // exercise the (correct, shared) generation-mismatch check instead
        // of reaching the dispatch match this test targets, since the
        // fake runtime's own generation defaults to 0.
        let grant = harness.mint_grant(
            "grant-click",
            "kortex.browser.click",
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &serde_json::json!({"target": {"browser_profile_id": "profile-1", "surface_id": "surface-1", "navigation_generation": null}, "selector": {"role": "button"}}),
        );
        let result = harness.execute(grant, None).await;
        assert!(matches!(
            result,
            Ok(BrowserGrantExecutionResult::NotYetEnabled { .. })
        ));
    }

    /// Proves the per-surface lock genuinely serializes two concurrent
    /// redeem attempts for the SAME surface across the FULL navigate-and-
    /// wait duration — not merely across the pre-execution checks — the
    /// property `browser_b5_5_architecture_gate.md` T11 requires. A second
    /// redemption must not even ARM its own navigation waiter until the
    /// first has fully resolved and released the surface lock.
    #[tokio::test]
    async fn concurrent_redemptions_on_same_surface_are_serialized() {
        let runtime = StdArc::new(FakeBrowserRuntime::manual());
        let active_profile_surfaces =
            StdArc::new(crate::browser_profile_store::ActiveProfileSurfaces::default());
        active_profile_surfaces.record(
            BrowserSurfaceId::from_string("surface-1".to_string()),
            "tenant-a".to_string(),
            crate::browser_profile_store::BrowserProfileId::from_raw_for_test("profile-1"),
        );
        let redeemed_tracker = StdArc::new(RedeemedGrantTracker::new());
        let surface_locks = StdArc::new(SurfaceRedeemLocks::new());
        let audit_log_path = StdArc::new(
            std::env::temp_dir().join(format!("kortex-b5-concurrency-test-{}", unix_now())),
        );
        let (signing_key, verifying_key) = signing_keypair();
        let verifying_key = StdArc::new(verifying_key);

        let mint = |grant_id: &str, url: &str| {
            let params_value = navigate_parameters_value(
                &grant_with(|g| {
                    g.surface_id = "surface-1".to_string();
                    g.browser_profile_id = "profile-1".to_string();
                }),
                url,
                30_000,
            );
            use time::format_description::well_known::Rfc3339;
            let now = time::OffsetDateTime::now_utc();
            let mut grant = CapabilityExecutionGrant {
                grant_id: grant_id.to_string(),
                tenant_id: "tenant-a".to_string(),
                principal_id: "ai-system".to_string(),
                capability_name: NAVIGATE_CAPABILITY.to_string(),
                browser_profile_id: "profile-1".to_string(),
                surface_id: "surface-1".to_string(),
                navigation_generation: None,
                canonicalized_parameters_hash: canonicalize_and_hash(
                    NAVIGATE_CAPABILITY,
                    &params_value,
                ),
                issued_at: now.format(&Rfc3339).unwrap(),
                expires_at: (now + Duration::from_secs(30)).format(&Rfc3339).unwrap(),
                signature: "00".repeat(64),
            };
            grant.signature = sign(&signing_key, &grant);
            grant
        };

        let grant_a = mint("grant-a", "https://example.com/a");
        let grant_b = mint("grant-b", "https://example.com/b");

        let (runtime_a, profiles_a, tracker_a, locks_a, audit_a, key_a) = (
            runtime.clone(),
            active_profile_surfaces.clone(),
            redeemed_tracker.clone(),
            surface_locks.clone(),
            audit_log_path.clone(),
            verifying_key.clone(),
        );
        let handle_a = tokio::spawn(async move {
            execute_granted_action(
                &*runtime_a,
                &key_a,
                &profiles_a,
                &tracker_a,
                &locks_a,
                &audit_a,
                grant_a,
                Some(BrowserCapabilityParamsWire::Navigate {
                    url: "https://example.com/a".to_string(),
                    timeout_ms: 30_000,
                }),
            )
            .await
        });

        // Wait until A has passed every pre-execution check and armed its
        // OWN navigation waiter (i.e. it is now the one holding the
        // surface lock, blocked on `runtime.navigate`'s own outcome).
        runtime.armed_notify.notified().await;
        assert_eq!(runtime.pending_navigate_senders.lock().unwrap().len(), 1);

        let (runtime_b, profiles_b, tracker_b, locks_b, audit_b, key_b) = (
            runtime.clone(),
            active_profile_surfaces.clone(),
            redeemed_tracker.clone(),
            surface_locks.clone(),
            audit_log_path.clone(),
            verifying_key.clone(),
        );
        let handle_b = tokio::spawn(async move {
            execute_granted_action(
                &*runtime_b,
                &key_b,
                &profiles_b,
                &tracker_b,
                &locks_b,
                &audit_b,
                grant_b,
                Some(BrowserCapabilityParamsWire::Navigate {
                    url: "https://example.com/b".to_string(),
                    timeout_ms: 30_000,
                }),
            )
            .await
        });

        // Give B every opportunity to (incorrectly) race ahead if the
        // surface lock were NOT held across A's own await.
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert_eq!(
            runtime.pending_navigate_senders.lock().unwrap().len(),
            1,
            "B must still be blocked behind A's per-surface lock, not yet arming its own waiter"
        );

        // Resolve A — releases the surface lock.
        runtime
            .pending_navigate_senders
            .lock()
            .unwrap()
            .remove(0)
            .send(NavigationOutcome::Success)
            .unwrap();
        let result_a = handle_a.await.unwrap();
        assert!(matches!(
            result_a,
            Ok(BrowserGrantExecutionResult::Success { .. })
        ));

        // NOW B should be able to proceed and arm its own waiter.
        runtime.armed_notify.notified().await;
        assert_eq!(runtime.pending_navigate_senders.lock().unwrap().len(), 1);
        runtime
            .pending_navigate_senders
            .lock()
            .unwrap()
            .remove(0)
            .send(NavigationOutcome::Success)
            .unwrap();
        let result_b = handle_b.await.unwrap();
        assert!(matches!(
            result_b,
            Ok(BrowserGrantExecutionResult::Success { .. })
        ));

        assert_eq!(
            runtime.navigate_calls.lock().unwrap().as_slice(),
            ["https://example.com/a", "https://example.com/b"],
            "navigate() must have been called for A, fully resolved, THEN called for B — never interleaved"
        );

        let _ = std::fs::remove_file(&*audit_log_path);
    }
}
