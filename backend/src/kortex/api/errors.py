"""Exception -> `IpcErrorCategory` translation for the M3 presentation layer.

`CapabilityDispatcher.dispatch()` (`kortex.core.dispatch`) communicates
failure via exceptions, not a `UniversalResult`-shaped return value (see
`schemas.py` module docstring) — this module is the adapter the M3 task
brief explicitly calls for ("If the existing dispatcher communicates
failures through exceptions rather than UniversalResult, build the API
adapter that translates those exceptions into the documented IPC error
contract"), not a rewrite of the dispatcher.

Mapping note — `AuthenticationError` -> `PERMISSION_DENIED`: the ratified
six-category taxonomy in `platform_service_contracts.md` §7 (and the
frontend's `IpcErrorCategory`) has no category for "no/invalid/expired
session token" distinct from "authenticated but forbidden". Introducing a
seventh category (e.g. a `SESSION_EXPIRED` value) is exactly the open
decision `phase3_desktop_architecture.md` §21.3 item 4 flags as requiring
Chief Architect / user sign-off before implementation — so rather than
unilaterally extending the taxonomy, both collapse to `PERMISSION_DENIED`
here. `message`/`details` still distinguish the two for logging/display;
`error.category` does not (components must not branch on category to tell
them apart today — see §8.3's own rule that `category`, not `message`, is
the stable contract).
"""

from __future__ import annotations

import http
from dataclasses import dataclass
from typing import Any

from kortex.api.schemas import IpcErrorCategory
from kortex.core.exceptions import (
    CapabilityNotFoundError,
    ConcurrentExecutionError,
    IdempotencyError,
)
from kortex.engines.security.exceptions import (
    AuthenticationError,
    AuthorizationDeniedError,
    SecurityEngineError,
)


@dataclass(frozen=True)
class ErrorMapping:
    category: IpcErrorCategory
    http_status: int


def map_exception(exc: BaseException) -> ErrorMapping:
    """Map a raised exception to its `IpcErrorCategory` + HTTP status.

    Order matters: `AuthorizationDeniedError`/`AuthenticationError` are
    both `SecurityEngineError` subclasses, so the more specific checks
    must run first.
    """
    if isinstance(exc, CapabilityNotFoundError):
        return ErrorMapping("CAPABILITY_NOT_FOUND", http.HTTPStatus.NOT_FOUND)
    if isinstance(exc, AuthorizationDeniedError):
        return ErrorMapping("PERMISSION_DENIED", http.HTTPStatus.FORBIDDEN)
    if isinstance(exc, AuthenticationError):
        return ErrorMapping("PERMISSION_DENIED", http.HTTPStatus.UNAUTHORIZED)
    if isinstance(exc, ConcurrentExecutionError):
        return ErrorMapping("EXECUTION_FAILED", http.HTTPStatus.CONFLICT)
    if isinstance(exc, IdempotencyError):
        return ErrorMapping("EXECUTION_FAILED", http.HTTPStatus.CONFLICT)
    if isinstance(exc, TimeoutError):
        return ErrorMapping("TIMEOUT_EXCEEDED", http.HTTPStatus.REQUEST_TIMEOUT)
    if isinstance(exc, SecurityEngineError):
        return ErrorMapping("EXECUTION_FAILED", http.HTTPStatus.INTERNAL_SERVER_ERROR)
    return ErrorMapping("EXECUTION_FAILED", http.HTTPStatus.INTERNAL_SERVER_ERROR)


def error_message(exc: BaseException) -> str:
    """`str(exc)` for a `KortexError` is `"[Code] message"` (see
    `KortexError.__str__`) — safe to surface: `KortexError.message` is
    never populated with secrets/tokens anywhere in the security engine
    (verified against `platform_service_contracts.md` §19.3's "no secret
    leakage" rule during this audit)."""
    return str(exc)


def error_details(exc: BaseException) -> dict[str, Any] | None:
    """No exception in the mapped hierarchy carries a structured `details`
    payload beyond `message`/`code` (confirmed during the M3 audit) — this
    returns `None` today, kept as a seam for when one does."""
    return None
