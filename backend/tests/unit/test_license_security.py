"""
Security and capability dispatch tests for KORTEX License Engine (M5.7).

Verifies authentication, authorization (license:manage, license:read),
dispatcher-injected execution context, and strict tenant isolation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import ReservedParameterError
from kortex.core.kernel import Kernel
from kortex.engines.license.config import _OFFICIAL_ROOT_KID
from kortex.engines.license.crypto import LicenseCryptoEngine
from kortex.engines.license.engine import LicenseEngine
from kortex.engines.license.exceptions import TenantMismatchError
from kortex.engines.license.models import (
    LicenseScopeEnum,
    LicenseTier,
    LicenseTokenClaims,
)
from kortex.engines.security.auth import PasswordHasher
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import (
    AuthenticationError,
    AuthorizationDeniedError,
)
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_MASTER_KEY = b"kortex-test-master-key-32-bytes!"
_TEST_SIGNING_KEY = bytes.fromhex("11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff")

# Test license keys
_LICENSE_SEED = bytes.fromhex("3f34ef585ba20e9dc048c2d9f6ce9ab55515dacc578f721d78635ad38af42782")
_LICENSE_PRIV = Ed25519PrivateKey.from_private_bytes(_LICENSE_SEED)
_LICENSE_PRIV_BYTES = _LICENSE_PRIV.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
_LICENSE_PUB_BYTES = _LICENSE_PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _issue_license_token(
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
        features=["advanced_ai", "custom_connectors"],
        quotas={"max_users": 100},
    )
    return crypto_engine.encode_token(claims, _LICENSE_PRIV_BYTES, kid=_OFFICIAL_ROOT_KID)


async def _seed_principal(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("test-credential")

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=True,
                credential_hash=credential_hash,
                roles=roles or [],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await data_store.execute_in_transaction(_action)


async def _grant_role_permission(
    data_store: IDataStore,
    role: str,
    permission: str,
) -> None:
    async def _action(session: AsyncSession) -> None:
        session.add(
            RolePermissionRecord(
                id=str(uuid.uuid4()),
                role=role,
                permission=permission,
            )
        )

    await data_store.execute_in_transaction(_action)


async def _issue_session_token(
    security_engine: SecurityEngine,
    principal_id: str,
    tenant_id: str,
    roles: list[str],
) -> TokenPayload:
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.fixture
async def booted_system(tmp_path: Path) -> tuple[Kernel, SecurityEngine, LicenseEngine]:
    kernel = Kernel()
    db_file = tmp_path / "test_sec.db"
    db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_file}")
    await db_manager.create_all_tables()
    kernel._db_manager = db_manager

    storage_engine = StorageEngine(base_directory=str(tmp_path / "sec_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)

    crypto_engine = LicenseCryptoEngine(trusted_root_keys={_OFFICIAL_ROOT_KID: _LICENSE_PUB_BYTES})
    license_engine = LicenseEngine(crypto_engine=crypto_engine)

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(license_engine)
    await kernel.boot()

    return kernel, security_engine, license_engine


@pytest.mark.asyncio
async def test_unauthenticated_activation_fails_closed(
    booted_system: tuple[Kernel, SecurityEngine, LicenseEngine],
) -> None:
    kernel, _, _ = booted_system
    req = CapabilityRequest(
        capability_name="kortex.license.activation.apply",
        session_token=None,
        parameters={"token": "some.token.here"},
    )
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(req)


@pytest.mark.asyncio
async def test_activation_denied_without_license_manage(
    booted_system: tuple[Kernel, SecurityEngine, LicenseEngine],
) -> None:
    kernel, sec_engine, _ = booted_system
    tenant_id = str(uuid.uuid4())
    data_store = kernel.get_engine("storage").data  # type: ignore[union-attr]

    await _seed_principal(data_store, tenant_id, "user-viewer", roles=["VIEWER"])
    # Do not grant license:manage to VIEWER
    session_token = await _issue_session_token(sec_engine, "user-viewer", tenant_id, roles=["VIEWER"])

    req = CapabilityRequest(
        capability_name="kortex.license.activation.apply",
        session_token=session_token,
        parameters={"token": "some.dummy.token"},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(req)


@pytest.mark.asyncio
async def test_reserved_parameter_rejection(booted_system: tuple[Kernel, SecurityEngine, LicenseEngine]) -> None:
    kernel, sec_engine, _ = booted_system
    tenant_id = str(uuid.uuid4())
    data_store = kernel.get_engine("storage").data  # type: ignore[union-attr]

    await _seed_principal(data_store, tenant_id, "admin-1", roles=["ADMIN"])
    await _grant_role_permission(data_store, "ADMIN", "license:manage")
    session_token = await _issue_session_token(sec_engine, "admin-1", tenant_id, roles=["ADMIN"])

    # Caller attempts to inject execution_context directly
    req1 = CapabilityRequest(
        capability_name="kortex.license.activation.apply",
        session_token=session_token,
        context={"resource_tenant_id": tenant_id},
        parameters={"token": "some.token", "execution_context": "forged_ctx"},
    )
    with pytest.raises(ReservedParameterError, match="reserved key"):
        await kernel.invoke_capability(req1)

    # Caller attempts to inject principal directly
    req2 = CapabilityRequest(
        capability_name="kortex.license.activation.apply",
        session_token=session_token,
        context={"resource_tenant_id": tenant_id},
        parameters={"token": "some.token", "principal": "forged_principal"},
    )
    with pytest.raises(ReservedParameterError, match="reserved key"):
        await kernel.invoke_capability(req2)


@pytest.mark.asyncio
async def test_cross_tenant_activation_rejected(booted_system: tuple[Kernel, SecurityEngine, LicenseEngine]) -> None:
    kernel, sec_engine, lic_engine = booted_system
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    data_store = kernel.get_engine("storage").data  # type: ignore[union-attr]

    await _seed_principal(data_store, tenant_a, "admin-a", roles=["ADMIN"])
    await _grant_role_permission(data_store, "ADMIN", "license:manage")
    session_token = await _issue_session_token(sec_engine, "admin-a", tenant_a, roles=["ADMIN"])

    # Token was issued for tenant_b
    token_for_b = _issue_license_token(lic_engine.crypto_engine, tenant_b)

    # Tenant A admin attempts to activate Tenant B's token within their authorized tenant context
    req = CapabilityRequest(
        capability_name="kortex.license.activation.apply",
        session_token=session_token,
        context={"resource_tenant_id": tenant_a},
        parameters={"token": token_for_b},
    )
    with pytest.raises(TenantMismatchError, match="does not match caller tenant"):
        await kernel.invoke_capability(req)


@pytest.mark.asyncio
async def test_authorized_activation_and_status_flow(
    booted_system: tuple[Kernel, SecurityEngine, LicenseEngine],
) -> None:
    kernel, sec_engine, lic_engine = booted_system
    tenant_id = str(uuid.uuid4())
    data_store = kernel.get_engine("storage").data  # type: ignore[union-attr]

    await _seed_principal(data_store, tenant_id, "admin-user", roles=["ADMIN"])
    await _grant_role_permission(data_store, "ADMIN", "license:manage")
    await _grant_role_permission(data_store, "ADMIN", "license:read")
    session_token = await _issue_session_token(sec_engine, "admin-user", tenant_id, roles=["ADMIN"])

    token = _issue_license_token(lic_engine.crypto_engine, tenant_id)

    # 1. Activate
    act_req = CapabilityRequest(
        capability_name="kortex.license.activation.apply",
        session_token=session_token,
        context={"resource_tenant_id": tenant_id},
        parameters={"token": token},
    )
    act_res = await kernel.invoke_capability(act_req)
    assert act_res.status == "ACTIVE"
    assert act_res.tier == "ENTERPRISE"
    assert "advanced_ai" in act_res.features

    # 2. Status inspection
    status_req = CapabilityRequest(
        capability_name="kortex.license.status.get",
        session_token=session_token,
        context={"resource_tenant_id": tenant_id},
    )
    status_res = await kernel.invoke_capability(status_req)
    assert status_res.status == "ACTIVE"
    assert status_res.tier == "ENTERPRISE"

    # 3. Revoke
    rev_req = CapabilityRequest(
        capability_name="kortex.license.activation.revoke",
        session_token=session_token,
        context={"resource_tenant_id": tenant_id},
        parameters={"reason": "Testing revocation via dispatcher"},
    )
    rev_res = await kernel.invoke_capability(rev_req)
    assert rev_res.status == "UNLICENSED"
    assert rev_res.tier == "COMMUNITY"
