"""Wire-level Pydantic models for the M3 IPC Bridge presentation layer.

These mirror `docs/architecture/phase3_desktop_architecture.md` §8.2-8.3
(`IpcCapabilityRequest` / `IpcResultEnvelope` / `IpcError`) field-for-field,
including camelCase wire names, so the JSON body crossing this HTTP boundary
is byte-identical in shape to the JSON already crossing the Tauri boundary
between the frontend and Rust (`apps/desktop/src/ipc/client.ts`) — per §8.1,
the IPC layer is a transport, not a second contract system.

Neither `CapabilityRequest` (`kortex.core.dispatch`) nor a `UniversalResult`
type exists with this shape today (the former uses `session_token`/`context`
or `caller_identity`/`tenant_id` for the ratified spec version, the latter
was never implemented anywhere in `backend/src`) — this module is that
translation boundary, not a replacement for either.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

IpcErrorCategory = Literal[
    "CAPABILITY_NOT_FOUND",
    "PERMISSION_DENIED",
    "VALIDATION_FAILED",
    "TIMEOUT_EXCEEDED",
    "SERVICE_UNAVAILABLE",
    "EXECUTION_FAILED",
]
"""The exact six categories from `platform_service_contracts.md` §7 and the
frontend's `IpcErrorCategory` — no seventh value is introduced here. See
`errors.py` module docstring for why `AuthenticationError` collapses into
`PERMISSION_DENIED` rather than gaining its own category."""

ResultStatus = Literal["SUCCESS", "FAILURE", "PARTIAL_SUCCESS", "CANCELLED"]


class _WireModel(BaseModel):
    """Base for every model on this HTTP boundary: camelCase on the wire,
    snake_case in Python, via Pydantic's alias generator (not by hand,
    which would drift as fields are added)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class IpcCapabilityRequest(_WireModel):
    """Exact mirror of `apps/desktop/src/ipc/client.ts`'s `IpcCapabilityRequest`."""

    request_id: str
    capability_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    idempotency_key: str | None = None
    timeout_ms: int | None = None


class IpcError(_WireModel):
    """Exact mirror of `client.ts`'s `IpcError`."""

    category: IpcErrorCategory
    message: str
    details: dict[str, Any] | None = None
    correlation_id: str


class IpcResultEnvelope(_WireModel):
    """Exact mirror of `client.ts`'s `IpcResultEnvelope`.

    `session_token` is deliberately NOT part of this shape's TypeScript
    counterpart — it is attached only as an extra top-level field on the
    raw HTTP JSON response (see `main.py`), read and stripped by Rust
    before anything reaches the webview, and is never serialized here so
    that constructing an `IpcResultEnvelope` can never accidentally leak it.
    """

    request_id: str
    correlation_id: str
    status: ResultStatus
    payload: dict[str, Any] | None = None
    errors: list[IpcError] = Field(default_factory=list)
    warnings: list[IpcError] = Field(default_factory=list)
    execution_duration_ms: float
