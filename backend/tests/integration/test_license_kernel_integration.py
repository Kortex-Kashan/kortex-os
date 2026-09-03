"""
Kernel integration tests for KORTEX License Engine (M5.7).

Verifies full bootstrap registration, ILicenseProvider retrieval from IoC,
authenticated capability dispatch through Kernel.invoke_capability, and
lifecycle state transitions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.base_engine import EngineState
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.license.config import _OFFICIAL_ROOT_KID
from kortex.engines.license.crypto import LicenseCryptoEngine
from kortex.engines.license.engine import LicenseEngine
from kortex.engines.license.interfaces import ILicenseProvider
from kortex.engines.license.models import (
    LicenseScopeEnum,
    LicenseTier,
    LicenseTokenClaims,
)
from kortex.engines.security.auth import PasswordHasher
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_MASTER_KEY = b"\x12" * 32
_SIGNING_KEY = bytes.fromhex("11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff")

# Deterministic license signing key
_LICENSE_SEED = bytes.fromhex("3f34ef585ba20e9dc048c2d9f6ce9ab55515dacc578f721d78635ad38af42782")
_LICENSE_PRIV = Ed25519PrivateKey.from_private_bytes(_LICENSE_SEED)
_LICENSE_PRIV_BYTES = _LICENSE_PRIV.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
_LICENSE_PUB_BYTES = _LICENSE_PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _generate_license_token(
    crypto_engine: LicenseCryptoEngine,
    tenant_id: str,
    tier: LicenseTier = LicenseTier.ENTERPRISE,
) -> str:
    now = datetime.now(UTC)
    claims = LicenseTokenClaims(
        schema_version=1,
        license_id=str(uuid.uuid4()),
        issuer="kortex.ai",
        subject_tenant_id=tenant_id,
        scope=LicenseScopeEnum.TENANT,
        tier=tier,
        issued_at=now - timedelta(days=1),
        not_before=now - timedelta(days=1),
        expires_at=now + timedelta(days=365),
        grace_period_days=14,
        features=["enterprise_workflow", "dedicated_storage", "process_mining_full"],
        quotas={"max_users": 250, "max_connectors": 50},
    )
    return crypto_engine.encode_token(claims, _LICENSE_PRIV_BYTES, kid=_OFFICIAL_ROOT_KID)


async def _seed_user_and_permissions(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    role: str = "TENANT_ADMIN",
) -> None:
    credential_hash = PasswordHasher().hash("integration-secret-password")

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=True,
                credential_hash=credential_hash,
                roles=[role],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        session.add(
            RolePermissionRecord(
                id=str(uuid.uuid4()),
                role=role,
                permission="license:manage",
            )
        )
        session.add(
            RolePermissionRecord(
                id=str(uuid.uuid4()),
                role=role,
                permission="license:read",
            )
        )

    await data_store.execute_in_transaction(_action)


@pytest.mark.asyncio
async def test_license_engine_kernel_end_to_end_flow(tmp_path: Path) -> None:
    kernel = Kernel()
    db_file = tmp_path / "license_integration.db"
    db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_file}")
    await db_manager.create_all_tables()
    kernel._db_manager = db_manager

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    security_engine = SecurityEngine(master_key=_MASTER_KEY, signing_private_key=_SIGNING_KEY)

    crypto_engine = LicenseCryptoEngine(trusted_root_keys={_OFFICIAL_ROOT_KID: _LICENSE_PUB_BYTES})
    license_engine = LicenseEngine(crypto_engine=crypto_engine)

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(license_engine)
    await kernel.boot()

    # 1. Verify engine registration and running state
    resolved_engine = kernel.get_engine("license")
    assert resolved_engine is not None
    assert resolved_engine.state == EngineState.RUNNING

    # 2. Check ILicenseProvider before activation (unlicensed tenant)
    tenant_id = str(uuid.uuid4())
    provider: ILicenseProvider = resolved_engine  # type: ignore[assignment]
    unlicensed_snap = provider.get_entitlements(tenant_id)
    assert unlicensed_snap.tier == LicenseTier.COMMUNITY
    assert not provider.is_feature_enabled(tenant_id, "enterprise_workflow")

    # 3. Seed user and authenticate session token
    await _seed_user_and_permissions(storage_engine.data, tenant_id, "admin-rob")  # type: ignore[union-attr]
    auth_res = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": "admin-rob",
            "password": "integration-secret-password",
        }
    )
    session_token = await security_engine.authentication_manager.issue_token(auth_res)

    # 4. Activate token via CapabilityDispatcher
    token = _generate_license_token(crypto_engine, tenant_id, tier=LicenseTier.ENTERPRISE)
    activate_req = CapabilityRequest(
        capability_name="kortex.license.activation.apply",
        session_token=session_token,
        context={"resource_tenant_id": tenant_id},
        parameters={"token": token},
    )
    act_res = await kernel.invoke_capability(activate_req)
    assert act_res.status == "ACTIVE"
    assert act_res.tier == "ENTERPRISE"
    assert "enterprise_workflow" in act_res.features

    # 5. Verify ILicenseProvider immediate reflection (fast path)
    active_snap = provider.get_entitlements(tenant_id)
    assert active_snap.tier == LicenseTier.ENTERPRISE
    assert active_snap.status.value == "ACTIVE"
    assert provider.is_feature_enabled(tenant_id, "enterprise_workflow")
    assert provider.get_quota(tenant_id, "max_users") == 250

    # 6. Verify status capability
    status_req = CapabilityRequest(
        capability_name="kortex.license.status.get",
        session_token=session_token,
        context={"resource_tenant_id": tenant_id},
    )
    status_res = await kernel.invoke_capability(status_req)
    assert status_res.status == "ACTIVE"
    assert status_res.tier == "ENTERPRISE"

    # 7. Revoke license
    revoke_req = CapabilityRequest(
        capability_name="kortex.license.activation.revoke",
        session_token=session_token,
        context={"resource_tenant_id": tenant_id},
        parameters={"reason": "Revoking license in integration test"},
    )
    rev_res = await kernel.invoke_capability(revoke_req)
    assert rev_res.status == "UNLICENSED"
    assert rev_res.tier == "COMMUNITY"

    # 8. Verify entitlements dropped to Community
    post_revoke_snap = provider.get_entitlements(tenant_id)
    assert post_revoke_snap.tier == LicenseTier.COMMUNITY
    assert not provider.is_feature_enabled(tenant_id, "enterprise_workflow")
