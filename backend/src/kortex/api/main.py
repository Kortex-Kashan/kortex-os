"""KORTEX M3 IPC Bridge presentation layer.

`apps/server/README.md` already references `uvicorn kortex.api.main:app`;
this module is that entry point, created for the first time in M3 (see
`phase3_desktop_architecture.md` §9.4). Per §9.2/§20.11, the surface is
deliberately limited to exactly three routes — no per-domain REST routes
are added here, regardless of how convenient one might seem.

Router design rule (restated from `backend/src/kortex/api/README.md`,
binding per §9.3): this module validates transport-level input shape,
delegates to the Kernel Capability Dispatcher, and formats the result. It
must never branch on `capability_name` to change how a request is
dispatched — every request goes through the exact same
`dispatcher.dispatch()` call. The one place this module inspects a
capability's *metadata* rather than its *name* is the session-token
issuance step in `_invoke` below; see that function's docstring for why
that is a metadata-driven transport concern, not per-capability business
logic routing.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from kortex.api.agent_enrollment import router as agent_enrollment_router
from kortex.api.errors import error_details, error_message, map_exception
from kortex.api.kernel_bootstrap import build_and_boot_kernel
from kortex.api.schemas import IpcCapabilityRequest, IpcError, IpcResultEnvelope
from kortex.api.token_codec import decode_token, encode_token
from kortex.core.dispatch import CapabilityRequest
from kortex.core.idempotency import sanitize_for_persistence
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import SecurityPrincipal
from kortex.engines.security.refresh_token_codec import encode_refresh_token

logger = logging.getLogger("kortex.api")

_DEFAULT_TIMEOUT_MS = 30_000

# AI Studio Functional Stabilization, Phase F security correction. Only a
# genuine login mints a NEW refresh token alongside the access token --
# `kortex.security.auth.refresh` itself deliberately does not (its own
# refresh token keeps its original, fixed absolute expiry; only the access
# token it returns is fresh), and no other capability mints or renews
# anything at all. See `_invoke`'s docstring below for the full rationale.
_LOGIN_CAPABILITIES = frozenset(
    {
        "kortex.security.auth.authenticate",
        "kortex.security.oauth.login_complete",
    }
)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.kernel = await build_and_boot_kernel()
    logger.info("KORTEX Kernel booted for M3 IPC Bridge.")
    try:
        yield
    finally:
        await app.state.kernel.shutdown()
        logger.info("KORTEX Kernel shut down.")


app = FastAPI(title="KORTEX IPC Bridge", lifespan=_lifespan)

# Phase 5: the Desktop Agent's enrollment endpoint. Mounted as a router rather
# than an `@app.post` here because it is the first route on this app that is
# versioned (`/api/v1/...`) and it owns its own request parsing — see that
# module's docstring for why it must not use the shared validation handler.
app.include_router(agent_enrollment_router)


def _kernel(request_or_ws: Request | WebSocket) -> Kernel:
    return request_or_ws.app.state.kernel  # type: ignore[no-any-return]


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    return authorization[len(prefix) :]


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Keep the malformed-request response envelope-shaped rather than
    FastAPI's default 422 body — the frontend/Rust boundary expects
    `IpcResultEnvelope` on every response from this endpoint, success or
    failure."""
    envelope = IpcResultEnvelope(
        request_id="",
        correlation_id=str(uuid.uuid4()),
        status="FAILURE",
        payload=None,
        errors=[
            IpcError(
                category="VALIDATION_FAILED",
                message=str(exc),
                correlation_id=str(uuid.uuid4()),
            )
        ],
        warnings=[],
        execution_duration_ms=0.0,
    )
    return JSONResponse(status_code=422, content=envelope.model_dump(by_alias=True))


