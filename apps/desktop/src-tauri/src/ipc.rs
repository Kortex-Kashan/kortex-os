//! M3 IPC bridge: the `invoke_capability` Tauri command and the secure
//! session-token custody it depends on.
//!
//! Per `phase3_desktop_architecture.md` §6.1/§8.2, this module is a
//! transport only: it forwards `IpcCapabilityRequest` to the backend's
//! `POST /capabilities/invoke` verbatim, attaches whatever session token
//! is currently held (if any), and returns the backend's response verbatim
//! — minus the `sessionToken` field, which is captured and stored here and
//! never reaches the caller (the webview). It never branches on
//! `capability_name`; the Kernel/Dispatcher on the backend owns all
//! capability routing.
//!
//! **Scope boundary, stated plainly**: this module talks to the backend at
//! a configured loopback URL (default `http://127.0.0.1:8000`, overridable
//! via `KORTEX_BACKEND_URL`). It does **not** spawn that backend process —
//! `sidecar.rs`'s own module docs already flag real sidecar wiring
//! (packaging, path resolution, readiness probing) as deferred until a
//! packaging tool is chosen, which remains unresolved (M1.2 scope, not
//! reopened here). For `tauri dev`, the backend is expected to already be
//! running (`uvicorn kortex.api.main:app`), matching
//! `phase3_desktop_architecture.md` §6.7's own dev/production split
//! (dev: run directly from a local Python venv; production: frozen
//! sidecar binary, auto-spawned — the latter is not implemented by this
//! module).

use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;

const DEFAULT_BACKEND_URL: &str = "http://127.0.0.1:8000";
const BACKEND_URL_ENV: &str = "KORTEX_BACKEND_URL";
const KEYRING_SERVICE: &str = "kortex-desktop";
const KEYRING_USER: &str = "session-token";
/// Phase F security correction: the refresh token is a distinct credential
/// from the access token above, stored under its own keychain entry so it
/// is never accidentally conflated with (or attached to ordinary calls
/// like) the access token. See `TokenStore`'s refresh-* methods.
const KEYRING_USER_REFRESH: &str = "refresh-token";
/// The one capability allowed to consume a refresh token (`refresh_session`
/// below) — never attached as a `Bearer` credential, always sent as an
/// explicit body parameter, matching the backend's own contract for
/// `kortex.security.auth.refresh`.
const REFRESH_CAPABILITY_NAME: &str = "kortex.security.auth.refresh";

