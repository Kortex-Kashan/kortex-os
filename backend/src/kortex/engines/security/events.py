"""Immutable System Event Payload Definitions for KORTEX OS Security Engine (Milestone M6).

This module defines immutable system event payload schemas emitted by the Security Engine
across authentication, authorization, secret operations, signature verification, and audit logging.
All payloads are frozen Pydantic models with deterministic topic bindings.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SecurityBaseEvent(BaseModel):
    """Base class for all immutable Security Engine system event payloads."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"sec-evt-{uuid.uuid4().hex}")
    event_type: str
    tenant_id: str
    timestamp: str = Field(default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat())


class SecurityAuditEvent(SecurityBaseEvent):
    """Dispatched when a universal security audit record is created."""

    event_type: Literal["kortex.event.security.audit"] = "kortex.event.security.audit"
    audit_id: str
    action: str
    actor_id: str
    actor_type: str
    resource_id: str | None = None
    previous_state_hash: str | None = None
    new_state_hash: str | None = None
    client_ip: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class SecurityAuthSuccessEvent(SecurityBaseEvent):
    """Dispatched when an authentication attempt succeeds."""

    event_type: Literal["kortex.event.security.auth.success"] = "kortex.event.security.auth.success"
    principal_id: str
    principal_type: str


class SecurityAuthFailureEvent(SecurityBaseEvent):
    """Dispatched when an authentication attempt fails."""

    event_type: Literal["kortex.event.security.auth.failure"] = "kortex.event.security.auth.failure"
    principal_id: str
    reason: str


class SecurityAccessGrantedEvent(SecurityBaseEvent):
    """Dispatched when a capability request is authorized."""

    event_type: Literal["kortex.event.security.access.granted"] = "kortex.event.security.access.granted"
    principal_id: str
    capability_name: str
    decision_code: str


class SecurityAccessDeniedEvent(SecurityBaseEvent):
    """Dispatched when a capability request is denied."""

    event_type: Literal["kortex.event.security.access.denied"] = "kortex.event.security.access.denied"
    principal_id: str
    capability_name: str
    reason: str
    decision_code: str


class SecuritySecretAccessedEvent(SecurityBaseEvent):
    """Dispatched when a secret is retrieved from the vault."""

    event_type: Literal["kortex.event.security.secret.accessed"] = "kortex.event.security.secret.accessed"
    secret_handle: str


class SecuritySecretModifiedEvent(SecurityBaseEvent):
    """Dispatched when a secret is written or deleted in the vault."""

    event_type: Literal["kortex.event.security.secret.modified"] = "kortex.event.security.secret.modified"
    secret_handle: str
    operation: Literal["PUT", "DELETE"]


class SecuritySignatureVerifiedEvent(SecurityBaseEvent):
    """Dispatched when a cryptographic signature verification is performed."""

    event_type: Literal["kortex.event.security.signature.verified"] = "kortex.event.security.signature.verified"
    is_valid: bool
    algorithm: str


__all__ = [
    "SecurityAccessDeniedEvent",
    "SecurityAccessGrantedEvent",
    "SecurityAuditEvent",
    "SecurityAuthFailureEvent",
    "SecurityAuthSuccessEvent",
    "SecurityBaseEvent",
    "SecuritySecretAccessedEvent",
    "SecuritySecretModifiedEvent",
    "SecuritySignatureVerifiedEvent",
]
