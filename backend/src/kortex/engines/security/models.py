"""
KORTEX Security Engine Domain Models (Milestone M1).

Pydantic v2 domain models and enums for the Security Engine, per
`docs/architecture/security_engine_implementation_spec.md` v3.0.0 (S5).

IMPORTANT — temporary local boundary:
The frozen architecture (`docs/architecture/shared_domain_models.md`) specifies
that models such as these should compose Universal Shared Domain Models
(`UniversalIdentity`, `UniversalClassification`, ...) as embedded sub-objects
rather than redefining them. As of this milestone, `kortex.shared` contains no
implementation of those models (verified: the package exports nothing beyond a
README and a docstring). Per the M1 authorization scope, this module does NOT
invent a substitute `kortex.shared` implementation. Instead, fields that would
eventually reference a Universal model (`principal_id`/`tenant_id` in place of
`UniversalIdentity`; `classification` in place of `UniversalClassification`)
are plain scalar/local-enum fields here. This is a deliberate, temporary
boundary that must converge with `kortex.shared` once that package is
implemented — it is not a permanent duplicate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel as SQLAlchemyBaseModel


class PrincipalType(str, Enum):
    """Category of security principal authenticated by the Security Engine.

    Per spec S6: "Local identity verification supporting User, Service
    Principal, and Agent identities."
    """

    USER = "USER"
    SERVICE_PRINCIPAL = "SERVICE_PRINCIPAL"
    AGENT = "AGENT"


class ClassificationLevel(str, Enum):
    """Security classification rating for a resource or capability.

    Mirrors the value set of `UniversalClassification.classification_level`
    (`docs/architecture/shared_domain_models.md` S9), which is not yet
    implemented in `kortex.shared` — see module docstring.
    """

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class SecurityPrincipal(BaseModel):
    """An authenticated caller: a User, Service Principal, or Agent identity.

    Per spec S5: `principal_id`, `principal_type`, `roles`, `attributes`, `tenant_id`.
    """

    principal_id: str = Field(min_length=1, description="Unique identifier of the authenticated principal.")
    principal_type: PrincipalType
    tenant_id: str = Field(min_length=1, description="Multi-tenant organization identifier.")
    roles: list[str] = Field(default_factory=list, description="RBAC role names assigned to this principal.")
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="ABAC attribute context (e.g. environment, resource ownership)."
    )


class PermissionRequirement(BaseModel):
    """A capability's declared permission requirement, evaluated during authorization.

    Per spec S5: `capability_name`, `required_permissions`, `security_classification`.
    """

    capability_name: str = Field(min_length=1, description="Canonical capability string being requested.")
    required_permissions: list[str] = Field(
        default_factory=list, description="RBAC permission keys required to execute the capability."
    )
    security_classification: ClassificationLevel = Field(
        default=ClassificationLevel.INTERNAL, description="Minimum classification level governing this capability."
    )


class AccessDecision(BaseModel):
    """The outcome of an authorization evaluation.

    Per spec S5: `is_allowed`, `decision_code`, `reason`, `evaluated_at_utc`.
    Immutable once created — an access decision is a point-in-time record,
    never mutated after evaluation.
    """

    model_config = ConfigDict(frozen=True)

    is_allowed: bool
    decision_code: str = Field(min_length=1, description="Machine-readable decision code (e.g. PERMISSION_DENIED).")
    reason: str = Field(min_length=1, description="Human-readable justification for the decision.")
    evaluated_at_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SecretEntry(BaseModel):
    """An encrypted secret vault record.

    Per spec S5: `secret_handle`, `encrypted_payload`, `algorithm`, `created_at_utc`.
    `encrypted_payload` holds the full M2 AEAD envelope (version, algorithm_id,
    key_id, nonce, tag, ciphertext — see `SecretStore`), never plaintext.
    """

    secret_handle: str = Field(min_length=1, description="Opaque handle string, e.g. 'secret:kortex/smtp_pass'.")
    encrypted_payload: bytes = Field(description="Full AEAD envelope bytes. Never plaintext.")
    algorithm: str = Field(min_length=1, description="Cryptographic algorithm identifier, e.g. 'aes-256-gcm'.")
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TokenPayload(BaseModel):
    """The claims encoded by an identity/session token, plus its detached
    Ed25519 signature (Milestone M3).

    `signature` defaults to `None` so that claims-only construction (as used
    by pre-M3 model-validation tests) remains valid — only a `TokenPayload`
    returned by `AuthenticationManager.issue_token` carries a real signature.
    `verify_token` must treat a `None` signature as an immediate fail-closed
    rejection, never as "not yet checked."
    """

    token_id: str = Field(min_length=1, description="Unique identifier for this token instance.")
    principal_id: str = Field(min_length=1)
    principal_type: PrincipalType
    tenant_id: str = Field(min_length=1)
    issued_at_utc: datetime
    expires_at_utc: datetime
    signature: bytes | None = Field(
        default=None, description="Detached Ed25519 signature over the other claim fields. Never trusted unread."
    )


class CryptographicSignature(BaseModel):
    """The result of a digital signature operation.

    Immutable once created. Never carries private key material — only the
    signature bytes and the public key required to verify it.
    """

    model_config = ConfigDict(frozen=True)

    algorithm: str = Field(min_length=1, description="Signature algorithm identifier, e.g. 'ed25519'.")
    signature: bytes = Field(description="Raw signature bytes.")
    public_key: bytes = Field(description="Raw public key bytes used to verify this signature.")
    signed_at_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SecurityMetadata(BaseModel):
    """Security classification and compliance attributes attached to a resource.

    Local M1-compatible stand-in for composing `UniversalClassification`
    (see module docstring) — not a permanent duplicate.
    """

    classification: ClassificationLevel = ClassificationLevel.INTERNAL
    compliance_flags: list[str] = Field(default_factory=list)
    encryption_required: bool = False


# -- SQLAlchemy ORM Model for IDataStore Persistence (Milestone M2) ----------


class SecretRecord(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model for an encrypted secret vault entry.

    `id`, `created_at`, `updated_at` are provided by `SQLAlchemyBaseModel`
    (the latter auto-updates on every mutation via `onupdate=`, satisfying
    Decision 6's "update updated_at_utc on replacement" without extra code).
    `encrypted_payload` holds the full AEAD envelope — never plaintext.
    """

    __tablename__ = "security_secrets"
    __table_args__ = (UniqueConstraint("tenant_id", "secret_handle", name="uq_security_secrets_tenant_handle"),)

    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    secret_handle: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="aes-256-gcm")


