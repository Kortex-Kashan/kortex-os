"""M5 regression coverage: `kortex.connector.driver.list` through the real
Kernel Capability Enforcement Boundary (`kortex.core.dispatch`).

Mirrors `test_capability_dispatch.py`'s established bootstrap/seeding
pattern (real, unmodified Storage + Security Engines; no mocks on the
security decision path) but drives it against the real, production
`kortex.connector.driver.list` capability registered by `ConnectorEngine`
rather than a synthetic test capability — proving the three states M5
requires end to end:

    no token                          -> AuthenticationError  (401)
    valid token, missing permission   -> AuthorizationDeniedError (403)
    valid token, "connector:read"     -> [] on an empty registry (200)
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
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32
_TEST_ROLE = "connector-dispatch-test-role"
_CAPABILITY = "kortex.connector.driver.list"


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-connector-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, ConnectorEngine]:
    kernel = Kernel()
    # M5-A8: explicit isolated in-memory DB — this test's exact-count registry
    # assertions must never observe a driver/definition another test (or a
    # real local run sharing the machine-wide default) left behind.
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "connector_dispatch_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    connector_engine = ConnectorEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)
    return kernel, storage_engine, security_engine, connector_engine


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, ConnectorEngine]:
    kernel, storage_engine, security_engine, connector_engine = _build_kernel(tmp_path)
    await kernel.boot()
    return kernel, storage_engine, security_engine, connector_engine


async def _seed_principal(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("connector-dispatch-test-credential")

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
            "password": "connector-dispatch-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.asyncio
async def test_no_token_is_denied_authentication(tmp_path: Path) -> None:
    kernel, _storage, _security, _connector = await _boot_kernel(tmp_path)

    request = CapabilityRequest(capability_name=_CAPABILITY, session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_without_connector_read_permission_is_denied_authorization(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _connector = await _boot_kernel(tmp_path)

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
async def test_authenticated_with_connector_read_permission_lists_empty_registry(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _connector = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "connector:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert result == []


@pytest.mark.asyncio
async def test_authenticated_with_connector_read_permission_lists_registered_driver(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, connector_engine = await _boot_kernel(tmp_path)
    connector_engine.register_driver(DummyConnectorDriver())

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "connector:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert len(result) == 1
    assert result[0].driver_id == "connector-dummy"
    assert result[0].display_name == "Reference Dummy Connector Driver"
