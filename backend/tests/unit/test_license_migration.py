"""
Unit tests for License Engine Alembic migration (b4e89f123c5a).

Verifies the complete migration lifecycle:
existing database (baseline 81d6d64c51ba)
→ migration (upgrade to head b4e89f123c5a)
→ kortex_licenses table verification
→ engine startup on migrated schema
→ activation of license
→ entitlement query
→ downgrade behavior (back to baseline 81d6d64c51ba)
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.license.config import _OFFICIAL_ROOT_KID
from kortex.engines.license.crypto import LicenseCryptoEngine
from kortex.engines.license.engine import LicenseEngine
from kortex.engines.license.models import (
    LicenseScopeEnum,
    LicenseStatusEnum,
    LicenseTier,
    LicenseTokenClaims,
)
from kortex.engines.license.repository import TenantScopedLicenseRepository
from kortex.engines.security.models import PrincipalType, SecurityPrincipal
from kortex.engines.storage.stores.data_store import RelationalDataStore

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_ALEMBIC_SCRIPT_DIR = _BACKEND_DIR / "alembic"

# Test key material
_TEST_SEED = bytes.fromhex("3f34ef585ba20e9dc048c2d9f6ce9ab55515dacc578f721d78635ad38af42782")
_TEST_PRIV = Ed25519PrivateKey.from_private_bytes(_TEST_SEED)
_TEST_PRIV_BYTES = _TEST_PRIV.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
_TEST_PUB_BYTES = _TEST_PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _alembic_cfg(db_path: str) -> Config:
    os.environ["KORTEX_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_SCRIPT_DIR))
    return cfg


def _upgrade_to_baseline(db_path: str) -> None:
    command.upgrade(_alembic_cfg(db_path), "81d6d64c51ba")


def _upgrade_to_head(db_path: str) -> None:
    command.upgrade(_alembic_cfg(db_path), "head")


def _downgrade_to_baseline(db_path: str) -> None:
    command.downgrade(_alembic_cfg(db_path), "-1")


def _issue_test_token(crypto_engine: LicenseCryptoEngine, tenant_id: str) -> str:
    now = datetime.now(UTC)
    claims = LicenseTokenClaims(
        schema_version=1,
        license_id=str(uuid.uuid4()),
        issuer="kortex.ai",
        subject_tenant_id=tenant_id,
        scope=LicenseScopeEnum.TENANT,
        tier=LicenseTier.ENTERPRISE,
        issued_at=now - timedelta(days=1),
        not_before=now - timedelta(days=1),
        expires_at=now + timedelta(days=365),
        grace_period_days=14,
        features=["migration_test_feature"],
        quotas={"max_users": 200},
    )
    return crypto_engine.encode_token(claims, _TEST_PRIV_BYTES, kid=_OFFICIAL_ROOT_KID)


@pytest.mark.asyncio
async def test_license_table_migration_full_lifecycle(tmp_path: Path) -> None:
    db_file = tmp_path / "test_migration_lifecycle.db"
    db_path = str(db_file).replace("\\", "/")

    # Step 1: Existing database at baseline revision 81d6d64c51ba
    await asyncio.to_thread(_upgrade_to_baseline, db_path)

    engine_baseline = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine_baseline.connect() as conn:
        baseline_tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "workflow_definitions" in baseline_tables
        assert "kortex_licenses" not in baseline_tables
    await engine_baseline.dispose()

    # Step 2: Migration upgrade to head (applies b4e89f123c5a)
    await asyncio.to_thread(_upgrade_to_head, db_path)

    # Step 3: kortex_licenses table verification
    engine_head = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine_head.connect() as conn:
        migrated_tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "kortex_licenses" in migrated_tables
        assert "workflow_definitions" in migrated_tables

        columns = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_columns("kortex_licenses"))
        col_names = {c["name"] for c in columns}
        required_columns = {
            "id",
            "license_id",
            "tenant_id",
            "active_tenant_id",
            "scope",
            "tier",
            "status",
            "raw_token",
            "kid",
            "signature_hex",
            "issued_at",
            "not_before",
            "expires_at",
            "grace_period_days",
            "features_json",
            "quotas_json",
            "activated_at",
            "activated_by",
            "revoked_at",
            "revocation_reason",
            "highest_observed_at",
            "grace_event_emitted",
            "created_at",
            "updated_at",
        }
        assert required_columns.issubset(col_names)
    await engine_head.dispose()

    # Step 4: Engine startup on migrated schema
    db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_path}")
    data_store = RelationalDataStore(db_manager)
    repo = TenantScopedLicenseRepository(data_store)
    crypto_engine = LicenseCryptoEngine(trusted_root_keys={_OFFICIAL_ROOT_KID: _TEST_PUB_BYTES})
    license_engine = LicenseEngine(crypto_engine=crypto_engine, repository=repo)

    tenant_id = str(uuid.uuid4())
    token = _issue_test_token(crypto_engine, tenant_id)
    ctx = CapabilityExecutionContext(
        request_id="req-mig-1",
        correlation_id="corr-mig-1",
        capability_name="kortex.license.activation.apply",
        principal=SecurityPrincipal(
            principal_id="admin-mig",
            tenant_id=tenant_id,
            principal_type=PrincipalType.USER,
            roles=["ADMIN"],
        ),
        tenant_id=tenant_id,
        session_token=None,
    )

    # Step 5: Activation of license
    res = await license_engine.apply_activation(token, execution_context=ctx)
    assert res.status == "ACTIVE"
    assert res.tier == "ENTERPRISE"
    assert "migration_test_feature" in res.features

    # Step 6: Entitlement query
    snapshot = license_engine.get_entitlements(tenant_id)
    assert snapshot.status == LicenseStatusEnum.ACTIVE
    assert snapshot.tier == LicenseTier.ENTERPRISE
    assert license_engine.is_feature_enabled(tenant_id, "migration_test_feature")
    assert license_engine.get_quota(tenant_id, "max_users") == 200

    # Step 7: Downgrade behavior (back to baseline 81d6d64c51ba)
    await db_manager.disconnect()
    await asyncio.to_thread(_downgrade_to_baseline, db_path)

    engine_post = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine_post.connect() as conn:
        tables_post = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "kortex_licenses" not in tables_post
        # Baseline schema preserved
        assert "workflow_definitions" in tables_post
    await engine_post.dispose()
