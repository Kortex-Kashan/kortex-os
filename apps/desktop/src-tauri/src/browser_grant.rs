//! Browser-B5.4: desktop-side verification and single-use tracking for a
//! backend-minted Capability Execution Grant (Browser-B5.3,
//! `backend/src/kortex/engines/browser/grant.py`).
//!
//! **What this module's pure verification functions deliberately do NOT
//! do**: invoke `BrowserRuntime`'s mutating methods. `browser_execute_granted_action`
//! (this file's own Tauri command, below) is the orchestration entry point
//! — it calls `mint`/`verify_signature`/`verify_expiry`/
//! `RedeemedGrantTracker`, then only `BrowserRuntime`'s query-only methods
//! (`surface_exists`/`navigation_generation`) to check live binding. In the
//! B5.0-B5.4 foundation phase it stops there — it never calls
//! `BrowserRuntime::navigate`/`.reload`/etc. See
//! `docs/architecture/browser_b5_architecture_gate.md` §6 and the
//! B5.0-B5.4 authorization's own "B4 POLICY INVARIANT" section: once a
//! later phase does wire real execution, it still reaches `BrowserRuntime`
//! through the exact same trait methods a human click already goes
//! through, so B4's `BrowserPolicyEngine` remains the final, unbypassable
//! gate regardless of anything decided in this file.
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
use std::time::{SystemTime, UNIX_EPOCH};

use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};

use crate::ipc::{forward_capability_request, IpcCapabilityRequest, IpcClientState};

const GRANT_VERIFICATION_KEY_CAPABILITY: &str = "kortex.browser.grant_verification_key";

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
    InternalError { message: String },
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

/// Successful redemption result — deliberately reports only that
/// verification passed and execution is not yet enabled, never a
/// `BrowserRuntime` result of any kind (there isn't one — B5.0-B5.4 mints/
/// verifies Grants, it does not execute them; see this file's own module
/// doc and `lib.rs::browser_execute_granted_action`'s own doc comment on
/// why).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BrowserGrantExecutionResult {
    pub capability_name: String,
    pub status: &'static str,
}

/// Browser-B5.4: the single AI-originated redeem command — never
/// repurposes a human-facing command (`browser_navigate`/etc.), never
/// accepts a raw URL/script/selector-free instruction. Verifies, in
/// order, every one of the independent checks §6/§8/§17 of the B5
/// architecture gate require, failing closed on the first one that
/// doesn't hold:
///
/// 1. Grant signature (against the cached backend public key — never a
///    key the Grant itself supplies).
/// 2. Grant expiry (`[issued_at, expires_at)`).
/// 3. Single-use (`RedeemedGrantTracker` — a second redemption of the
///    identical `grant_id` is rejected even if everything else about it
///    is still valid).
/// 4. Live surface existence (`BrowserRuntime::surface_exists` — re-checked
///    now, never trusted from whatever was true when the Grant was
///    minted).
/// 5. Live tenant/profile binding (`ActiveProfileSurfaces::lookup` — the
///    Grant's own `tenant_id`/`browser_profile_id` claims are compared
///    against this surface's real, current binding, never trusted alone).
/// 6. Live navigation generation, if the Grant claims one (`BrowserRuntime::
///    navigation_generation` — a mismatch means the page has navigated
///    since the Grant was minted; fails as `StaleReference`).
///
/// All of the above run while holding `SurfaceRedeemLocks`' per-surface
/// lock, so two concurrent redeem attempts for the same surface can never
/// interleave their checks.
///
/// **What happens after every check passes, in this phase**: nothing
/// browser-visible. See this module's own doc comment and the B5.0-B5.4
/// authorization's explicit "DO NOT implement browser.navigate execution"
/// (and the identical instruction for every other capability) — turning on
/// a real `BrowserRuntime` call per `capability_name` is Browser-B5.5+'s
/// job. Returning `NotYetEnabled` here, for a Grant that passed every
/// security check above, is a deliberate, disclosed placeholder — not a
/// silent no-op a caller could mistake for success.
#[tauri::command]
pub async fn browser_execute_granted_action(
    runtime_state: tauri::State<'_, crate::browser_runtime::BrowserRuntimeState>,
    ipc_state: tauri::State<'_, std::sync::Arc<IpcClientState>>,
    active_profile_surfaces: tauri::State<'_, crate::browser_profile_store::ActiveProfileSurfaces>,
    grant_key_cache: tauri::State<'_, GrantVerificationKeyCache>,
    redeemed_tracker: tauri::State<'_, RedeemedGrantTracker>,
    surface_locks: tauri::State<'_, SurfaceRedeemLocks>,
    grant: CapabilityExecutionGrant,
) -> Result<BrowserGrantExecutionResult, BrowserGrantExecutionError> {
    let public_key = grant_key_cache
        .get_or_fetch(&ipc_state)
        .await
        .map_err(|message| BrowserGrantExecutionError::InternalError { message })?;
    if let Err(reason) = verify_signature(&grant, &public_key) {
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
        return Err(reason.into());
    }
    verify_expiry(&grant)?;
    redeemed_tracker.try_mark_redeemed(&grant.grant_id)?;

    let surface_lock = surface_locks.lock_for(&grant.surface_id);
    let _guard = surface_lock.lock().await;

    let surface_id =
        crate::browser_runtime::BrowserSurfaceId::from_string(grant.surface_id.clone());
    if !runtime_state.0.surface_exists(&surface_id) {
        return Err(BrowserGrantExecutionError::SurfaceNotFound);
    }

    match active_profile_surfaces.lookup(&surface_id) {
        None => return Err(BrowserGrantExecutionError::ProfileNotFound),
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
                return Err(BrowserGrantExecutionError::ProfileNotFound);
            }
        }
    }

    if let Some(claimed_generation) = grant.navigation_generation {
        let live_generation = runtime_state
            .0
            .navigation_generation(&surface_id)
            .map_err(|_| BrowserGrantExecutionError::SurfaceNotFound)?;
        if live_generation != claimed_generation {
            return Err(BrowserGrantExecutionError::StaleReference);
        }
    }

    Ok(BrowserGrantExecutionResult {
        capability_name: grant.capability_name.clone(),
        status: "not_yet_enabled",
    })
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

    fn hex_encode(bytes: &[u8]) -> String {
        bytes.iter().map(|b| format!("{b:02x}")).collect()
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
}