class PrincipalRecord(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model for a Security Engine principal (Milestone M3).

    Colocated in this module rather than in `kortex.engines.storage` or
    `kortex.engines.identity`, following the same repo-wide convention
    `SecretRecord` (above) and `kortex.engines.document.models.DocumentRecord`
    already establish: each engine owns and defines its own persistence
    models, consumed exclusively through `IDataStore` — Storage Engine
    provides the I/O abstraction, not the schema. Auto-created via the
    existing `Base.metadata.create_all()` boot path; no Alembic migration is
    required, exactly as `SecretRecord` required none.

    `credential_hash` holds a single Argon2id hash used uniformly for every
    `PrincipalType` — a password hash for `USER`, a pre-shared-secret hash for
    `SERVICE_PRINCIPAL`/`AGENT`. There is no plaintext credential field and no
    reversible/encrypted credential storage: verification is always one-way.
    `roles`/`attributes` are carried through into an authenticated
    `SecurityPrincipal` as opaque identity metadata only — nothing in
    Milestone M3 interprets, evaluates, or acts on them.
    """

    __tablename__ = "security_principals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "principal_id", "principal_type", name="uq_security_principals_tenant_principal"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    principal_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    credential_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class RolePermissionRecord(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model for a single RBAC role-to-permission grant (Milestone M4).

    Colocated in `security/models.py` per the same cross-engine convention
    `SecretRecord`/`PrincipalRecord`/`DocumentRecord` already establish.
    Auto-created via the existing `Base.metadata.create_all()` boot path; no
    Alembic migration required.

    Deliberately global, not tenant-scoped — RBAC (this table) evaluates
    static "can role X do Y" facts; tenant isolation is ABAC's
    responsibility (`abac.py`), matching the frozen spec's own division
    between "static role-to-permission matrices" (S8) and "dynamic
    environmental attributes" including `tenant_id` (S9). A role's
    permissions are the same regardless of which tenant a principal
    belongs to.

    One row per (role, permission) grant — a role's full permission set is
    the union of every row matching that role. There is no provisioning
    capability in M4 (matching M3's `PrincipalRecord` precedent): no role
    has any row here unless a caller inserts it directly via `IDataStore`,
    so RBAC fails closed (denies) for every role until explicitly granted.
    """

    __tablename__ = "security_role_permissions"
    __table_args__ = (UniqueConstraint("role", "permission", name="uq_security_role_permissions_role_permission"),)

    role: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    permission: Mapped[str] = mapped_column(String(255), nullable=False)


class UniversalAuditEntry(BaseModel):
    """The Universal Audit Entry model (Milestone M6).

    Per `docs/architecture/shared_domain_models.md` S11 &
    `docs/architecture/security_engine_implementation_spec.md` S5/S10.
    Immutable once created (`frozen=True`).
    """

    model_config = ConfigDict(frozen=True)

    audit_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Unique UUID identifying audit record."
    )
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Timestamp of action.")
    action: str = Field(min_length=1, description="Canonical capability or action name executed.")
    actor_id: str = Field(min_length=1, description="Identifier of actor performing action (User/Agent/System).")
    actor_type: str = Field(
        default="SYSTEM_ENGINE",
        description=(
            "Type of actor, per shared_domain_models.md S11: HUMAN, AI_AGENT, SYSTEM_ENGINE, or CONNECTOR. "
            "Not a Python enum here (kept as plain str, consistent with the frozen field spec), so this is "
            "advisory, not enforced — callers constructing entries directly are responsible for using this "
            "vocabulary rather than `PrincipalType`'s own (USER/SERVICE_PRINCIPAL/AGENT), which is a "
            "different, unrelated vocabulary for a different model."
        ),
    )
    tenant_id: str = Field(min_length=1, description="Multi-tenant organization identifier.")
    resource_id: str | None = Field(default=None, description="Identifier of target resource acted upon.")
    previous_state_hash: str | None = Field(
        default=None, description="SHA256 content hash of resource prior to action."
    )
    new_state_hash: str | None = Field(default=None, description="SHA256 content hash of resource after action.")
    client_ip: str | None = Field(default=None, description="Optional client IP or node location.")
    context: dict[str, Any] = Field(default_factory=dict, description="Structured execution context data.")


class AuditRecord(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model for an immutable audit log entry (Milestone M6).

    Colocated in `security/models.py` per the same cross-engine convention
    `SecretRecord`/`PrincipalRecord`/`RolePermissionRecord` already establish.
    Auto-created via the existing `Base.metadata.create_all()` boot path.
    """

    __tablename__ = "security_audit_records"

    audit_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    timestamp_utc: Mapped[datetime] = mapped_column(index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    previous_state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
