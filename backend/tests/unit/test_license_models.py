"""
Unit tests for KORTEX License Engine domain models and claim validation (M5.7).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kortex.engines.license.exceptions import (
    MalformedTokenError,
    UnsupportedScopeError,
)
from kortex.engines.license.models import (
    EntitlementSnapshot,
    LicenseScopeEnum,
    LicenseStatusEnum,
    LicenseTier,
    LicenseTokenClaims,
)


def _valid_claims_dict() -> dict:
    return {
        "schema_version": 1,
        "license_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        "issuer": "kortex.ai",
        "subject_tenant_id": "11111111-2222-4333-8444-555555555555",
        "scope": "TENANT",
        "tier": "PROFESSIONAL",
        "issued_at": "2026-01-01T00:00:00Z",
        "not_before": "2026-01-01T00:00:00Z",
        "expires_at": "2027-01-01T00:00:00Z",
        "grace_period_days": 14,
        "features": ["feature_b", "feature_a"],
        "quotas": {"max_users": 25, "max_connectors": 5},
    }


def test_claims_valid_parsing() -> None:
    data = _valid_claims_dict()
    claims = LicenseTokenClaims.model_validate(data)
    assert claims.schema_version == 1
    assert claims.tier == LicenseTier.PROFESSIONAL
    assert claims.scope == LicenseScopeEnum.TENANT
    # Features must be sorted and deduplicated
    assert claims.features == ["feature_a", "feature_b"]
    assert claims.quotas == {"max_users": 25, "max_connectors": 5}


def test_claims_extra_fields_forbidden() -> None:
    data = _valid_claims_dict()
    data["arbitrary_hack"] = "forbidden"
    with pytest.raises(ValidationError):
        LicenseTokenClaims.model_validate(data)


def test_claims_invalid_schema_version() -> None:
    data = _valid_claims_dict()
    data["schema_version"] = 2
    with pytest.raises(MalformedTokenError, match="Unsupported schema_version"):
        LicenseTokenClaims.model_validate(data)


def test_claims_invalid_license_id_uuid() -> None:
    data = _valid_claims_dict()
    data["license_id"] = "not-a-uuid"
    with pytest.raises(MalformedTokenError, match="Invalid license_id"):
        LicenseTokenClaims.model_validate(data)


def test_claims_non_v4_license_id() -> None:
    # UUIDv1
    data = _valid_claims_dict()
    data["license_id"] = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    with pytest.raises(MalformedTokenError, match="UUIDv4"):
        LicenseTokenClaims.model_validate(data)


def test_claims_invalid_subject_tenant_id() -> None:
    data = _valid_claims_dict()
    data["subject_tenant_id"] = "invalid_tenant"
    with pytest.raises(UnsupportedScopeError, match="subject_tenant_id must be a valid UUID"):
        LicenseTokenClaims.model_validate(data)


def test_claims_invalid_scope_system() -> None:
    data = _valid_claims_dict()
    data["scope"] = "SYSTEM"
    with pytest.raises(UnsupportedScopeError, match="Only scope='TENANT' is supported"):
        LicenseTokenClaims.model_validate(data)


def test_claims_invalid_chronology_issued_after_not_before() -> None:
    data = _valid_claims_dict()
    data["issued_at"] = "2026-06-01T00:00:00Z"
    data["not_before"] = "2026-01-01T00:00:00Z"
    with pytest.raises(MalformedTokenError, match=r"issued_at .* cannot be after not_before"):
        LicenseTokenClaims.model_validate(data)


def test_claims_invalid_chronology_not_before_after_expires() -> None:
    data = _valid_claims_dict()
    data["not_before"] = "2027-06-01T00:00:00Z"
    data["expires_at"] = "2027-01-01T00:00:00Z"
    with pytest.raises(MalformedTokenError, match=r"not_before .* cannot be after expires_at"):
        LicenseTokenClaims.model_validate(data)


def test_claims_invalid_grace_period() -> None:
    data = _valid_claims_dict()
    data["grace_period_days"] = -1
    with pytest.raises(ValidationError):
        LicenseTokenClaims.model_validate(data)

    data["grace_period_days"] = 91
    with pytest.raises(ValidationError):
        LicenseTokenClaims.model_validate(data)


def test_claims_invalid_feature_key() -> None:
    data = _valid_claims_dict()
    data["features"] = ["valid_feature", "Invalid-Uppercase-Feature!"]
    with pytest.raises(MalformedTokenError, match="Invalid feature key"):
        LicenseTokenClaims.model_validate(data)


def test_claims_invalid_quota_negative() -> None:
    data = _valid_claims_dict()
    data["quotas"] = {"max_users": -5}
    with pytest.raises(MalformedTokenError, match="must be a non-negative integer"):
        LicenseTokenClaims.model_validate(data)


def test_entitlement_snapshot_frozen() -> None:
    snapshot = EntitlementSnapshot(
        tenant_id="tenant-123",
        tier=LicenseTier.COMMUNITY,
        status=LicenseStatusEnum.ACTIVE,
        features=frozenset(["feat1"]),
        quotas={"users": 5},
    )
    with pytest.raises(ValidationError):
        snapshot.tier = LicenseTier.ENTERPRISE  # type: ignore[misc]