async def _invoke(
    kernel: Kernel,
    ipc_request: IpcCapabilityRequest,
    session_token_blob: str | None,
) -> tuple[IpcResultEnvelope, str | None, str | None, int]:
    """Dispatch one capability call and build the response envelope.

    Returns `(envelope, minted_session_token, minted_refresh_token, http_status)`.
    `http_status` carries the per-exception status `errors.map_exception`
    resolved (e.g. `AuthenticationError` -> 401 vs `AuthorizationDeniedError`
    -> 403, both `PERMISSION_DENIED`) — it must flow through from here,
    not be re-derived from `category` alone at the route, or that
    401/403 distinction is lost even though the category is identical.

    `minted_session_token` is non-`None` only immediately after a
    *successful* dispatch of a capability whose registry descriptor has
    `requires_authentication is False` and whose raw result is a
    `SecurityPrincipal` — today that is `kortex.security.auth.authenticate`,
    `kortex.security.oauth.login_complete`, and `kortex.security.auth.refresh`
    (Phase F), the only capabilities the registry permits to register that
    way at all (enforced by `RegistryEngine.register_capability`'s own
    hard-coded invariant, unrelated to and unmodified by this code).
    `minted_refresh_token` is non-`None` only alongside a login proper
    (`_LOGIN_CAPABILITIES`) — never alongside a plain refresh, so a refresh
    token's own absolute lifetime is never itself extended.

    This is metadata-driven (`descriptor.requires_authentication`,
    `isinstance(result, SecurityPrincipal)`), not a `capability_name`
    string check, because session-token custody is itself a transport/IPC
    boundary responsibility per §8.4 ("Rust — not the webview — receives
    and stores the resulting session token"), not a capability-routing
    decision. It does not change how the request is dispatched — dispatch
    is identical for every capability regardless of this check's outcome.
    The one exception is `_LOGIN_CAPABILITIES` membership, used only to
    decide whether a SECOND, refresh, token also gets minted — this is a
    narrow, additive check confined to this transport-layer function; it
    changes what gets attached to the response, never how or whether the
    capability itself was dispatched.

    Phase F security correction (AI Studio Functional Stabilization): an
    earlier version of this function reissued a fresh access token after
    *any* successful authenticated capability call. That was found to make
    a stolen access token effectively renewable indefinitely — nothing
    distinguished the legitimate desktop app's own traffic from a stolen
    token being replayed by an attacker, since both are just "a valid
    bearer token calling an authenticated capability." Ordinary capability
    dispatch (the `elif descriptor.requires_authentication` branch that
    used to live here) no longer mints or renews anything at all. Renewal
    is now possible only through the narrow `kortex.security.auth.refresh`
    capability, which requires the separate, narrowly-scoped refresh token
    minted only at login — never the access token used for ordinary calls
    — and that refresh token is itself bounded by an absolute ceiling
    (`AuthenticationManager._REFRESH_TOKEN_TTL`) independent of activity.
    See the Phase F security review report for the full stolen-token
    analysis this change closes.
    """
    correlation_id = ipc_request.correlation_id or str(uuid.uuid4())
    timeout_s = (ipc_request.timeout_ms or _DEFAULT_TIMEOUT_MS) / 1000.0

    session_token = None
    if session_token_blob is not None:
        try:
            session_token = decode_token(session_token_blob)
        except ValueError as exc:
            return (
                IpcResultEnvelope(
                    request_id=ipc_request.request_id,
                    correlation_id=correlation_id,
                    status="FAILURE",
                    payload=None,
                    errors=[IpcError(category="PERMISSION_DENIED", message=str(exc), correlation_id=correlation_id)],
                    warnings=[],
                    execution_duration_ms=0.0,
                ),
                None,
                None,
                401,
            )

    # `resource_tenant_id` is derived only from the server-verified session
    # token, never from caller-supplied `parameters` — `abac.py` denies by
    # default when it is absent or mismatched, and the dispatcher's own
    # adversarial test suite proves a caller cannot inject it via
    # `parameters` (`test_capability_dispatch.py`'s `_authz_context`
    # test). Defaulting it to the caller's own tenant is the only
    # generic, capability-agnostic rule available at this transport layer;
    # a capability that must reach across tenants needs its own explicit
    # design, out of scope here.
    context: dict[str, Any] = {"correlation_id": correlation_id}
    if session_token is not None:
        context["resource_tenant_id"] = session_token.tenant_id

    dispatch_request = CapabilityRequest(
        request_id=ipc_request.request_id,
        correlation_id=correlation_id,
        idempotency_key=ipc_request.idempotency_key,
        capability_name=ipc_request.capability_name,
        session_token=session_token,
        parameters=ipc_request.parameters,
        context=context,
    )

    start = time.monotonic()
    try:
        result: Any = await asyncio.wait_for(kernel.invoke_capability(dispatch_request), timeout=timeout_s)
    except TimeoutError as exc:
        duration_ms = (time.monotonic() - start) * 1000
        mapping = map_exception(exc)
        return (
            IpcResultEnvelope(
                request_id=ipc_request.request_id,
                correlation_id=correlation_id,
                status="FAILURE",
                errors=[
                    IpcError(
                        category=mapping.category,
                        message="Capability invocation timed out.",
                        correlation_id=correlation_id,
                    )
                ],
                execution_duration_ms=duration_ms,
            ),
            None,
            None,
            mapping.http_status,
        )
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        mapping = map_exception(exc)
        logger.info("Capability '%s' failed: %s", ipc_request.capability_name, error_message(exc))
        return (
            IpcResultEnvelope(
                request_id=ipc_request.request_id,
                correlation_id=correlation_id,
                status="FAILURE",
                payload=None,
                errors=[
                    IpcError(
                        category=mapping.category,
                        message=error_message(exc),
                        details=error_details(exc),
                        correlation_id=correlation_id,
                    )
                ],
                warnings=[],
                execution_duration_ms=duration_ms,
            ),
            None,
            None,
            mapping.http_status,
        )

    duration_ms = (time.monotonic() - start) * 1000

    minted_token: str | None = None
    minted_refresh_token: str | None = None
    try:
        descriptor = kernel.get_capability(ipc_request.capability_name)
        if not descriptor.requires_authentication and isinstance(result, SecurityPrincipal):
            # Covers `authenticate`, `oauth_login_complete`, and (Phase F)
            # `kortex.security.auth.refresh` -- all three are registered
            # `requires_authentication=False` and authenticate the caller
            # themselves (credentials / OAuth code / refresh token), rather
            # than via a session-token header. Every one of them mints a
            # fresh access token on success. Only a genuine login
            # (`_LOGIN_CAPABILITIES`) additionally mints a NEW refresh
            # token -- `auth.refresh` deliberately does not, so a session's
            # absolute lifetime is never itself extended by using it.
            security_engine = cast(SecurityEngine, kernel.get_engine("security"))
            issued = await security_engine.authentication_manager.issue_token(result)
            minted_token = encode_token(issued)
            if ipc_request.capability_name in _LOGIN_CAPABILITIES:
                issued_refresh = await security_engine.authentication_manager.issue_refresh_token(result)
                minted_refresh_token = encode_refresh_token(issued_refresh)
    except Exception:
        logger.exception("Failed to mint a session token after successful dispatch.")

    payload = result if isinstance(result, dict) else {"result": _jsonable(result)}
    return (
        IpcResultEnvelope(
            request_id=ipc_request.request_id,
            correlation_id=correlation_id,
            status="SUCCESS",
            payload=payload,
            errors=[],
            warnings=[],
            execution_duration_ms=duration_ms,
        ),
        minted_token,
        minted_refresh_token,
        200,
    )


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of a raw capability result into JSON-safe
    data. Pydantic models (e.g. `SecurityPrincipal`) dump cleanly; anything
    else falls back to `str()` rather than letting `JSONResponse` raise."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