/// Exact mirror of the frontend's `IpcCapabilityRequest`
/// (`apps/desktop/src/ipc/client.ts`) and the backend's `IpcCapabilityRequest`
/// (`backend/src/kortex/api/schemas.py`) — the same camelCase JSON shape
/// crosses both the Tauri boundary and the loopback HTTP boundary
/// unchanged, per §8.1 ("the IPC layer is a transport, not a second
/// contract system").
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct IpcCapabilityRequest {
    pub request_id: String,
    pub capability_name: String,
    #[serde(default)]
    pub parameters: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub correlation_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub idempotency_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeout_ms: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct IpcError {
    pub category: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<Value>,
    pub correlation_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct IpcResultEnvelope {
    pub request_id: String,
    pub correlation_id: String,
    pub status: String,
    #[serde(default)]
    pub payload: Option<Value>,
    #[serde(default)]
    pub errors: Vec<IpcError>,
    #[serde(default)]
    pub warnings: Vec<IpcError>,
    pub execution_duration_ms: f64,
    /// The real HTTP status code the backend's `/capabilities/invoke`
    /// response line carried (e.g. 401 vs 403 for two exceptions that both
    /// map to the identical `PERMISSION_DENIED` category —
    /// `backend/src/kortex/api/errors.py`'s own documented taxonomy
    /// collapse). Never present in the backend's JSON body itself (it's a
    /// transport-level fact, not a payload field) — populated here, in
    /// `forward_capability_request`, from the real `reqwest::Response`
    /// before the body is parsed. `None` only when no real HTTP response
    /// was ever received (backend unreachable / unparseable response) —
    /// see `transport_error`. Added for M4.1: without this field, the
    /// 401-vs-403 distinction the backend already computes and sends never
    /// reached the frontend, since the pre-M4.1 code path only ever read
    /// the response body, never `response.status()`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub http_status: Option<u16>,
}

/// The raw HTTP response body carries up to two extra fields beyond
/// `IpcResultEnvelope`: `sessionToken` (present only immediately after a
/// successful login/refresh — see `backend/src/kortex/api/main.py::_invoke`)
/// and `refreshToken` (Phase F security correction; present only
/// immediately after a successful login, never after a plain refresh).
/// This type exists so both fields are captured and stripped *here*, never
/// forwarded to the frontend as part of `IpcResultEnvelope`.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawBackendResponse {
    #[serde(flatten)]
    envelope: IpcResultEnvelope,
    #[serde(default)]
    session_token: Option<String>,
    #[serde(default)]
    refresh_token: Option<String>,
}

/// Session-token custody, abstracted behind a trait so production code
/// (`KeyringTokenStore`, backed by the OS credential manager) and tests
/// (an in-memory double) share the exact same `invoke_capability` logic.
/// The webview never has access to either implementation — only Rust
/// commands hold a `TokenStore`.
///
/// Phase F security correction: the `*_refresh` methods custody a SECOND,
/// distinct credential under its own storage slot — never returned by
/// `load()`/attached by `forward_capability_request`'s `Bearer` header,
/// and never overwritten by an ordinary call's (nonexistent, post-Phase-F)
/// `sessionToken`. Only `refresh_session` below ever reads it, and only
/// `logout` and a successful login ever write it.
pub trait TokenStore: Send + Sync {
    fn load(&self) -> Option<String>;
    fn store(&self, token: &str);
    /// Discards the held session token (logout). A store with nothing held
    /// is a no-op, never an error.
    fn clear(&self);
    fn load_refresh(&self) -> Option<String>;
    fn store_refresh(&self, token: &str);
    /// Discards the held refresh token (logout). A store with nothing held
    /// is a no-op, never an error.
    fn clear_refresh(&self);
}

/// Production implementation: the OS-native credential store (Windows
/// Credential Manager / macOS Keychain / Linux Secret Service), matching
/// `phase3_desktop_architecture.md` §8.4's "OS-native secure credential
/// store" requirement. Never logs, never returns the token to the
/// webview — only `invoke_capability`/the event relay read it.
pub struct KeyringTokenStore;

impl TokenStore for KeyringTokenStore {
    fn load(&self) -> Option<String> {
        keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER)
            .ok()?
            .get_password()
            .ok()
    }

    fn store(&self, token: &str) {
        if let Ok(entry) = keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER) {
            // A failed write means the session simply won't be
            // remembered across restarts — never a reason to fail the
            // login response the caller is already holding.
            let _ = entry.set_password(token);
        }
    }

    fn clear(&self) {
        if let Ok(entry) = keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER) {
            // "Nothing was ever stored" and "the OS keychain entry is
            // already gone" are both acceptable logout outcomes, never a
            // reason to fail the logout the caller is already committed to.
            let _ = entry.delete_credential();
        }
    }

    fn load_refresh(&self) -> Option<String> {
        keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER_REFRESH)
            .ok()?
            .get_password()
            .ok()
    }

    fn store_refresh(&self, token: &str) {
        if let Ok(entry) = keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER_REFRESH) {
            let _ = entry.set_password(token);
        }
    }

    fn clear_refresh(&self) {
        if let Ok(entry) = keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER_REFRESH) {
            let _ = entry.delete_credential();
        }
    }
}

/// Shared client state, managed as Tauri app state.
pub struct IpcClientState {
    http: reqwest::Client,
    base_url: String,
    pub token_store: Arc<dyn TokenStore>,
}

