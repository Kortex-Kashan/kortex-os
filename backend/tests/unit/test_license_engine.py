"""
Unit tests for KORTEX License Engine and ILicenseProvider (M5.7).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.db import DatabaseEngineManager
from kortex.engines.license.config import (
    _OFFICIAL_ROOT_KID,
    CANONICAL_COMMUNITY_FEATURES,
    CANONICAL_COMMUNITY_QUOTAS,
)
from kortex.engines.license.engine import LicenseEngine
from kortex.engines.license.exceptions import SecurityConfigurationError
from kortex.engines.license.models import (
    LicenseStatusEnum,
    LicenseTier,
)
from kortex.engines.license.repository import TenantScopedLicenseRepository
from kortex.engines.license.tables import LicenseRecord
from kortex.engines.storage.stores.data_store import RelationalDataStore


def _build_test_record(
    tenant_id: str,
    tier: str = "ENTERPRISE",
    status: str = "ACTIVE",
    expires_at: datetime | None = None,
    grace_days: int = 14,
) -> LicenseRecord:
    now = datetime.now(UTC)
    return LicenseRecord(
        id="rec-1",
        license_id="a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        tenant_id=tenant_id,
        active_tenant_id=tenant_id if status in ("ACTIVE", "GRACE_PERIOD") else None,
        scope="TENANT",
        tier=tier,
        status=status,
        raw_token="raw.jwt.token",
        kid=_OFFICIAL_ROOT_KID,
        signature_hex="sig_hex_123",
        issued_at=now - timedelta(days=30),
        not_before=now - timedelta(days=30),
        expires_at=expires_at,
        grace_period_days=grace_days,
        features_json=json.dumps(["doc_ai", "workflow_pro"]),
        quotas_json=json.dumps({"max_users": 100, "max_connectors": 20}),
        activated_at=now - timedelta(days=30),
        activated_by="admin-user",
        revoked_at=None,
        revocation_reason=None,
        highest_observed_at=now,
        grace_event_emitted=False,
    )


def test_production_override_raises_security_configuration_error() -> None:
    custom_roots = {_OFFICIAL_ROOT_KID: b"x" * 32}
    with pytest.raises(SecurityConfigurationError, match="Custom root keys are strictly forbidden"):
        LicenseEngine(trusted_root_keys=custom_roots, is_production=True)


def test_unlicensed_tenant_defaults_to_community() -> None:
    engine = LicenseEngine()
    snapshot = engine.get_entitlements("random-tenant-id")

    assert snapshot.tier == LicenseTier.COMMUNITY
    assert snapshot.status == LicenseStatusEnum.UNLICENSED
    assert snapshot.features == CANONICAL_COMMUNITY_FEATURES
    assert snapshot.quotas == CANONICAL_COMMUNITY_QUOTAS
    assert not snapshot.is_degraded
    assert not snapshot.clock_tamper_detected

    assert not engine.is_feature_enabled("random-tenant-id", "doc_ai")
    assert engine.is_feature_enabled("random-tenant-id", "core_workflows")
    assert engine.get_tier("random-tenant-id") == LicenseTier.COMMUNITY
    assert engine.get_quota("random-tenant-id", "max_users") == 5


def test_active_license_entitlements() -> None:
    tenant_id = "tenant-active-123"
    rec = _build_test_record(tenant_id, expires_at=datetime.now(UTC) + timedelta(days=100))

    engine = LicenseEngine()
    engine._cached_records[tenant_id] = rec

    snapshot = engine.get_entitlements(tenant_id)
    assert snapshot.tier == LicenseTier.ENTERPRISE
    assert snapshot.status == LicenseStatusEnum.ACTIVE
    assert "doc_ai" in snapshot.features
    assert snapshot.quotas["max_users"] == 100
    assert not snapshot.is_degraded
    assert not snapshot.clock_tamper_detected

    assert engine.is_feature_enabled(tenant_id, "doc_ai")
    assert engine.get_quota(tenant_id, "max_users") == 100
    assert engine.get_tier(tenant_id) == LicenseTier.ENTERPRISE


def test_perpetual_license_never_expires() -> None:
    tenant_id = "tenant-perpetual"
    rec = _build_test_record(tenant_id, expires_at=None)

    engine = LicenseEngine()
    engine._cached_records[tenant_id] = rec

    snapshot = engine.get_entitlements(tenant_id)
    assert snapshot.status == LicenseStatusEnum.ACTIVE
    assert snapshot.expires_at is None
    assert not snapshot.is_degraded


def test_grace_period_entitlement() -> None:
    tenant_id = "tenant-grace"
    now = datetime.now(UTC)
    # Expired 2 days ago, but within 14-day grace period
    rec = _build_test_record(tenant_id, expires_at=now - timedelta(days=2), grace_days=14)

    engine = LicenseEngine()
    engine._cached_records[tenant_id] = rec

    snapshot = engine.get_entitlements(tenant_id)
    assert snapshot.tier == LicenseTier.ENTERPRISE
    assert snapshot.status == LicenseStatusEnum.GRACE_PERIOD
    # Features still active during grace, but marked degraded
    assert "doc_ai" in snapshot.features
    assert snapshot.is_degraded


def test_expired_license_reverts_to_community() -> None:
    tenant_id = "tenant-expired"
    now = datetime.now(UTC)
    # Expired 20 days ago (grace period 14 days exhausted)
    rec = _build_test_record(tenant_id, expires_at=now - timedelta(days=20), grace_days=14)

    engine = LicenseEngine()
    engine._cached_records[tenant_id] = rec

    snapshot = engine.get_entitlements(tenant_id)
    assert snapshot.tier == LicenseTier.COMMUNITY
    assert snapshot.status == LicenseStatusEnum.EXPIRED
    assert snapshot.features == CANONICAL_COMMUNITY_FEATURES
    assert snapshot.quotas == CANONICAL_COMMUNITY_QUOTAS
    assert snapshot.is_degraded


def test_clock_rollback_triggers_tamper_defense() -> None:
    tenant_id = "tenant-rollback"
    rec = _build_test_record(tenant_id, expires_at=datetime.now(UTC) + timedelta(days=100))

    engine = LicenseEngine()
    engine._cached_records[tenant_id] = rec

    # Simulate watermark 2 hours into the future relative to current now
    future_watermark = datetime.now(UTC) + timedelta(hours=2)
    engine._highest_observed_at[tenant_id] = future_watermark

    snapshot = engine.get_entitlements(tenant_id)
    assert snapshot.tier == LicenseTier.COMMUNITY
    assert snapshot.clock_tamper_detected is True
    assert snapshot.is_degraded is True
    assert snapshot.features == CANONICAL_COMMUNITY_FEATURES


def test_clock_rollback_boundary_one_hour() -> None:
    tenant_id = "tenant-boundary"
    rec = _build_test_record(tenant_id, expires_at=datetime.now(UTC) + timedelta(days=100))

    engine = LicenseEngine()
    engine._cached_records[tenant_id] = rec

    # 1. Rollback of 30 minutes (< 1 hour tolerance) -> not flagged
    engine._highest_observed_at[tenant_id] = datetime.now(UTC) + timedelta(minutes=30)
    snap_within = engine.get_entitlements(tenant_id)
    assert snap_within.clock_tamper_detected is False
    assert snap_within.tier == LicenseTier.ENTERPRISE

    # 2. Rollback of 65 minutes (> 1 hour tolerance) -> flagged as tamper
    engine._highest_observed_at[tenant_id] = datetime.now(UTC) + timedelta(minutes=65)
    snap_exceeded = engine.get_entitlements(tenant_id)
    assert snap_exceeded.clock_tamper_detected is True
    assert snap_exceeded.tier == LicenseTier.COMMUNITY
    assert snap_exceeded.is_degraded is True


def test_clock_restoration_clears_tamper_defense() -> None:
    tenant_id = "tenant-restored"
    rec = _build_test_record(tenant_id, expires_at=datetime.now(UTC) + timedelta(days=100))

    engine = LicenseEngine()
    engine._cached_records[tenant_id] = rec

    # Tamper active
    engine._highest_observed_at[tenant_id] = datetime.now(UTC) + timedelta(hours=2)
    snap1 = engine.get_entitlements(tenant_id)
    assert snap1.clock_tamper_detected is True

    # Clock restored to match or exceed watermark
    engine._highest_observed_at[tenant_id] = datetime.now(UTC)
    snap2 = engine.get_entitlements(tenant_id)
    assert snap2.clock_tamper_detected is False
    assert snap2.tier == LicenseTier.ENTERPRISE


@pytest.mark.asyncio
async def test_engine_lifecycle(tmp_path: Path) -> None:
    db_file = tmp_path / "test_engine_life.db"
    db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_file}")
    await db_manager.create_all_tables()
    data_store = RelationalDataStore(db_manager)
    repo = TenantScopedLicenseRepository(data_store)

    engine = LicenseEngine(repository=repo)
    kernel_mock = MagicMock()
    kernel_mock.get_engine.side_effect = lambda name: MagicMock()

    assert engine.state == EngineState.UNINITIALIZED
    await engine.initialize(kernel_mock)
    assert engine.state == EngineState.READY

    await engine.start()
    assert engine.state == EngineState.RUNNING

    health = engine.health()
    assert health["status"] == "healthy"
    assert health["state"] == "RUNNING"

    await engine.stop()
    assert engine.state == EngineState.STOPPED
