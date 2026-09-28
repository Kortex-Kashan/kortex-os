//! Browser Completion Program (B6): the desktop half of the AI <-> Browser
//! execution bridge.
//!
//! The backend pauses an agent task after a `kortex.browser.*` capability
//! mints a Capability Execution Grant, then announces `browser.grant.pending`
//! on the authenticated `/events/stream` relay (`events.rs`). That event is a
//! notification only — identifiers, never the Grant or its parameters, and
//! never an authority. This module, per notification:
//!
//! 1. **Ownership** — proceeds only if THIS desktop has the named surface
//!    live and bound to the named tenant/profile. Another desktop of the same
//!    user ignores it and never claims, so it can never report a misleading
//!    `SurfaceNotFound` for an action the owning desktop really performed.
//! 2. **Verification key** — fetched BEFORE claiming: if it is unavailable
//!    nothing is claimed, so the backend can later say with certainty that
//!    the action was never executed.
//! 3. **Claim** — `kortex.ai.agent.browser_execution.claim`, the only way to
//!    obtain the Grant and its execution parameters; authenticated, owner-
//!    checked, and single-use on the backend.
//! 4. **Execute** — through the unchanged `execute_granted_action`
//!    (`browser_grant.rs`), which independently re-verifies signature,
//!    expiry, single-use, surface/tenant/profile binding, navigation
//!    generation and the parameter hash before B4 policy / the bounded UIA
//!    pool ever run. Nothing here relaxes any of those checks.
//! 5. **Report** — `kortex.browser.report_execution` with the typed outcome.
//!    Only transport failures are retried, and a retry re-sends the SAME
//!    already-computed outcome; the action itself is never re-executed.
//!
//! Bounded: at most `MAX_IN_FLIGHT` notifications are processed at once;
//! beyond that a notification is dropped (its Grant then expires unexecuted,
//! which the backend reports as not executed) — never queued without limit.

use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use base64::Engine;
use serde::Deserialize;
use serde_json::{json, Map, Value};
use tauri::{AppHandle, Manager};

use crate::browser_grant::{
    execute_granted_action, verify_signature, BrowserCapabilityParamsWire,
    BrowserGrantExecutionError, BrowserGrantExecutionResult, CapabilityExecutionGrant,
    GrantVerificationKeyCache, RedeemedGrantTracker, SurfaceRedeemLocks,
};
use crate::browser_profile_store::ActiveProfileSurfaces;
use crate::browser_runtime::{
    BrowserRuntime, BrowserRuntimeState, BrowserSurfaceId, PolicyAuditLogPath,
};
use crate::browser_uia::UiaWorkerPool;
use crate::ipc::{
    forward_capability_request, IpcCapabilityRequest, IpcClientState, IpcResultEnvelope,
};

pub const PENDING_TOPIC: &str = "browser.grant.pending";
const CLAIM_CAPABILITY: &str = "kortex.ai.agent.browser_execution.claim";
const REPORT_CAPABILITY: &str = "kortex.browser.report_execution";
const MAX_IN_FLIGHT: usize = 8;
const REPORT_ATTEMPTS: u32 = 3;
const REPORT_INITIAL_BACKOFF: Duration = Duration::from_millis(250);

/// Identifiers from one `browser.grant.pending` notification.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub(crate) struct PendingNotice {
    pub tenant_id: String,
    pub task_id: String,
    pub grant_id: String,
    pub browser_profile_id: String,
    pub surface_id: String,
}

/// Extracts a notice from a relayed `/events/stream` frame, or `None` for
/// any other topic or any malformed payload.
pub(crate) fn parse_pending_notice(frame: &Value) -> Option<PendingNotice> {
    if frame.get("topic")?.as_str()? != PENDING_TOPIC {
        return None;
    }
    let notice: PendingNotice = serde_json::from_value(frame.get("payload")?.clone()).ok()?;
    let fields = [
        &notice.tenant_id,
        &notice.task_id,
        &notice.grant_id,
        &notice.browser_profile_id,
        &notice.surface_id,
    ];
    if fields.iter().any(|f| f.is_empty()) {
        return None;
    }
    Some(notice)
}