impl IpcClientState {
    pub fn new(token_store: Arc<dyn TokenStore>) -> Self {
        let base_url =
            std::env::var(BACKEND_URL_ENV).unwrap_or_else(|_| DEFAULT_BACKEND_URL.to_string());
        Self {
            http: reqwest::Client::new(),
            base_url,
            token_store,
        }
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn current_token(&self) -> Option<String> {
        self.token_store.load()
    }

    pub fn has_token(&self) -> bool {
        self.token_store.load().is_some()
    }

    pub fn clear_token(&self) {
        self.token_store.clear();
    }
}

fn correlation_id_for(request: &IpcCapabilityRequest) -> String {
    request
        .correlation_id
        .clone()
        .unwrap_or_else(|| request.request_id.clone())
}

fn transport_error(request: &IpcCapabilityRequest, message: String) -> IpcResultEnvelope {
    let correlation_id = correlation_id_for(request);
    IpcResultEnvelope {
        request_id: request.request_id.clone(),
        correlation_id: correlation_id.clone(),
        status: "FAILURE".to_string(),
        payload: None,
        errors: vec![IpcError {
            category: "SERVICE_UNAVAILABLE".to_string(),
            message,
            details: None,
            correlation_id,
        }],
        warnings: vec![],
        execution_duration_ms: 0.0,
        // No real HTTP response was ever received — never fabricate a
        // status code for a request that never reached the backend.
        http_status: None,
    }
}

/// Forwards `request` to `POST {base_url}/capabilities/invoke`, attaching
/// the stored session token (if any) as `Authorization: Bearer <token>`.
/// On a successful response carrying a new `sessionToken`, stores it and
/// returns only the `IpcResultEnvelope` half — the token itself never
/// leaves this function.
pub async fn forward_capability_request(
    state: &IpcClientState,
    request: IpcCapabilityRequest,
) -> IpcResultEnvelope {
    let url = format!("{}/capabilities/invoke", state.base_url);
    let mut builder = state.http.post(&url).json(&request);
    if let Some(token) = state.token_store.load() {
        builder = builder.bearer_auth(token);
    }

    let response = match builder
        .timeout(Duration::from_millis(
            request.timeout_ms.unwrap_or(30_000) + 5_000,
        ))
        .send()
        .await
    {
        Ok(resp) => resp,
        Err(err) => return transport_error(&request, format!("Backend unreachable: {err}")),
    };

    // Captured before `response` is consumed by `.bytes()` below — this is
    // the real status line FastAPI sent (e.g. 401 for an invalid/expired
    // token vs 403 for an authenticated-but-forbidden request; see
    // `backend/src/kortex/api/errors.py::map_exception`), which the
    // pre-M4.1 version of this function discarded entirely by only ever
    // reading the JSON body.
    let http_status = response.status().as_u16();

    let bytes = match response.bytes().await {
        Ok(bytes) => bytes,
        Err(err) => {
            return transport_error(&request, format!("Failed to read backend response: {err}"))
        }
    };

    let raw: RawBackendResponse = match serde_json::from_slice(&bytes) {
        Ok(raw) => raw,
        Err(err) => {
            return transport_error(
                &request,
                format!("Backend returned an unparseable response: {err}"),
            )
        }
    };

    if let Some(token) = &raw.session_token {
        state.token_store.store(token);
    }
    if let Some(refresh_token) = &raw.refresh_token {
        state.token_store.store_refresh(refresh_token);
    }

    let mut envelope = raw.envelope;
    envelope.http_status = Some(http_status);
    envelope
}

#[tauri::command]
pub async fn invoke_capability(
    state: tauri::State<'_, Arc<IpcClientState>>,
    request: IpcCapabilityRequest,
) -> Result<IpcResultEnvelope, ()> {
    // Every failure mode `forward_capability_request` can encounter is
    // already represented *inside* `IpcResultEnvelope` (status: FAILURE
    // + a documented `IpcError`, per §8.3) — this command never needs to
    // reject the JS promise. The `Result` wrapper exists only because
    // Tauri's async-command macro requires it for a command taking a
    // borrowed `State` argument (a `'static`-lifetime requirement on the
    // underlying future); `Err` is unreachable in practice.
    Ok(forward_capability_request(&state, request).await)
}

/// Phase F security correction: exchanges the held refresh token for a
/// fresh access token via `kortex.security.auth.refresh`, reusing
/// `forward_capability_request` exactly like any other capability call —
/// the refresh token travels as an explicit body parameter, never as the
/// `Bearer` credential (that header still carries whatever access token is
/// currently held, which `auth.refresh` ignores since it is registered
/// `requires_authentication=False`). A successful response's `sessionToken`
/// is captured and stored by the same generic path every other call
/// already uses; the webview only ever sees whether the call succeeded.
///
/// If no refresh token is held at all (never logged in this way, or
/// already logged out), returns a synthetic FAILURE envelope without ever
/// making a network call — there is nothing to exchange. Implemented as a
/// plain function over `&IpcClientState` (mirroring `forward_capability_
/// request`'s own split from `invoke_capability`) so it is directly
/// testable without a full Tauri harness.
async fn refresh_session_impl(state: &IpcClientState) -> IpcResultEnvelope {
    let Some(refresh_token) = state.token_store.load_refresh() else {
        let request_id = uuid_like_id();
        return IpcResultEnvelope {
            request_id: request_id.clone(),
            correlation_id: request_id,
            status: "FAILURE".to_string(),
            payload: None,
            errors: vec![IpcError {
                category: "PERMISSION_DENIED".to_string(),
                message: "No refresh token is held.".to_string(),
                details: None,
                correlation_id: "no-refresh-token".to_string(),
            }],
            warnings: vec![],
            execution_duration_ms: 0.0,
            http_status: None,
        };
    };

    let request_id = uuid_like_id();
    let request = IpcCapabilityRequest {
        request_id: request_id.clone(),
        capability_name: REFRESH_CAPABILITY_NAME.to_string(),
        parameters: serde_json::json!({ "refresh_token": refresh_token }),
        correlation_id: Some(request_id),
        idempotency_key: None,
        timeout_ms: None,
    };
    forward_capability_request(state, request).await
}

#[tauri::command]
pub async fn refresh_session(
    state: tauri::State<'_, Arc<IpcClientState>>,
) -> Result<IpcResultEnvelope, ()> {
    // Same unreachable-`Err` note as `invoke_capability` above.
    Ok(refresh_session_impl(&state).await)
}

/// Minimal, dependency-free unique-enough request id for the one command
/// (`refresh_session`) that must construct its own `IpcCapabilityRequest`
/// rather than receive one from the webview — every other command's
/// request id already comes from the frontend's own `crypto.randomUUID()`.
/// Not a real UUID (no external crate pulled in for one call site); only
/// needs to be unique enough for correlation/log-reading, never parsed as
/// a UUID by anything.
fn uuid_like_id() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("refresh-session-{nanos:x}")
}

