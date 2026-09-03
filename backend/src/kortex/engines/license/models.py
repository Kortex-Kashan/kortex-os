"""
KORTEX License Engine Domain Models (Milestone M5.7).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kortex.engines.license.config import (
    DEFAULT_GRACE_PERIOD_DAYS,
    MAX_GRACE_PERIOD_DAYS,
)
from kortex.engines.license.exceptions import MalformedTokenError, UnsupportedScopeError

_UUID4_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_KEY_REGEX = re.compile(r"^[a-z0-9_]+$")


class LicenseTier(str, Enum):
    """Commercial entitlement tier."""

    COMMUNITY = "COMMUNITY"
    PROFESSIONAL = "PROFESSIONAL"
    ENTERPRISE = "ENTERPRISE"


class LicenseStatusEnum(str, Enum):
    """License lifecycle state."""

    ACTIVE = "ACTIVE"
    GRACE_PERIOD = "GRACE_PERIOD"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"
    UNLICENSED = "UNLICENSED"


class LicenseScopeEnum(str, Enum):
    """Supported license scope (M5.7 supports TENANT only)."""

    TENANT = "TENANT"


class LicenseTokenClaims(BaseModel):
    """Immutable, signed claims contained in a valid license token payload."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, description="Token schema version")
    license_id: str = Field(..., description="Unique UUIDv4 identifier of the license")
    issuer: str = Field(..., min_length=1, max_length=128, description="License issuing authority")
    subject_tenant_id: str = Field(..., description="Target tenant UUIDv4")
    scope: LicenseScopeEnum = Field(default=LicenseScopeEnum.TENANT, description="License scope")
    tier: LicenseTier = Field(..., description="Commercial entitlement tier")
    issued_at: datetime = Field(..., description="Issuance timestamp (UTC)")
    not_before: datetime = Field(..., description="Start of validity window (UTC)")
    expires_at: datetime | None = Field(default=None, description="Expiration timestamp (UTC, None=perpetual)")
    grace_period_days: int = Field(
        default=DEFAULT_GRACE_PERIOD_DAYS,
        ge=0,
        le=MAX_GRACE_PERIOD_DAYS,
        description="Grace period in days",
    )
    features: list[str] = Field(default_factory=list, description="Explicitly granted feature identifiers")
    quotas: dict[str, int] = Field(default_factory=dict, description="Capacity quotas")

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, v: int) -> int:
        if v != 1:
            raise MalformedTokenError(f"Unsupported schema_version {v}, expected 1")
        return v

    @field_validator("license_id")
    @classmethod
    def _validate_license_id(cls, v: str) -> str:
        try:
            val = uuid.UUID(v)
            if val.version != 4:
                raise MalformedTokenError(f"license_id must be a UUIDv4, got UUIDv{val.version}")
        except (ValueError, TypeError) as exc:
            raise MalformedTokenError(f"Invalid license_id: {v}") from exc
        return str(val).lower()

    @field_validator("subject_tenant_id")
    @classmethod
    def _validate_subject_tenant_id(cls, v: str) -> str:
        try:
            val = uuid.UUID(v)
        except (ValueError, TypeError) as exc:
            raise UnsupportedScopeError(f"subject_tenant_id must be a valid UUID, got: {v}") from exc
        return str(val).lower()

    @field_validator("scope", mode="before")
    @classmethod
    def _validate_scope(cls, v: Any) -> LicenseScopeEnum:
        if isinstance(v, str):
            if v != "TENANT":
                raise UnsupportedScopeError(f"Only scope='TENANT' is supported in M5.7, got: {v}")
            return LicenseScopeEnum.TENANT
        if v != LicenseScopeEnum.TENANT:
            raise UnsupportedScopeError(f"Only scope='TENANT' is supported in M5.7, got: {v}")
        return LicenseScopeEnum.TENANT

    @field_validator("issued_at", "not_before", "expires_at")
    @classmethod
    def _validate_tz(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @field_validator("features")
    @classmethod
    def _validate_features(cls, v: list[str]) -> list[str]:
        seen = set()
        cleaned = []
        for item in v:
            if not isinstance(item, str) or not _KEY_REGEX.match(item):
                raise MalformedTokenError(f"Invalid feature key: {item!r}. Must match '^[a-z0-9_]+$'")
            if item not in seen:
                seen.add(item)
                cleaned.append(item)
        cleaned.sort()
        return cleaned

    @field_validator("quotas")
    @classmethod
    def _validate_quotas(cls, v: dict[str, int]) -> dict[str, int]:
        cleaned: dict[str, int] = {}
        for k, val in v.items():
            if not isinstance(k, str) or not _KEY_REGEX.match(k):
                raise MalformedTokenError(f"Invalid quota key: {k!r}. Must match '^[a-z0-9_]+$'")
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                raise MalformedTokenError(f"Quota for {k!r} must be a non-negative integer, got {val!r}")
            cleaned[k] = val
        return cleaned

    @model_validator(mode="after")
    def _validate_chronology(self) -> LicenseTokenClaims:
        if self.issued_at > self.not_before:
            raise MalformedTokenError(
                f"issued_at ({self.issued_at.isoformat()}) cannot be after not_before ({self.not_before.isoformat()})"
            )
        if self.expires_at is not None and self.not_before > self.expires_at:
            raise MalformedTokenError(
                f"not_before ({self.not_before.isoformat()}) cannot be after expires_at ({self.expires_at.isoformat()})"
            )
        return self


class EntitlementSnapshot(BaseModel):
    """Immutable entitlement snapshot for one tenant at a specific point in time."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    tier: LicenseTier
    status: LicenseStatusEnum
    features: frozenset[str]
    quotas: dict[str, int]
    expires_at: datetime | None = None
    is_degraded: bool = False
    clock_tamper_detected: bool = False


# -- Capability Transport Models --------------------------------------------


class TokenVerifyRequest(BaseModel):
    """Input parameters for kortex.license.token.verify."""

    token: str = Field(..., min_length=1, description="Raw dot-separated license token string")


class TokenVerifyResponse(BaseModel):
    """Output for kortex.license.token.verify."""

    is_valid: bool = Field(default=True, description="True if cryptographic signature is valid")
    claims: LicenseTokenClaims = Field(..., description="Verified token claims")


class LicenseActivateRequest(BaseModel):
    """Input parameters for kortex.license.activation.apply."""

    token: str = Field(..., min_length=1, description="Raw dot-separated license token string to activate")


class LicenseRevokeRequest(BaseModel):
    """Input parameters for kortex.license.activation.revoke."""

    reason: str = Field(..., min_length=1, max_length=500, description="Reason for license revocation")


class LicenseStatusResponse(BaseModel):
    """Output for activation, revocation, and status inspection capabilities."""

    tenant_id: str
    tier: str
    status: str
    expires_at: str | None = None
    features: list[str]
    quotas: dict[str, int]
    grace_period_remaining_days: int | None = None
    is_degraded: bool = False
    clock_tamper_detected: bool = False