/// `/capabilities/invoke` returns a dict-valued handler result AS the
/// payload, and wraps any other result as `{"result": ...}`
/// (`backend/src/kortex/api/main.py::_invoke`). Reads the handler's own
/// object under either shape.
pub(crate) fn capability_result_object(payload: Option<&Value>) -> Option<&Map<String, Value>> {
    let object = payload?.as_object()?;
    match object.get("result").and_then(Value::as_object) {
        Some(inner) if object.len() == 1 => Some(inner),
        _ => Some(object),
    }
}

/// The report's `execution_outcome` for one execution result. A screenshot
/// is reported by size only: its pixels never leave this process (the AI
/// reasoning context is text-only, and a captured image may show on-screen
/// credentials that no text scrubber can redact).
pub(crate) fn execution_outcome_json(
    result: &Result<BrowserGrantExecutionResult, BrowserGrantExecutionError>,
) -> Value {
    match result {
        Ok(BrowserGrantExecutionResult::Screenshot {
            capability_name,
            image_base64,
        }) => {
            let image_byte_length = base64::engine::general_purpose::STANDARD
                .decode(image_base64)
                .map(|bytes| bytes.len())
                .unwrap_or(0);
            json!({"result": {
                "status": "screenshot",
                "capability_name": capability_name,
                "image_byte_length": image_byte_length,
            }})
        }
        Ok(other) => json!({ "result": other }),
        Err(error) => json!({ "error": error }),
    }
}

fn grant_invalid_outcome() -> Value {
    json!({"error": {"kind": "grantInvalid"}})
}

/// Retry only when no usable response arrived (backend unreachable) or the
/// backend itself failed (5xx). A 4xx means the report was received and
/// refused; re-sending it cannot change that.
fn is_transport_failure(envelope: &IpcResultEnvelope) -> bool {
    envelope.status != "SUCCESS" && envelope.http_status.map_or(true, |status| status >= 500)
}

/// Recognizable in the backend's dispatch audit (`request_id`) as a bridge
/// call — mirrors `browser_grant.rs::uuid_like_request_id`'s own convention.
fn bridge_request_id() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("browser-bridge-{nanos:x}")
}

type TransportFuture<'a> = Pin<Box<dyn Future<Output = IpcResultEnvelope> + Send + 'a>>;

/// The two backend calls this bridge makes, abstracted so the full flow can
/// be exercised against a fake backend in tests.
pub(crate) trait BridgeTransport: Send + Sync {
    fn invoke<'a>(&'a self, capability_name: &'a str, parameters: Value) -> TransportFuture<'a>;
}

struct IpcBridgeTransport {
    ipc: Arc<IpcClientState>,
}

impl BridgeTransport for IpcBridgeTransport {
    fn invoke<'a>(&'a self, capability_name: &'a str, parameters: Value) -> TransportFuture<'a> {
        Box::pin(async move {
            forward_capability_request(
                &self.ipc,
                IpcCapabilityRequest {
                    request_id: bridge_request_id(),
                    capability_name: capability_name.to_string(),
                    parameters,
                    correlation_id: None,
                    idempotency_key: None,
                    timeout_ms: None,
                },
            )
            .await
        })
    }
}

/// Everything `execute_granted_action` needs, borrowed from Tauri-managed
/// state in production and from a test harness in tests.
pub(crate) struct BridgeDeps<'a> {
    pub runtime: &'a dyn BrowserRuntime,
    pub active_profile_surfaces: &'a ActiveProfileSurfaces,
    pub redeemed_tracker: &'a RedeemedGrantTracker,
    pub surface_locks: &'a SurfaceRedeemLocks,
    pub audit_log_path: &'a std::path::Path,
    pub uia_pool: &'a Arc<UiaWorkerPool>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum BridgeOutcome {
    /// This desktop does not own the named surface; nothing was claimed.
    NotOurs,
    /// The backend refused the claim (expired, already claimed, not ours).
    ClaimRefused,
    /// Executed (or refused by desktop-side verification) and reported.
    Reported,
    /// Executed but the report could not be delivered; the backend will
    /// classify the outcome as unknown once the report deadline passes.
    ReportUndelivered,
}