/// M4.1: reports only whether a session token is currently held — never
/// the token's value. Lets the frontend decide, on startup, whether a
/// backend validation round trip is worth attempting at all (no stored
/// token means there is nothing to validate, so the CHECKING state can
/// resolve straight to UNAUTHENTICATED without ever calling the backend).
#[tauri::command]
pub fn has_session(state: tauri::State<'_, Arc<IpcClientState>>) -> bool {
    state.has_token()
}

/// M4.1: discards the held session token (logout). Rust remains the sole
/// custodian of the token for its entire lifecycle, including its end —
/// the webview asks for logout, it never handles the token itself.
///
/// Phase F security correction: also discards the held refresh token —
/// without this, a logged-out session's refresh token would remain in the
/// OS keychain, still capable of minting a fresh access token via
/// `refresh_session` for the remainder of its own absolute ceiling. Logout
/// must end BOTH credentials' usefulness, not just the access token's.
#[tauri::command]
pub fn logout(state: tauri::State<'_, Arc<IpcClientState>>) {
    state.clear_token();
    state.token_store.clear_refresh();
}

/// Outcome of a `GET {base_url}/health` call, returned verbatim to the
/// webview. `body` carries the backend's JSON response unmodified — no
/// wire-shape translation is needed here (unlike `IpcResultEnvelope`)
/// because `/health` is not a capability call, it is Kernel diagnostic
/// data with its own already-stable snake_case shape
/// (`backend/src/kortex/core/kernel.py::health_check`). `ok` distinguishes
/// "the backend answered" (regardless of whether it reports itself
/// healthy or degraded — that verdict lives inside `body`) from a genuine
/// transport failure (backend unreachable, or a response this command
/// could not parse as JSON).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SystemHealthOutcome {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status_code: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub body: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

const HEALTH_REQUEST_TIMEOUT: Duration = Duration::from_secs(10);

/// Fetches `GET {base_url}/health` and returns it verbatim.
///
/// Deliberately attaches **no** `Authorization` header: per
/// `backend/src/kortex/api/main.py`'s `/health` route, this endpoint is
/// intentionally unauthenticated (a liveness/diagnostic surface, not a
/// capability), and preserving that exact backend contract — rather than
/// attaching a token "for consistency" — is the whole point of routing it
/// through its own command instead of reusing `invoke_capability`. No
/// session token is read or required for this call.
pub async fn fetch_system_health(state: &IpcClientState) -> SystemHealthOutcome {
    let url = format!("{}/health", state.base_url);
    let response = match state.http.get(&url).timeout(HEALTH_REQUEST_TIMEOUT).send().await {
        Ok(resp) => resp,
        Err(err) => {
            return SystemHealthOutcome {
                ok: false,
                status_code: None,
                body: None,
                error: Some(format!("Backend unreachable: {err}")),
            }
        }
    };
    let status_code = response.status().as_u16();

    let bytes = match response.bytes().await {
        Ok(bytes) => bytes,
        Err(err) => {
            return SystemHealthOutcome {
                ok: false,
                status_code: Some(status_code),
                body: None,
                error: Some(format!("Failed to read backend response: {err}")),
            }
        }
    };

    match serde_json::from_slice::<Value>(&bytes) {
        Ok(body) => SystemHealthOutcome {
            ok: true,
            status_code: Some(status_code),
            body: Some(body),
            error: None,
        },
        Err(err) => SystemHealthOutcome {
            ok: false,
            status_code: Some(status_code),
            body: None,
            error: Some(format!("Backend returned an unparseable response: {err}")),
        },
    }
}

