"""
Unit tests for KORTEX License Engine Capability Handlers (M5.7).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.license.config import (
    _OFFICIAL_ROOT_KID,
)
from kortex.engines.license.crypto import LicenseCryptoEngine
from kortex.engines.license.engine import LicenseEngine
from kortex.engines.license.exceptions import (
    LicenseExpiredError,
    LicenseNotYetValidError,
    TenantMismatchError,
)
from kortex.engines.license.models import (
    LicenseScopeEnum,
    LicenseTier,
    LicenseTokenClaims,
)
from kortex.engines.license.repository import TenantScopedLicenseRepository
from kortex.engines.security.exceptions import AuthenticationError
from kortex.engines.security.models import PrincipalType, SecurityPrincipal
from kortex.engines.storage.stores.data_store import RelationalDataStore

# Deterministic test keys
_TEST_SEED = bytes.fromhex("3f34ef585ba20e9dc048c2d9f6ce9ab55515dacc578f721d78635ad38af42782")
_TEST_PRIV = Ed25519PrivateKey.from_private_bytes(_TEST_SEED)
_TEST_PRIV_BYTES = _TEST_PRIV.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
_TEST_PUB_BYTES = _TEST_PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _issue_token(
    crypto_engine: LicenseCryptoEngine,
    tenant_id: str,
    license_id: str = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    tier: LicenseTier = LicenseTier.ENTERPRISE,
    issued_at: datetime | None = None,
    not_before: datetime | None = None,
    expires_at: datetime | None = None,
    grace_days: int = 14,
    features: list[str] | None = None,
    quotas: dict[str, int] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims = LicenseTokenClaims(
        schema_version=1,
        license_id=license_id,
        issuer="kortex.ai",
        subject_tenant_id=tenant_id,
        scope=LicenseScopeEnum.TENANT,
        tier=tier,
        issued_at=issued_at or (now - timedelta(days=1)),
        not_before=not_before or (now - timedelta(days=1)),
        expires_at=expires_at or (now + timedelta(days=365)),
        grace_period_days=grace_days,
        features=features or ["feat_one", "feat_two"],
        quotas=quotas or {"max_users": 50},
    )
    return crypto_engine.encode_token(claims, _TEST_PRIV_BYTES, kid=_OFFICIAL_ROOT_KID)


def _make_context(
    tenant_id: str,
    principal_id: str = "test-admin",
    capability_name: str = "kortex.license.activation.apply",
) -> CapabilityExecutionContext:
    principal = SecurityPrincipal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        principal_type=PrincipalType.USER,
        roles=["TENANT_ADMIN"],
    )
    return CapabilityExecutionContext(
        request_id="req-123",
        correlation_id="corr-123",
        capability_name=capability_name,
        principal=principal,
        tenant_id=tenant_id,
        session_token=None,
    )


@pytest.fixture
async def setup_engine(tmp_path: Path) -> LicenseEngine:
    db_file = tmp_path / "test_caps.db"
    db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_file}")
    await db_manager.create_all_tables()
    data_store = RelationalDataStore(db_manager)
    repo = TenantScopedLicenseRepository(data_store)

    crypto_engine = LicenseCryptoEngine(trusted_root_keys={_OFFICIAL_ROOT_KID: _TEST_PUB_BYTES})
    engine = LicenseEngine(crypto_engine=crypto_engine, repository=repo)
    return engine


def test_token_verify_stateless(setup_engine: LicenseEngine) -> None:
    engine = setup_engine
    tenant_id = "11111111-2222-4333-8444-555555555555"
    token = _issue_token(engine.crypto_engine, tenant_id)

    response = engine.verify_token(token)
    assert response.is_valid is True
    assert response.claims.subject_tenant_id == tenant_id
    assert response.claims.tier == LicenseTier.ENTERPRISE

    # Verify no cache mutation and no DB mutation
    assert tenant_id not in engine._cached_records


@pytest.mark.asyncio
async def test_apply_activation_success(setup_engine: LicenseEngine) -> None:
    engine = setup_engine
    tenant_id = "11111111-2222-4333-8444-555555555555"
    token = _issue_token(engine.crypto_engine, tenant_id)
    ctx = _make_context(tenant_id)

    res = await engine.apply_activation(token, execution_context=ctx)
    assert res.tenant_id == tenant_id
    assert res.tier == "ENTERPRISE"
    assert res.status == "ACTIVE"
    assert "feat_one" in res.features

    # Fast path check
    assert engine.is_feature_enabled(tenant_id, "feat_one")
    assert engine.get_tier(tenant_id) == LicenseTier.ENTERPRISE


@pytest.mark.asyncio
async def test_apply_activation_tenant_mismatch_rejected(setup_engine: LicenseEngine) -> None:
    engine = setup_engine
    token_tenant = "11111111-2222-4333-8444-555555555555"
    token = _issue_token(engine.crypto_engine, token_tenant)

    # Caller belongs to a different tenant
    caller_tenant = "99999999-9999-4999-8999-999999999999"
    ctx = _make_context(caller_tenant)

    with pytest.raises(TenantMismatchError, match="does not match caller tenant"):
        await engine.apply_activation(token, execution_context=ctx)


@pytest.mark.asyncio
async def test_apply_activation_missing_context(setup_engine: LicenseEngine) -> None:
    engine = setup_engine
    token = _issue_token(engine.crypto_engine, "11111111-2222-4333-8444-555555555555")

    with pytest.raises(AuthenticationError, match="execution context is missing"):
        await engine.apply_activation(token, execution_context=None)


@pytest.mark.asyncio
async def test_apply_activation_not_yet_valid(setup_engine: LicenseEngine) -> None:
    engine = setup_engine
    tenant_id = "11111111-2222-4333-8444-555555555555"
    future_date = datetime.now(UTC) + timedelta(days=30)
    token = _issue_token(
        engine.crypto_engine,
        tenant_id,
        issued_at=datetime.now(UTC),
        not_before=future_date,
    )
    ctx = _make_context(tenant_id)

    with pytest.raises(LicenseNotYetValidError, match="not yet valid"):
        await engine.apply_activation(token, execution_context=ctx)


@pytest.mark.asyncio
async def test_apply_activation_already_expired(setup_engine: LicenseEngine) -> None:
    engine = setup_engine
    tenant_id = "11111111-2222-4333-8444-555555555555"
    expired_date = datetime.now(UTC) - timedelta(days=60)
    token = _issue_token(
        engine.crypto_engine,
        tenant_id,
        issued_at=datetime.now(UTC) - timedelta(days=120),
        not_before=datetime.now(UTC) - timedelta(days=120),
        expires_at=expired_date,
        grace_days=14,
    )
    ctx = _make_context(tenant_id)

    with pytest.raises(LicenseExpiredError, match="grace period has elapsed"):
        await engine.apply_activation(token, execution_context=ctx)


@pytest.mark.asyncio
async def test_apply_activation_idempotent_reapplication(setup_engine: LicenseEngine) -> None:
    engine = setup_engine
    tenant_id = "11111111-2222-4333-8444-555555555555"
    token = _issue_token(engine.crypto_engine, tenant_id)
    ctx = _make_context(tenant_id)

    res1 = await engine.apply_activation(token, execution_context=ctx)
    res2 = await engine.apply_activation(token, execution_context=ctx)
    assert res1.status == "ACTIVE"
    assert res2.status == "ACTIVE"


@pytest.mark.asyncio
async def test_apply_activation_renewal_supersedes(setup_engine: LicenseEngine) -> None:
    engine = setup_engine
    tenant_id = "11111111-2222-4333-8444-555555555555"
    token1 = _issue_token(
        engine.crypto_engine,
        tenant_id,
        license_id="11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        tier=LicenseTier.PROFESSIONAL,
    )
    token2 = _issue_token(
        engine.crypto_engine,
        tenant_id,
        license_id="22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        tier=LicenseTier.ENTERPRISE,
    )
    ctx = _make_context(tenant_id)

    await engine.apply_activation(token1, execution_context=ctx)
    assert engine.get_tier(tenant_id) == LicenseTier.PROFESSIONAL

    await engine.apply_activation(token2, execution_context=ctx)
    assert engine.get_tier(tenant_id) == LicenseTier.ENTERPRISE


@pytest.mark.asyncio
async def test_revoke_activation_drops_to_community(setup_engine: LicenseEngine) -> None:
    engine = setup_engine
    tenant_id = "11111111-2222-4333-8444-555555555555"
    token = _issue_token(engine.crypto_engine, tenant_id)
    ctx = _make_context(tenant_id)

    await engine.apply_activation(token, execution_context=ctx)
    assert engine.get_tier(tenant_id) == LicenseTier.ENTERPRISE

    rev_res = await engine.revoke_activation(reason="Testing revocation", execution_context=ctx)
    assert rev_res.status == "UNLICENSED"
    assert rev_res.tier == "COMMUNITY"

    # Effective tier is now COMMUNITY
    assert engine.get_tier(tenant_id) == LicenseTier.COMMUNITY


@pytest.mark.asyncio
async def test_get_status_capability(setup_engine: LicenseEngine) -> None:
    engine = setup_engine
    tenant_id = "11111111-2222-4333-8444-555555555555"
    token = _issue_token(engine.crypto_engine, tenant_id)
    ctx = _make_context(tenant_id)

    await engine.apply_activation(token, execution_context=ctx)

    status_res = engine.get_status(execution_context=ctx)
    assert status_res.tenant_id == tenant_id
    assert status_res.status == "ACTIVE"
    assert status_res.tier == "ENTERPRISE"
