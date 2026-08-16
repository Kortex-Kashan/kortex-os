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

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


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
    roles: List[str] = Field(default_factory=list, description="RBAC role names assigned to this principal.")
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="ABAC attribute context (e.g. environment, resource ownership)."
    )


class PermissionRequirement(BaseModel):
    """A capability's declared permission requirement, evaluated during authorization.

    Per spec S5: `capability_name`, `required_permissions`, `security_classification`.
    """

    capability_name: str = Field(min_length=1, description="Canonical capability string being requested.")
    required_permissions: List[str] = Field(
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
    evaluated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SecretEntry(BaseModel):
    """An encrypted secret vault record.

    Per spec S5: `secret_handle`, `encrypted_payload`, `algorithm`, `created_at_utc`.
    `encrypted_payload` holds the full M2 AEAD envelope (version, algorithm_id,
    key_id, nonce, tag, ciphertext — see `SecretStore`), never plaintext.
    """

    secret_handle: str = Field(min_length=1, description="Opaque handle string, e.g. 'secret:kortex/smtp_pass'.")
    encrypted_payload: bytes = Field(description="Full AEAD envelope bytes. Never plaintext.")
    algorithm: str = Field(min_length=1, description="Cryptographic algorithm identifier, e.g. 'aes-256-gcm'.")
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TokenPayload(BaseModel):
    """The claims encoded by an identity/session token.

    Token issuance, signing, and verification are implemented in a later
    milestone (M3) — this is the data contract for what a token asserts.
    """

    token_id: str = Field(min_length=1, description="Unique identifier for this token instance.")
    principal_id: str = Field(min_length=1)
    principal_type: PrincipalType
    tenant_id: str = Field(min_length=1)
    issued_at_utc: datetime
    expires_at_utc: datetime


class CryptographicSignature(BaseModel):
    """The result of a digital signature operation.

    Immutable once created. Never carries private key material — only the
    signature bytes and the public key required to verify it.
    """

    model_config = ConfigDict(frozen=True)

    algorithm: str = Field(min_length=1, description="Signature algorithm identifier, e.g. 'ed25519'.")
    signature: bytes = Field(description="Raw signature bytes.")
    public_key: bytes = Field(description="Raw public key bytes used to verify this signature.")
    signed_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SecurityMetadata(BaseModel):
    """Security classification and compliance attributes attached to a resource.

    Local M1-compatible stand-in for composing `UniversalClassification`
    (see module docstring) — not a permanent duplicate.
    """

    classification: ClassificationLevel = ClassificationLevel.INTERNAL
    compliance_flags: List[str] = Field(default_factory=list)
    encryption_required: bool = False


# -- SQLAlchemy ORM Model for IDataStore Persistence (Milestone M2) ----------

from sqlalchemy import LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel as SQLAlchemyBaseModel


class SecretRecord(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model for an encrypted secret vault entry.

    `id`, `created_at`, `updated_at` are provided by `SQLAlchemyBaseModel`
    (the latter auto-updates on every mutation via `onupdate=`, satisfying
    Decision 6's "update updated_at_utc on replacement" without extra code).
    `encrypted_payload` holds the full AEAD envelope — never plaintext.
    """

    __tablename__ = "security_secrets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "secret_handle", name="uq_security_secrets_tenant_handle"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    secret_handle: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="aes-256-gcm")