#[tauri::command]
pub async fn get_system_health(
    state: tauri::State<'_, Arc<IpcClientState>>,
) -> Result<SystemHealthOutcome, ()> {
    // Same `Result` wrapper note as `invoke_capability` above: every
    // failure mode is already represented inside `SystemHealthOutcome`,
    // `Err` is unreachable in practice.
    Ok(fetch_system_health(&state).await)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::convert::Infallible;
    use std::sync::Mutex;

    use http_body_util::{BodyExt, Full};
    use hyper::body::Bytes;
    use hyper::service::service_fn;
    use hyper::{Request as HyperRequest, Response as HyperResponse};
    use hyper_util::rt::TokioIo;
    use tokio::net::TcpListener;

    /// An in-memory `TokenStore` double — no real OS keychain access is
    /// exercised in unit tests (which would be flaky/permission-sensitive
    /// in CI), while `KeyringTokenStore` itself is a thin enough wrapper
    /// (three keyring calls, no branching logic of its own) that its
    /// correctness rests on the `keyring` crate, not on code written here.
    #[derive(Default)]
    struct MemoryTokenStore {
        token: Mutex<Option<String>>,
        refresh_token: Mutex<Option<String>>,
    }

    impl TokenStore for MemoryTokenStore {
        fn load(&self) -> Option<String> {
            self.token.lock().unwrap().clone()
        }

        fn store(&self, token: &str) {
            *self.token.lock().unwrap() = Some(token.to_string());
        }

        fn clear(&self) {
            *self.token.lock().unwrap() = None;
        }

        fn load_refresh(&self) -> Option<String> {
            self.refresh_token.lock().unwrap().clone()
        }

        fn store_refresh(&self, token: &str) {
            *self.refresh_token.lock().unwrap() = Some(token.to_string());
        }

        fn clear_refresh(&self) {
            *self.refresh_token.lock().unwrap() = None;
        }
    }

    /// Spawns a real local HTTP server (matching `sidecar.rs`'s own
    /// preference for real OS/network primitives over a mocking
    /// framework) that returns a fixed JSON body for every request, and
    /// records the last request's headers/body for assertions.
    type CapturedRequest = Arc<Mutex<Option<(Vec<(String, String)>, Value)>>>;

    struct RecordingServer {
        base_url: String,
        last_request: CapturedRequest,
    }

    async fn start_recording_server(response_body: &'static str) -> RecordingServer {
        start_recording_server_with_status(response_body, 200).await
    }

    /// Same as `start_recording_server`, but with a caller-chosen HTTP
    /// status line — needed to prove `http_status` is threaded from the
    /// real response onto the returned envelope (M4.1's 401-vs-403 fix),
    /// which a hardcoded-200 server can never exercise.
    async fn start_recording_server_with_status(
        response_body: &'static str,
        status: u16,
    ) -> RecordingServer {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let last_request: CapturedRequest = Arc::new(Mutex::new(None));
        let captured = last_request.clone();

        tokio::spawn(async move {
            loop {
                let (stream, _) = match listener.accept().await {
                    Ok(pair) => pair,
                    Err(_) => break,
                };
                let io = TokioIo::new(stream);
                let captured = captured.clone();
                let svc = service_fn(move |req: HyperRequest<hyper::body::Incoming>| {
                    let captured = captured.clone();
                    async move {
                        let headers = req
                            .headers()
                            .iter()
                            .map(|(k, v)| {
                                (k.to_string(), v.to_str().unwrap_or("").to_string())
                            })
                            .collect();
                        let body_bytes = req.into_body().collect().await.unwrap().to_bytes();
                        let body_json: Value =
                            serde_json::from_slice(&body_bytes).unwrap_or(Value::Null);
                        *captured.lock().unwrap() = Some((headers, body_json));
                        let mut response =
                            HyperResponse::new(Full::new(Bytes::from(response_body)));
                        *response.status_mut() =
                            hyper::StatusCode::from_u16(status).unwrap();
                        Ok::<_, Infallible>(response)
                    }
                });
                tokio::spawn(async move {
                    let _ = hyper::server::conn::http1::Builder::new()
                        .serve_connection(io, svc)
                        .await;
                });
            }
        });

        RecordingServer {
            base_url: format!("http://{addr}"),
            last_request,
        }
    }

    fn sample_request() -> IpcCapabilityRequest {
        IpcCapabilityRequest {
            request_id: "req-1".to_string(),
            capability_name: "kortex.security.auth.authenticate".to_string(),
            parameters: serde_json::json!({}),
            correlation_id: None,
            idempotency_key: None,
            timeout_ms: None,
        }
    }

    fn state_with(base_url: String, token_store: Arc<dyn TokenStore>) -> IpcClientState {
        IpcClientState {
            http: reqwest::Client::new(),
            base_url,
            token_store,
        }
    }

    #[tokio::test]
    async fn successful_login_response_mints_and_stores_token_without_leaking_it() {
        let server = start_recording_server(
            r#"{"requestId":"req-1","correlationId":"c-1","status":"SUCCESS","payload":{"principalId":"alice"},"errors":[],"warnings":[],"executionDurationMs":1.0,"sessionToken":"opaque-blob"}"#,
        )
        .await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with(server.base_url, store.clone());

        let envelope = forward_capability_request(&state, sample_request()).await;

        assert_eq!(envelope.status, "SUCCESS");
        // The token must never appear anywhere on the envelope returned
        // to the caller — proving acceptance criteria #3's Rust half.
        let serialized = serde_json::to_string(&envelope).unwrap();
        assert!(!serialized.contains("opaque-blob"));
        assert_eq!(store.load(), Some("opaque-blob".to_string()));
    }

    #[tokio::test]
    async fn stored_token_is_attached_as_bearer_auth_on_subsequent_calls() {
        let server = start_recording_server(
            r#"{"requestId":"req-1","correlationId":"c-1","status":"SUCCESS","payload":null,"errors":[],"warnings":[],"executionDurationMs":1.0}"#,
        )
        .await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        store.store("previously-issued-token");
        let state = state_with(server.base_url, store);

        let _ = forward_capability_request(&state, sample_request()).await;

        let (headers, _) = server.last_request.lock().unwrap().clone().unwrap();
        let auth = headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case("authorization"))
            .map(|(_, v)| v.clone());
        assert_eq!(auth, Some("Bearer previously-issued-token".to_string()));
    }

    #[tokio::test]
    async fn no_stored_token_means_no_authorization_header() {
        let server = start_recording_server(
            r#"{"requestId":"req-1","correlationId":"c-1","status":"FAILURE","payload":null,"errors":[],"warnings":[],"executionDurationMs":1.0}"#,
        )
        .await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with(server.base_url, store);

        let _ = forward_capability_request(&state, sample_request()).await;

        let (headers, _) = server.last_request.lock().unwrap().clone().unwrap();
        assert!(!headers.iter().any(|(k, _)| k.eq_ignore_ascii_case("authorization")));
    }

    #[tokio::test]
    async fn request_body_forwarded_verbatim_in_camel_case() {
        let server = start_recording_server(
            r#"{"requestId":"req-1","correlationId":"c-1","status":"SUCCESS","payload":null,"errors":[],"warnings":[],"executionDurationMs":1.0}"#,
        )
        .await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with(server.base_url, store);

        let mut request = sample_request();
        request.parameters = serde_json::json!({"foo": "bar"});
        let _ = forward_capability_request(&state, request).await;

        let (_, body) = server.last_request.lock().unwrap().clone().unwrap();
        assert_eq!(body["requestId"], "req-1");
        assert_eq!(body["capabilityName"], "kortex.security.auth.authenticate");
        assert_eq!(body["parameters"]["foo"], "bar");
        assert!(body.get("request_id").is_none(), "must not send snake_case");
    }

    #[tokio::test]
    async fn unreachable_backend_returns_service_unavailable_not_a_panic() {
        // Port 0 request already consumed a real ephemeral port above;
        // here we deliberately point at a closed port to prove the
        // transport-failure path degrades gracefully.
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with("http://127.0.0.1:1".to_string(), store);

        let envelope = forward_capability_request(&state, sample_request()).await;

        assert_eq!(envelope.status, "FAILURE");
        assert_eq!(envelope.errors[0].category, "SERVICE_UNAVAILABLE");
    }

    #[test]
    fn memory_token_store_round_trips() {
        let store = MemoryTokenStore::default();
        assert_eq!(store.load(), None);
        store.store("abc");
        assert_eq!(store.load(), Some("abc".to_string()));
    }

    #[test]
    fn memory_token_store_clear_discards_the_token() {
        let store = MemoryTokenStore::default();
        store.store("abc");
        store.clear();
        assert_eq!(store.load(), None);
        // Clearing an already-empty store is a no-op, never a panic.
        store.clear();
        assert_eq!(store.load(), None);
    }

    #[tokio::test]
    async fn a_401_response_threads_its_real_status_onto_the_envelope() {
        let server = start_recording_server_with_status(
            r#"{"requestId":"req-1","correlationId":"c-1","status":"FAILURE","payload":null,"errors":[{"category":"PERMISSION_DENIED","message":"invalid token","correlationId":"c-1"}],"warnings":[],"executionDurationMs":1.0}"#,
            401,
        )
        .await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with(server.base_url, store);

        let envelope = forward_capability_request(&state, sample_request()).await;

        assert_eq!(envelope.errors[0].category, "PERMISSION_DENIED");
        assert_eq!(envelope.http_status, Some(401));
    }

    #[tokio::test]
    async fn a_403_response_threads_its_real_status_onto_the_envelope() {
        // Same body/category as the 401 case above — proving the
        // distinction lives in `http_status`, not in anything the body
        // itself carries (per `errors.py`, both collapse to the identical
        // PERMISSION_DENIED category).
        let server = start_recording_server_with_status(
            r#"{"requestId":"req-1","correlationId":"c-1","status":"FAILURE","payload":null,"errors":[{"category":"PERMISSION_DENIED","message":"forbidden","correlationId":"c-1"}],"warnings":[],"executionDurationMs":1.0}"#,
            403,
        )
        .await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with(server.base_url, store);

        let envelope = forward_capability_request(&state, sample_request()).await;

        assert_eq!(envelope.errors[0].category, "PERMISSION_DENIED");
        assert_eq!(envelope.http_status, Some(403));
    }

    #[tokio::test]
    async fn a_successful_response_also_carries_its_real_200_status() {
        let server = start_recording_server(
            r#"{"requestId":"req-1","correlationId":"c-1","status":"SUCCESS","payload":null,"errors":[],"warnings":[],"executionDurationMs":1.0}"#,
        )
        .await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with(server.base_url, store);

        let envelope = forward_capability_request(&state, sample_request()).await;

        assert_eq!(envelope.http_status, Some(200));
    }

    #[tokio::test]
    async fn unreachable_backend_carries_no_fabricated_http_status() {
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with("http://127.0.0.1:1".to_string(), store);

        let envelope = forward_capability_request(&state, sample_request()).await;

        assert_eq!(envelope.http_status, None);
    }

    #[test]
    fn ipc_client_state_has_token_and_clear_token_reflect_the_underlying_store() {
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with("http://127.0.0.1:1".to_string(), store);

        assert!(!state.has_token());
        state.token_store.store("a-token");
        assert!(state.has_token());

        state.clear_token();
        assert!(!state.has_token());
        assert_eq!(state.current_token(), None);
    }

    #[tokio::test]
    async fn fetch_system_health_returns_backend_body_verbatim_without_auth_header() {
        let server = start_recording_server(
            r#"{"kernelState":"RUNNING","systemHealthPlaceholder":true}"#,
        )
        .await;
        // A token IS stored, proving the omission of the Authorization
        // header below is deliberate (health is unauthenticated by
        // contract), not just "no token was available to attach".
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        store.store("some-stored-token");
        let state = state_with(server.base_url, store);

        let outcome = fetch_system_health(&state).await;

        assert!(outcome.ok);
        assert_eq!(outcome.status_code, Some(200));
        assert_eq!(outcome.body.unwrap()["kernelState"], "RUNNING");
        assert!(outcome.error.is_none());

        let (headers, _) = server.last_request.lock().unwrap().clone().unwrap();
        assert!(
            !headers.iter().any(|(k, _)| k.eq_ignore_ascii_case("authorization")),
            "GET /health must never carry an Authorization header"
        );
    }

    #[tokio::test]
    async fn fetch_system_health_unreachable_backend_reports_ok_false() {
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with("http://127.0.0.1:1".to_string(), store);

        let outcome = fetch_system_health(&state).await;

        assert!(!outcome.ok);
        assert!(outcome.body.is_none());
        assert!(outcome.error.unwrap().contains("unreachable"));
    }

    #[tokio::test]
    async fn fetch_system_health_unparseable_response_reports_ok_false() {
        let server = start_recording_server("not json").await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with(server.base_url, store);

        let outcome = fetch_system_health(&state).await;

        assert!(!outcome.ok);
        assert_eq!(outcome.status_code, Some(200));
        assert!(outcome.body.is_none());
        assert!(outcome.error.is_some());
    }

    // -- Phase F security correction: refresh-token custody --------------

    #[test]
    fn memory_token_store_refresh_round_trips_independently_of_the_access_token() {
        let store = MemoryTokenStore::default();
        assert_eq!(store.load_refresh(), None);
        store.store("access-token");
        store.store_refresh("refresh-token");
        assert_eq!(store.load(), Some("access-token".to_string()));
        assert_eq!(store.load_refresh(), Some("refresh-token".to_string()));

        store.clear_refresh();
        assert_eq!(store.load_refresh(), None);
        // Clearing the refresh token must never touch the access token.
        assert_eq!(store.load(), Some("access-token".to_string()));
        // Clearing an already-empty refresh slot is a no-op, never a panic.
        store.clear_refresh();
        assert_eq!(store.load_refresh(), None);
    }

    #[tokio::test]
    async fn a_login_response_with_a_refresh_token_stores_it_separately_from_the_access_token() {
        let server = start_recording_server(
            r#"{"requestId":"req-1","correlationId":"c-1","status":"SUCCESS","payload":{"principalId":"alice"},"errors":[],"warnings":[],"executionDurationMs":1.0,"sessionToken":"access-blob","refreshToken":"refresh-blob"}"#,
        )
        .await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with(server.base_url, store.clone());

        let envelope = forward_capability_request(&state, sample_request()).await;

        assert_eq!(envelope.status, "SUCCESS");
        // Neither token may ever appear on the envelope returned to the
        // caller (webview) — same guarantee `sessionToken` already has,
        // now proven for `refreshToken` too.
        let serialized = serde_json::to_string(&envelope).unwrap();
        assert!(!serialized.contains("access-blob"));
        assert!(!serialized.contains("refresh-blob"));
        assert_eq!(store.load(), Some("access-blob".to_string()));
        assert_eq!(store.load_refresh(), Some("refresh-blob".to_string()));
    }

    #[tokio::test]
    async fn an_ordinary_call_response_with_no_refresh_token_field_leaves_any_stored_refresh_token_untouched() {
        // Mirrors the backend's own Phase F contract: an ordinary
        // capability response never carries `refreshToken` at all. This
        // proves the Rust side does not, say, clear the refresh slot just
        // because the field was absent from a given response.
        let server = start_recording_server(
            r#"{"requestId":"req-1","correlationId":"c-1","status":"SUCCESS","payload":null,"errors":[],"warnings":[],"executionDurationMs":1.0}"#,
        )
        .await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        store.store_refresh("still-here");
        let state = state_with(server.base_url, store.clone());

        let _ = forward_capability_request(&state, sample_request()).await;

        assert_eq!(store.load_refresh(), Some("still-here".to_string()));
    }

    #[tokio::test]
    async fn refresh_session_with_no_stored_refresh_token_makes_no_network_call() {
        // Points at a closed port: if `refresh_session_impl` tried to make
        // a real request here, it would fail with SERVICE_UNAVAILABLE, not
        // the PERMISSION_DENIED this test asserts -- so a SERVICE_UNAVAILABLE
        // result would indicate the "no network call" contract was broken.
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with("http://127.0.0.1:1".to_string(), store);

        let envelope = refresh_session_impl(&state).await;

        assert_eq!(envelope.status, "FAILURE");
        assert_eq!(envelope.errors[0].category, "PERMISSION_DENIED");
    }

    #[tokio::test]
    async fn refresh_session_sends_the_refresh_token_as_a_body_parameter_never_as_the_bearer_header() {
        let server = start_recording_server(
            r#"{"requestId":"req-1","correlationId":"c-1","status":"SUCCESS","payload":{"principalId":"alice"},"errors":[],"warnings":[],"executionDurationMs":1.0,"sessionToken":"fresh-access-blob"}"#,
        )
        .await;
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        store.store("stale-access-token");
        store.store_refresh("the-refresh-token");
        let state = state_with(server.base_url, store.clone());

        let envelope = refresh_session_impl(&state).await;

        assert_eq!(envelope.status, "SUCCESS");
        let (headers, body) = server.last_request.lock().unwrap().clone().unwrap();
        assert_eq!(body["capabilityName"], "kortex.security.auth.refresh");
        assert_eq!(body["parameters"]["refresh_token"], "the-refresh-token");
        // The stale access token is still whatever `forward_capability_
        // request` always attaches (harmless: `auth.refresh` ignores it) --
        // the refresh token itself must never appear in that header.
        let auth = headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case("authorization"))
            .map(|(_, v)| v.clone());
        assert_eq!(auth, Some("Bearer stale-access-token".to_string()));
        assert!(!auth.unwrap().contains("the-refresh-token"));
        // A successful refresh mints a new access token via the same
        // generic capture path -- and must never mint a new refresh token.
        assert_eq!(store.load(), Some("fresh-access-blob".to_string()));
        assert_eq!(store.load_refresh(), Some("the-refresh-token".to_string()));
    }

    #[test]
    fn logout_clears_both_the_access_token_and_the_refresh_token() {
        // `logout` itself takes a `tauri::State`, which requires a running
        // Tauri app to construct -- exercised at the `TokenStore` level
        // instead, which is exactly what `logout`'s own body delegates to
        // (`state.clear_token()` + `state.token_store.clear_refresh()`).
        let store: Arc<dyn TokenStore> = Arc::new(MemoryTokenStore::default());
        let state = state_with("http://127.0.0.1:1".to_string(), store);
        state.token_store.store("access-token");
        state.token_store.store_refresh("refresh-token");

        state.clear_token();
        state.token_store.clear_refresh();

        assert_eq!(state.current_token(), None);
        assert_eq!(state.token_store.load_refresh(), None);
    }
}
