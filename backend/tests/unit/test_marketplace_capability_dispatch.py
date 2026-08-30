"""M7 regression coverage: `kortex.marketplace.listing.list` through the real
Kernel Capability Enforcement Boundary (`kortex.core.dispatch`).

Mirrors `test_connector_capability_dispatch.py` / `test_workflow_capability_dispatch.py`'s
established bootstrap/seeding pattern (real, unmodified Storage + Security
Engines; no mocks on the security decision path) but drives it against the
real, production `kortex.marketplace.listing.list` capability registered
by `MarketplaceEngine` — proving the three states M7 requires end to end:

    no token                          -> AuthenticationError  (401)
    valid token, missing permission   -> AuthorizationDeniedError (403)
    valid token, "marketplace:read"   -> [] on an empty catalog (200)
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.marketplace.engine import MarketplaceEngine
from kortex.engines.marketplace.models import MarketplaceItemType, MarketplaceListing
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_MASTER_KEY = b"\x77" * 32
_TEST_SIGNING_KEY = b"\x88" * 32
_TEST_ROLE = "marketplace-dispatch-test-role"
_CAPABILITY = "kortex.marketplace.listing.list"


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-marketplace-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, MarketplaceEngine]:
    kernel = Kernel()
    # M5-A8: explicit isolated in-memory DB — this test's exact-count catalog
    # assertions must never observe a listing another test (or a real local
    # run sharing the machine-wide default) left behind.
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "marketplace_dispatch_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    marketplace_engine = MarketplaceEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(marketplace_engine)
    return kernel, storage_engine, security_engine, marketplace_engine


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, MarketplaceEngine]:
    kernel, storage_engine, security_engine, marketplace_engine = _build_kernel(tmp_path)
    await kernel.boot()
    return kernel, storage_engine, security_engine, marketplace_engine


async def _seed_principal(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("marketplace-dispatch-test-credential")

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
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await data_store.execute_in_transaction(_action)


async def _grant_role_permission(data_store: IDataStore, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        existing = await session.scalar(
            select(RolePermissionRecord).where(
                RolePermissionRecord.role == role,
                RolePermissionRecord.permission == permission,
            )
        )
        if existing is None:
            session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))

    await data_store.execute_in_transaction(_action)


async def _issue_token(security_engine: SecurityEngine, tenant_id: str, principal_id: str) -> TokenPayload:
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "marketplace-dispatch-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.asyncio
async def test_no_token_is_denied_authentication(tmp_path: Path) -> None:
    kernel, _storage, _security, _marketplace = await _boot_kernel(tmp_path)

    request = CapabilityRequest(capability_name=_CAPABILITY, session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_without_marketplace_read_permission_is_denied_authorization(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _marketplace = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    # Principal has no roles at all -> RBAC denies for lack of any granted permission.
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_with_marketplace_read_permission_lists_empty_catalog(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _marketplace = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "marketplace:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert result == []


@pytest.mark.asyncio
async def test_authenticated_with_marketplace_read_permission_lists_registered_listing(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, marketplace_engine = await _boot_kernel(tmp_path)
    marketplace_engine.registry.register_listing(
        MarketplaceListing(
            listing_id="listing-demo",
            name="Sample Recipe Pack",
            description="A sample catalog entry.",
            version="1.0.0",
            item_type=MarketplaceItemType.RECIPE,
            publisher="KORTEX",
        )
    )

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "marketplace:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert len(result) == 1
    assert result[0].listing_id == "listing-demo"
    assert result[0].name == "Sample Recipe Pack"