@app.post("/capabilities/invoke")
async def invoke_capability(
    request: Request,
    ipc_request: IpcCapabilityRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    kernel = _kernel(request)
    session_token_blob = _extract_bearer(authorization)
    envelope, minted_token, minted_refresh_token, status_code = await _invoke(kernel, ipc_request, session_token_blob)

    body = envelope.model_dump(by_alias=True)
    if minted_token is not None:
        body["sessionToken"] = minted_token
    if minted_refresh_token is not None:
        body["refreshToken"] = minted_refresh_token
    return JSONResponse(status_code=status_code, content=body)


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    kernel = _kernel(request)
    report = await kernel.health_check()
    overall = report.get("system_health", {}).get("status", "unknown")
    status_code = 200 if overall in ("healthy", "degraded") else 503
    return JSONResponse(status_code=status_code, content=_jsonable(report))


@app.websocket("/events/stream")
async def events_stream(websocket: WebSocket, topic: str = "*") -> None:
    """Authenticated event relay.

    Known limitation (documented, not silently assumed away): `Event`
    (`kortex.engines.event.engine`) has no `tenant_id` field and Event
    Engine has no tenant/permission-scoping concept at all today (verified
    during the M3 audit) — `phase3_desktop_architecture.md` §13.1.2's
    "scoped server-side by tenant_id and granted permissions" is therefore
    implemented here as best-effort filtering on `event.payload["tenant_id"]`
    when a publisher happens to include one, not a guarantee. An event
    published without a `tenant_id` key in its payload is forwarded to
    every authenticated subscriber of a matching topic — see the M3 final
    report's Known Limitations for what a complete fix would require
    (a schema change to `Event` itself, out of scope for this adapter).

    M6.4-0: this relay is externally-broadcast-to-any-same-tenant-client
    surface, distinct in trust level from the internal, in-process
    subscribers (`AIOrchestrationEngine`, `ExternalExecutionManager`) that
    receive the same `Event` object directly from the Event Engine. A
    domain event may legitimately carry a field only those internal
    subscribers should ever see (e.g. `workflow.approval.decided`'s
    `decider_session_token`, a live, usable session token minted for the
    deciding principal so a resume subscriber can dispatch with real
    authenticated identity). `sanitize_for_persistence` is applied to the
    outbound payload below, returning a redacted COPY -- it never mutates
    `event.payload` itself, so this has no effect on the other subscribers
    sharing the same `Event` object within one `publish_event` call.
    """
    authorization = websocket.headers.get("authorization")
    session_token_blob = _extract_bearer(authorization)
    if session_token_blob is None:
        await websocket.close(code=1008, reason="Missing session token.")
        return

    kernel = _kernel(websocket)
    try:
        token = decode_token(session_token_blob)
        security_engine = cast(SecurityEngine, kernel.get_engine("security"))
        principal: SecurityPrincipal = await security_engine.authentication_manager.verify_token(token)
    except Exception as exc:
        logger.info("WS /events/stream rejected: %s", error_message(exc) if hasattr(exc, "message") else str(exc))
        await websocket.close(code=1008, reason="Authentication failed.")
        return

    await websocket.accept()
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def _on_event(event: Any) -> None:
        await queue.put(event)

    subscription_id = kernel.subscribe_event("*", _on_event, subscriber_name=f"ws:{principal.principal_id}")

    is_prefix = topic.endswith("*") and topic != "*"
    prefix = topic[:-1] if is_prefix else None

    async def _matches(event: Any) -> bool:
        if topic != "*":
            if is_prefix:
                if not event.topic.startswith(prefix):
                    return False
            elif event.topic != topic:
                return False
        event_tenant = event.payload.get("tenant_id") if isinstance(event.payload, dict) else None
        return event_tenant is None or event_tenant == principal.tenant_id

    try:
        while True:
            event = await queue.get()
            if not await _matches(event):
                continue
            await websocket.send_json(
                {
                    "eventId": event.id,
                    "topic": event.topic,
                    "payload": _jsonable(sanitize_for_persistence(event.payload)),
                    "correlationId": event.trace_id,
                    "timestampUtc": event.timestamp.isoformat(),
                }
            )
    except WebSocketDisconnect:
        pass
    finally:
        kernel.unsubscribe_event(subscription_id)
