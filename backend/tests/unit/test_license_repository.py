"""
Unit tests for KORTEX License Engine Repository and Persistence (M5.7).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.engines.license.exceptions import (
    LicenseConflictError,
    TerminalLicenseError,
)
from kortex.engines.license.models import (
    LicenseScopeEnum,
    LicenseTier,
    LicenseTokenClaims,
)
from kortex.engines.license.repository import TenantScopedLicenseRepository
from kortex.engines.storage.stores.data_store import RelationalDataStore


def _build_claims(license_id: str, tenant_id: str, tier: LicenseTier = LicenseTier.ENTERPRISE) -> LicenseTokenClaims:
    return LicenseTokenClaims(
        schema_version=1,
        license_id=license_id,
        issuer="kortex.ai",
        subject_tenant_id=tenant_id,
        scope=LicenseScopeEnum.TENANT,
        tier=tier,
        issued_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        not_before=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC),
        grace_period_days=14,
        features=["feat_a", "feat_b"],
        quotas={"max_users": 10},
    )


@pytest.fixture
async def repo(tmp_path: Path) -> TenantScopedLicenseRepository:
    db_file = tmp_path / "test_repo.db"
    db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_file}")
    await db_manager.create_all_tables()
    data_store = RelationalDataStore(db_manager)
    return TenantScopedLicenseRepository(data_store)


@pytest.mark.asyncio
async def test_apply_activation_first_time(repo: TenantScopedLicenseRepository) -> None:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    claims = _build_claims("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", tenant_id)

    rec, is_reapp = await repo.apply_activation(
        claims=claims,
        raw_token="token_bytes_1",
        kid="kortex-root-2026",
        signature_hex="sig1",
        activated_by="user-admin",
    )

    assert not is_reapp
    assert rec.license_id == claims.license_id
    assert rec.tenant_id == tenant_id
    assert rec.active_tenant_id == tenant_id
    assert rec.status == "ACTIVE"

    active = await repo.get_active_license(tenant_id)
    assert active is not None
    assert active.license_id == claims.license_id


@pytest.mark.asyncio
async def test_apply_activation_idempotent(repo: TenantScopedLicenseRepository) -> None:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    claims = _build_claims("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", tenant_id)

    rec1, is_reapp1 = await repo.apply_activation(
        claims=claims,
        raw_token="token_bytes_1",
        kid="kortex-root-2026",
        signature_hex="sig1",
        activated_by="user-admin",
    )
    assert not is_reapp1

    rec2, is_reapp2 = await repo.apply_activation(
        claims=claims,
        raw_token="token_bytes_1",
        kid="kortex-root-2026",
        signature_hex="sig1",
        activated_by="user-admin",
    )
    assert is_reapp2
    assert rec2.id == rec1.id


@pytest.mark.asyncio
async def test_apply_activation_conflict_divergent_token(repo: TenantScopedLicenseRepository) -> None:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    claims = _build_claims("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", tenant_id)

    await repo.apply_activation(
        claims=claims,
        raw_token="token_bytes_1",
        kid="kortex-root-2026",
        signature_hex="sig1",
        activated_by="user-admin",
    )

    # Same license_id, different raw_token
    with pytest.raises(LicenseConflictError, match="divergent claims or token bytes"):
        await repo.apply_activation(
            claims=claims,
            raw_token="divergent_token_bytes",
            kid="kortex-root-2026",
            signature_hex="sig2",
            activated_by="user-admin",
        )


@pytest.mark.asyncio
async def test_apply_activation_renewal_supersedes_old(repo: TenantScopedLicenseRepository) -> None:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    claims1 = _build_claims("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", tenant_id, tier=LicenseTier.PROFESSIONAL)
    claims2 = _build_claims("b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e", tenant_id, tier=LicenseTier.ENTERPRISE)

    rec1, _ = await repo.apply_activation(
        claims=claims1,
        raw_token="token_1",
        kid="kortex-root-2026",
        signature_hex="sig1",
        activated_by="user-admin",
    )
    assert rec1.status == "ACTIVE"

    rec2, _ = await repo.apply_activation(
        claims=claims2,
        raw_token="token_2",
        kid="kortex-root-2026",
        signature_hex="sig2",
        activated_by="user-admin",
    )
    assert rec2.status == "ACTIVE"
    assert rec2.active_tenant_id == tenant_id

    # The current active license must now be rec2
    active = await repo.get_active_license(tenant_id)
    assert active is not None
    assert active.license_id == claims2.license_id
    assert active.tier == "ENTERPRISE"


@pytest.mark.asyncio
async def test_apply_activation_cannot_reactivate_terminal(repo: TenantScopedLicenseRepository) -> None:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    claims = _build_claims("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", tenant_id)

    await repo.apply_activation(
        claims=claims,
        raw_token="token_1",
        kid="kortex-root-2026",
        signature_hex="sig1",
        activated_by="user-admin",
    )

    # Revoke it to make it terminal
    revoked = await repo.revoke_license(tenant_id, reason="Testing", revoked_by="admin")
    assert revoked is not None
    assert revoked.status == "REVOKED"

    # Reactivation attempt must fail with TerminalLicenseError
    with pytest.raises(TerminalLicenseError, match="terminal state 'REVOKED'"):
        await repo.apply_activation(
            claims=claims,
            raw_token="token_1",
            kid="kortex-root-2026",
            signature_hex="sig1",
            activated_by="user-admin",
        )


@pytest.mark.asyncio
async def test_mark_grace_event_emitted(repo: TenantScopedLicenseRepository) -> None:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    claims = _build_claims("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", tenant_id)

    await repo.apply_activation(
        claims=claims,
        raw_token="token_1",
        kid="kortex-root-2026",
        signature_hex="sig1",
        activated_by="user-admin",
    )

    # First mark: returns True
    assert await repo.mark_grace_event_emitted(claims.license_id) is True
    # Second mark: returns False (deduplication)
    assert await repo.mark_grace_event_emitted(claims.license_id) is False


@pytest.mark.asyncio
async def test_update_highest_observed_at(repo: TenantScopedLicenseRepository) -> None:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    claims = _build_claims("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", tenant_id)

    await repo.apply_activation(
        claims=claims,
        raw_token="token_1",
        kid="kortex-root-2026",
        signature_hex="sig1",
        activated_by="user-admin",
    )

    future_ts = datetime(2027, 6, 1, 12, 0, 0, tzinfo=UTC)
    await repo.update_highest_observed_at(tenant_id, future_ts)

    active = await repo.get_active_license(tenant_id)
    assert active is not None
    assert active.highest_observed_at.year == 2027
    assert active.highest_observed_at.month == 6