pub(crate) struct BridgeRun {
    pub outcome: BridgeOutcome,
    /// The claimed Grant failed signature verification against the cached
    /// key — most plausibly a backend restart rotated its signing key; the
    /// production caller invalidates the cache so the next Grant refetches.
    pub signature_rejected: bool,
}

pub(crate) fn owns_surface(deps: &BridgeDeps<'_>, notice: &PendingNotice) -> bool {
    let surface_id = BrowserSurfaceId::from_string(notice.surface_id.clone());
    if !deps.runtime.surface_exists(&surface_id) {
        return false;
    }
    matches!(
        deps.active_profile_surfaces.lookup(&surface_id),
        Some((tenant_id, profile_id))
            if tenant_id == notice.tenant_id && profile_id.as_str() == notice.browser_profile_id
    )
}

pub(crate) async fn process_pending_notice(
    deps: &BridgeDeps<'_>,
    transport: &dyn BridgeTransport,
    verification_key: &[u8; 32],
    notice: &PendingNotice,
    report_backoff: Duration,
) -> BridgeRun {
    let not_reported = |outcome| BridgeRun {
        outcome,
        signature_rejected: false,
    };
    if !owns_surface(deps, notice) {
        return not_reported(BridgeOutcome::NotOurs);
    }

    let claim = transport
        .invoke(
            CLAIM_CAPABILITY,
            json!({"task_id": notice.task_id, "grant_id": notice.grant_id}),
        )
        .await;
    if claim.status != "SUCCESS" {
        return not_reported(BridgeOutcome::ClaimRefused);
    }

    let claimed = capability_result_object(claim.payload.as_ref());
    let grant = claimed
        .and_then(|c| c.get("grant"))
        .and_then(|g| serde_json::from_value::<CapabilityExecutionGrant>(g.clone()).ok());
    let params = claimed
        .and_then(|c| c.get("execution_parameters"))
        .and_then(|p| serde_json::from_value::<BrowserCapabilityParamsWire>(p.clone()).ok());

    let mut signature_rejected = false;
    let outcome = match grant {
        // The claim succeeded, so the backend now waits for a report:
        // anything unusable in it is reported as an invalid Grant rather
        // than left to expire as "outcome unknown".
        Some(grant)
            if grant.grant_id == notice.grant_id
                && grant.tenant_id == notice.tenant_id
                && grant.surface_id == notice.surface_id
                && grant.browser_profile_id == notice.browser_profile_id =>
        {
            signature_rejected = verify_signature(&grant, verification_key).is_err();
            let result = execute_granted_action(
                deps.runtime,
                verification_key,
                deps.active_profile_surfaces,
                deps.redeemed_tracker,
                deps.surface_locks,
                deps.audit_log_path,
                deps.uia_pool,
                grant,
                params,
            )
            .await;
            execution_outcome_json(&result)
        }
        _ => grant_invalid_outcome(),
    };

    let report = json!({
        "task_id": notice.task_id,
        "grant_id": notice.grant_id,
        "execution_outcome": outcome,
    });
    let mut backoff = report_backoff;
    for attempt in 1..=REPORT_ATTEMPTS {
        let envelope = transport.invoke(REPORT_CAPABILITY, report.clone()).await;
        if !is_transport_failure(&envelope) {
            return BridgeRun {
                outcome: if envelope.status == "SUCCESS" {
                    BridgeOutcome::Reported
                } else {
                    BridgeOutcome::ReportUndelivered
                },
                signature_rejected,
            };
        }
        if attempt < REPORT_ATTEMPTS {
            tokio::time::sleep(backoff).await;
            backoff *= 2;
        }
    }
    BridgeRun {
        outcome: BridgeOutcome::ReportUndelivered,
        signature_rejected,
    }
}

/// Bounds concurrent notification processing (see module doc).
#[derive(Default)]
pub struct BrowserBridgeState {
    in_flight: AtomicUsize,
}

struct InFlightPermit(Arc<BrowserBridgeState>);

