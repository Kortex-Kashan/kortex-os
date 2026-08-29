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

from kortex.api.errors import error_details, error_message, map_exception
from kortex.api.kernel_bootstrap import build_and_boot_kernel
from kortex.api.schemas import IpcCapabilityRequest, IpcError, IpcResultEnvelope
from kortex.api.token_codec import decode_token, encode_token
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import SecurityPrincipal

logger = logging.getLogger("kortex.api")

_DEFAULT_TIMEOUT_MS = 30_000


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
) -> tuple[IpcResultEnvelope, str | None, int]:
    """Dispatch one capability call and build the response envelope.

    Returns `(envelope, minted_session_token, http_status)`.
    `http_status` carries the per-exception status `errors.map_exception`
    resolved (e.g. `AuthenticationError` -> 401 vs `AuthorizationDeniedError`
    -> 403, both `PERMISSION_DENIED`) — it must flow through from here,
    not be re-derived from `category` alone at the route, or that
    401/403 distinction is lost even though the category is identical.

    `minted_session_token` is non-`None` only immediately after a
    *successful* dispatch of a capability whose registry descriptor has
    `requires_authentication is False` and whose raw result is a
    `SecurityPrincipal` — today that is
    exactly (and only) `kortex.security.auth.authenticate`, the sole
    capability the registry permits to register that way at all (enforced
    by `RegistryEngine.register_capability`'s own hard-coded invariant,
    unrelated to and unmodified by this code).

    This is metadata-driven (`descriptor.requires_authentication`,
    `isinstance(result, SecurityPrincipal)`), not a `capability_name`
    string check, because session-token custody is itself a transport/IPC
    boundary responsibility per §8.4 ("Rust — not the webview — receives
    and stores the resulting session token"), not a capability-routing
    decision. It does not change how the request is dispatched — dispatch
    is identical for every capability regardless of this check's outcome.
    An alternative was considered (a new `kortex.security.auth.login`
    capability owned by the Security Engine) and rejected: it would have
    required broadening `RegistryEngine`'s bootstrap-exemption invariant
    and the Security Engine's spec-S15-ratified four-capability list,
    which is a larger, more invasive change than this narrow, additive
    transport-layer step. Documented in the M3 final report as an
    architectural decision, not silently made.
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
            mapping.http_status,
        )

    duration_ms = (time.monotonic() - start) * 1000

    minted_token: str | None = None
    try:
        descriptor = kernel.get_capability(ipc_request.capability_name)
        if not descriptor.requires_authentication and isinstance(result, SecurityPrincipal):
            security_engine = cast(SecurityEngine, kernel.get_engine("security"))
            issued = await security_engine.authentication_manager.issue_token(result)
            minted_token = encode_token(issued)
    except Exception:
        logger.exception("Failed to mint session token after successful bootstrap-exempt dispatch.")

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
    envelope, minted_token, status_code = await _invoke(kernel, ipc_request, session_token_blob)

    body = envelope.model_dump(by_alias=True)
    if minted_token is not None:
        body["sessionToken"] = minted_token
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
                    "payload": _jsonable(event.payload),
                    "correlationId": event.trace_id,
                    "timestampUtc": event.timestamp.isoformat(),
                }
            )
    except WebSocketDisconnect:
        pass
    finally:
        kernel.unsubscribe_event(subscription_id)