impl BrowserBridgeState {
    fn try_acquire(self: &Arc<Self>) -> Option<InFlightPermit> {
        let mut current = self.in_flight.load(Ordering::SeqCst);
        loop {
            if current >= MAX_IN_FLIGHT {
                return None;
            }
            match self.in_flight.compare_exchange(
                current,
                current + 1,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => return Some(InFlightPermit(self.clone())),
                Err(observed) => current = observed,
            }
        }
    }
}

impl Drop for InFlightPermit {
    fn drop(&mut self) {
        self.0.in_flight.fetch_sub(1, Ordering::SeqCst);
    }
}

/// Entry point from the event relay: a no-op for every frame that is not a
/// well-formed `browser.grant.pending` notification.
pub fn handle_relayed_event(app: &AppHandle, frame: &Value) {
    let Some(notice) = parse_pending_notice(frame) else {
        return;
    };
    let Some(state) = app.try_state::<Arc<BrowserBridgeState>>() else {
        return;
    };
    let Some(permit) = state.inner().try_acquire() else {
        return;
    };
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let _permit = permit;
        run_notice(&app, &notice).await;
    });
}

async fn run_notice(app: &AppHandle, notice: &PendingNotice) {
    let runtime_state = app.state::<BrowserRuntimeState>();
    let ipc_state = app.state::<Arc<IpcClientState>>();
    let active_profile_surfaces = app.state::<ActiveProfileSurfaces>();
    let grant_key_cache = app.state::<GrantVerificationKeyCache>();
    let redeemed_tracker = app.state::<RedeemedGrantTracker>();
    let surface_locks = app.state::<SurfaceRedeemLocks>();
    let audit_log_path = app.state::<PolicyAuditLogPath>();
    let uia_pool = app.state::<Arc<UiaWorkerPool>>();

    let deps = BridgeDeps {
        runtime: &*runtime_state.0,
        active_profile_surfaces: &active_profile_surfaces,
        redeemed_tracker: &redeemed_tracker,
        surface_locks: &surface_locks,
        audit_log_path: &audit_log_path.0,
        uia_pool: &uia_pool,
    };
    // Cheap local check before any network call: a notification for a
    // surface this desktop does not own must not even fetch a key.
    if !owns_surface(&deps, notice) {
        return;
    }
    let Ok(verification_key) = grant_key_cache.get_or_fetch(&ipc_state).await else {
        return;
    };
    let transport = IpcBridgeTransport {
        ipc: ipc_state.inner().clone(),
    };
    let run = process_pending_notice(
        &deps,
        &transport,
        &verification_key,
        notice,
        REPORT_INITIAL_BACKOFF,
    )
    .await;
    if run.signature_rejected {
        grant_key_cache.invalidate();
    }
    if run.outcome == BridgeOutcome::ReportUndelivered {
        // Identifiers only — never the outcome's content.
        eprintln!(
            "KORTEX: Browser execution outcome for task {} (grant {}) could not be reported; \
             the backend will treat it as unknown.",
            notice.task_id, notice.grant_id
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    use crate::browser_grant::tests::Harness;

    fn notice() -> PendingNotice {
        PendingNotice {
            tenant_id: "tenant-a".to_string(),
            task_id: "task-1".to_string(),
            grant_id: "grant-1".to_string(),
            browser_profile_id: "profile-1".to_string(),
            surface_id: "surface-1".to_string(),
        }
    }

    fn envelope(
        status: &str,
        payload: Option<Value>,
        http_status: Option<u16>,
    ) -> IpcResultEnvelope {
        IpcResultEnvelope {
            request_id: "r".to_string(),
            correlation_id: "c".to_string(),
            status: status.to_string(),
            payload,
            errors: vec![],
            warnings: vec![],
            execution_duration_ms: 0.0,
            http_status,
        }
    }

    /// A fake backend: answers the claim with a scripted envelope and
    /// records every report, answering reports from a script too.
    struct FakeBackend {
        claim_response: IpcResultEnvelope,
        report_responses: Mutex<Vec<IpcResultEnvelope>>,
        calls: Mutex<Vec<(String, Value)>>,
    }

    impl FakeBackend {
        fn new(claim_response: IpcResultEnvelope) -> Self {
            Self {
                claim_response,
                report_responses: Mutex::new(vec![]),
                calls: Mutex::new(vec![]),
            }
        }

        fn reports(&self) -> Vec<Value> {
            self.calls
                .lock()
                .unwrap()
                .iter()
                .filter(|(name, _)| name == REPORT_CAPABILITY)
                .map(|(_, params)| params.clone())
                .collect()
        }

        fn claims(&self) -> usize {
            self.calls
                .lock()
                .unwrap()
                .iter()
                .filter(|(name, _)| name == CLAIM_CAPABILITY)
                .count()
        }
    }

    impl BridgeTransport for FakeBackend {
        fn invoke<'a>(
            &'a self,
            capability_name: &'a str,
            parameters: Value,
        ) -> TransportFuture<'a> {
            self.calls
                .lock()
                .unwrap()
                .push((capability_name.to_string(), parameters));
            let response = if capability_name == CLAIM_CAPABILITY {
                self.claim_response.clone()
            } else {
                let mut scripted = self.report_responses.lock().unwrap();
                if scripted.is_empty() {
                    envelope("SUCCESS", Some(json!({"accepted": true})), Some(200))
                } else {
                    scripted.remove(0)
                }
            };
            Box::pin(async move { response })
        }
    }

    fn deps(harness: &Harness) -> BridgeDeps<'_> {
        BridgeDeps {
            runtime: &harness.runtime,
            active_profile_surfaces: &harness.active_profile_surfaces,
            redeemed_tracker: &harness.redeemed_tracker,
            surface_locks: &harness.surface_locks,
            audit_log_path: &harness.audit_log_path,
            uia_pool: &harness.uia_pool,
        }
    }

    /// A claim response exactly as the backend returns it: a dict-valued
    /// handler result is the envelope payload itself.
    fn navigate_claim(harness: &Harness, grant_id: &str) -> IpcResultEnvelope {
        let params = json!({"capability": "kortex.browser.navigate", "url": "https://example.com/", "timeout_ms": 30000});
        let hashed = json!({
            "target": {"browser_profile_id": "profile-1", "surface_id": "surface-1", "navigation_generation": null},
            "url": "https://example.com/",
            "timeout_ms": 30000,
        });
        let grant = harness.mint_grant(
            grant_id,
            "kortex.browser.navigate",
            "surface-1",
            "tenant-a",
            "profile-1",
            None,
            &hashed,
        );
        envelope(
            "SUCCESS",
            Some(json!({
                "task_id": "task-1",
                "grant": crate::browser_grant::tests::grant_json(&grant),
                "execution_parameters": params,
                "report_deadline": "2099-01-01T00:00:00+00:00",
            })),
            Some(200),
        )
    }

    #[test]
    fn parses_only_well_formed_pending_notices() {
        let frame = json!({"topic": PENDING_TOPIC, "payload": {
            "tenant_id": "tenant-a", "task_id": "task-1", "grant_id": "grant-1",
            "browser_profile_id": "profile-1", "surface_id": "surface-1",
            "audience_principal_id": "alice", "capability_name": "kortex.browser.read",
        }});
        assert_eq!(parse_pending_notice(&frame), Some(notice()));
        assert_eq!(
            parse_pending_notice(&json!({"topic": "other", "payload": {}})),
            None
        );
        let missing = json!({"topic": PENDING_TOPIC, "payload": {"tenant_id": "tenant-a"}});
        assert_eq!(parse_pending_notice(&missing), None);
        let blank = json!({"topic": PENDING_TOPIC, "payload": {
            "tenant_id": "", "task_id": "t", "grant_id": "g", "browser_profile_id": "p", "surface_id": "s",
        }});
        assert_eq!(parse_pending_notice(&blank), None);
    }

    #[test]
    fn capability_result_object_reads_both_backend_payload_shapes() {
        let direct = json!({"public_key_hex": "ab", "algorithm": "ed25519"});
        assert_eq!(
            capability_result_object(Some(&direct))
                .unwrap()
                .get("public_key_hex"),
            Some(&json!("ab"))
        );
        let wrapped = json!({"result": {"public_key_hex": "cd"}});
        assert_eq!(
            capability_result_object(Some(&wrapped))
                .unwrap()
                .get("public_key_hex"),
            Some(&json!("cd"))
        );
        assert!(capability_result_object(None).is_none());
    }

    #[test]
    fn screenshot_outcome_reports_size_never_pixels() {
        let result = Ok(BrowserGrantExecutionResult::Screenshot {
            capability_name: "kortex.browser.screenshot".to_string(),
            image_base64: base64::engine::general_purpose::STANDARD.encode([1u8, 2, 3, 4, 5]),
        });
        let outcome = execution_outcome_json(&result);
        assert_eq!(outcome["result"]["status"], "screenshot");
        assert_eq!(outcome["result"]["image_byte_length"], 5);
        assert!(outcome["result"].get("image_base64").is_none());
    }

    #[test]
    fn error_outcome_carries_the_desktop_error_kind() {
        let outcome = execution_outcome_json(&Err(BrowserGrantExecutionError::StaleReference));
        assert_eq!(outcome, json!({"error": {"kind": "staleReference"}}));
    }

    #[test]
    fn only_transport_failures_are_retryable() {
        assert!(is_transport_failure(&envelope("FAILURE", None, None)));
        assert!(is_transport_failure(&envelope("FAILURE", None, Some(503))));
        assert!(!is_transport_failure(&envelope("FAILURE", None, Some(403))));
        assert!(!is_transport_failure(&envelope("SUCCESS", None, Some(200))));
    }

    #[tokio::test]
    async fn a_surface_this_desktop_does_not_own_is_never_claimed() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let backend = FakeBackend::new(navigate_claim(&harness, "grant-1"));
        let mut foreign = notice();
        foreign.browser_profile_id = "someone-elses-profile".to_string();

        let run = process_pending_notice(
            &deps(&harness),
            &backend,
            &harness.verifying_key,
            &foreign,
            Duration::ZERO,
        )
        .await;

        assert_eq!(run.outcome, BridgeOutcome::NotOurs);
        assert_eq!(backend.claims(), 0);
        assert!(backend.reports().is_empty());
    }

    #[tokio::test]
    async fn a_refused_claim_executes_nothing_and_reports_nothing() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let backend = FakeBackend::new(envelope("FAILURE", None, Some(409)));

        let run = process_pending_notice(
            &deps(&harness),
            &backend,
            &harness.verifying_key,
            &notice(),
            Duration::ZERO,
        )
        .await;

        assert_eq!(run.outcome, BridgeOutcome::ClaimRefused);
        assert!(harness.runtime_navigate_calls().is_empty());
        assert!(backend.reports().is_empty());
    }

    #[tokio::test]
    async fn claimed_grant_executes_once_and_reports_the_real_outcome() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let backend = FakeBackend::new(navigate_claim(&harness, "grant-1"));

        let run = process_pending_notice(
            &deps(&harness),
            &backend,
            &harness.verifying_key,
            &notice(),
            Duration::ZERO,
        )
        .await;

        assert_eq!(run.outcome, BridgeOutcome::Reported);
        assert!(!run.signature_rejected);
        assert_eq!(
            harness.runtime_navigate_calls(),
            vec!["https://example.com/".to_string()]
        );
        let reports = backend.reports();
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0]["task_id"], "task-1");
        assert_eq!(reports[0]["grant_id"], "grant-1");
        assert_eq!(
            reports[0]["execution_outcome"],
            json!({"result": {"status": "success", "capability_name": "kortex.browser.navigate"}})
        );
    }

    #[tokio::test]
    async fn a_lost_report_is_resent_without_re_executing_the_action() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let backend = FakeBackend::new(navigate_claim(&harness, "grant-1"));
        backend.report_responses.lock().unwrap().extend([
            envelope("FAILURE", None, None),
            envelope("FAILURE", None, Some(502)),
        ]);

        let run = process_pending_notice(
            &deps(&harness),
            &backend,
            &harness.verifying_key,
            &notice(),
            Duration::ZERO,
        )
        .await;

        assert_eq!(run.outcome, BridgeOutcome::Reported);
        assert_eq!(
            harness.runtime_navigate_calls().len(),
            1,
            "the action must run exactly once"
        );
        let reports = backend.reports();
        assert_eq!(reports.len(), 3);
        assert!(
            reports.iter().all(|r| r == &reports[0]),
            "every retry re-sends the same outcome"
        );
    }

    #[tokio::test]
    async fn a_refused_report_is_not_retried() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let backend = FakeBackend::new(navigate_claim(&harness, "grant-1"));
        backend
            .report_responses
            .lock()
            .unwrap()
            .push(envelope("FAILURE", None, Some(403)));

        let run = process_pending_notice(
            &deps(&harness),
            &backend,
            &harness.verifying_key,
            &notice(),
            Duration::ZERO,
        )
        .await;

        assert_eq!(run.outcome, BridgeOutcome::ReportUndelivered);
        assert_eq!(backend.reports().len(), 1);
    }

    #[tokio::test]
    async fn a_claim_for_a_different_grant_is_reported_invalid_and_not_executed() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let backend = FakeBackend::new(navigate_claim(&harness, "some-other-grant"));

        let run = process_pending_notice(
            &deps(&harness),
            &backend,
            &harness.verifying_key,
            &notice(),
            Duration::ZERO,
        )
        .await;

        assert_eq!(run.outcome, BridgeOutcome::Reported);
        assert!(harness.runtime_navigate_calls().is_empty());
        assert_eq!(
            backend.reports()[0]["execution_outcome"],
            json!({"error": {"kind": "grantInvalid"}})
        );
    }

    #[tokio::test]
    async fn a_grant_signed_by_another_key_is_rejected_by_desktop_verification() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let backend = FakeBackend::new(navigate_claim(&harness, "grant-1"));
        let (_other_signing, other_verifying) = crate::browser_grant::tests::signing_keypair();

        let run = process_pending_notice(
            &deps(&harness),
            &backend,
            &other_verifying,
            &notice(),
            Duration::ZERO,
        )
        .await;

        assert!(run.signature_rejected);
        assert!(harness.runtime_navigate_calls().is_empty());
        assert_eq!(
            backend.reports()[0]["execution_outcome"],
            json!({"error": {"kind": "grantInvalid"}})
        );
    }

    #[tokio::test]
    async fn an_ai_grant_for_a_parked_inactive_profile_tab_is_refused_and_reported() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        // The user switched to another profile: "surface-1" is parked.
        harness.active_profile_surfaces.set_active_profile(
            "tenant-a".to_string(),
            Some(crate::browser_profile_store::BrowserProfileId::from_raw_for_test("profile-2")),
        );
        let backend = FakeBackend::new(navigate_claim(&harness, "grant-1"));

        let run = process_pending_notice(
            &deps(&harness),
            &backend,
            &harness.verifying_key,
            &notice(),
            Duration::ZERO,
        )
        .await;

        assert_eq!(run.outcome, BridgeOutcome::Reported);
        assert!(harness.runtime_navigate_calls().is_empty());
        assert_eq!(
            backend.reports()[0]["execution_outcome"],
            json!({"error": {"kind": "profileNotFound"}})
        );
    }

    #[tokio::test]
    async fn a_second_notice_for_the_same_grant_cannot_execute_it_again() {
        let harness = Harness::new("surface-1", "tenant-a", "profile-1");
        let backend = FakeBackend::new(navigate_claim(&harness, "grant-1"));

        process_pending_notice(
            &deps(&harness),
            &backend,
            &harness.verifying_key,
            &notice(),
            Duration::ZERO,
        )
        .await;
        process_pending_notice(
            &deps(&harness),
            &backend,
            &harness.verifying_key,
            &notice(),
            Duration::ZERO,
        )
        .await;

        assert_eq!(
            harness.runtime_navigate_calls().len(),
            1,
            "single-use holds even if the backend re-issued a claim"
        );
        assert_eq!(
            backend.reports()[1]["execution_outcome"],
            json!({"error": {"kind": "grantInvalid"}})
        );
    }

    #[test]
    fn in_flight_processing_is_bounded() {
        let state = Arc::new(BrowserBridgeState::default());
        let permits: Vec<_> = (0..MAX_IN_FLIGHT)
            .map(|_| state.try_acquire().unwrap())
            .collect();
        assert!(state.try_acquire().is_none());
        drop(permits);
        assert!(state.try_acquire().is_some());
    }
}
