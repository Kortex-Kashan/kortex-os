"""M7.3-W2 regression coverage: `kortex.connector.profile.register/.list/.delete`
through the real Kernel Capability Enforcement Boundary (`kortex.core.dispatch`).

Mirrors `test_connector_capability_dispatch.py`'s established bootstrap/
seeding pattern (real, unmodified Storage + Security Engines; no mocks on
the security decision path) but exercises the three new M7.3 connector
profile lifecycle capabilities, proving:

    no token                           -> AuthenticationError  (401)
    valid token, missing permission    -> AuthorizationDeniedError (403)
    valid token, "connector:write"     -> register/delete succeed
    valid token, "connector:read"      -> list succeeds, tenant-scoped

    a caller-supplied tenant_id on the submitted profile is never trusted:
    the principal's own tenant_id is always authoritative (M6.3-1 pattern)

    tenant B cannot list or delete tenant A's profile
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
from kortex.engines.connector.exceptions import ConnectorProfileNotFoundError, DriverNotFoundError
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32
_WRITE_ROLE = "connector-profile-write-role"
_READ_ROLE = "connector-profile-read-role"


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-connector-profile-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, ConnectorEngine]:
    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "connector_profile_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    connector_engine = ConnectorEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)
    return kernel, storage_engine, security_engine, connector_engine


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, ConnectorEngine]:
    kernel, storage_engine, security_engine, connector_engine = _build_kernel(tmp_path)
    await kernel.boot()
    # `register_profile` (M7.3) rejects a profile referencing an
    # unregistered driver_id -- every `_sample_profile` in this file uses
    # "connector-dummy", so it must actually be registered for the
    # register-focused tests to exercise anything past that check.
    connector_engine.register_driver(DummyConnectorDriver())
    return kernel, storage_engine, security_engine, connector_engine


async def _seed_principal(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("connector-profile-test-credential")

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
            "password": "connector-profile-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _authorized_token(
    tmp_path: Path,
    storage_engine: StorageEngine,
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str,
    permission: str,
) -> TokenPayload:
    role = f"role-{uuid.uuid4().hex[:8]}"
    await _seed_principal(storage_engine.data, tenant_id, principal_id, roles=[role])
    await _grant_role_permission(storage_engine.data, role, permission)
    return await _issue_token(security_engine, tenant_id, principal_id)


def _sample_profile(profile_id: str, tenant_id: str = "attacker-supplied-tenant") -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "tenant_id": tenant_id,
        "name": "Test Profile",
        "driver_id": "connector-dummy",
        "rate_limit_per_sec": 5.0,
        "max_retries": 2,
    }


# -- Authentication / authorization gates -----------------------------------


@pytest.mark.asyncio
async def test_register_profile_no_token_is_denied_authentication(tmp_path: Path) -> None:
    kernel, _storage, _security, _connector = await _boot_kernel(tmp_path)

    request = CapabilityRequest(
        capability_name="kortex.connector.profile.register",
        session_token=None,
        parameters={"profile": _sample_profile("p1")},
    )
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_register_profile_without_connector_write_permission_is_denied(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _connector = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.connector.profile.register",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
        parameters={"profile": _sample_profile("p1")},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_list_profiles_without_connector_read_permission_is_denied(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _connector = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.connector.profile.list",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_delete_profile_without_connector_write_permission_is_denied(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _connector = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.connector.profile.delete",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
        parameters={"profile_id": "p1"},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


# -- Correct behavior + tenant-spoofing resistance ---------------------------


@pytest.mark.asyncio
async def test_register_profile_ignores_caller_supplied_tenant_id(tmp_path: Path) -> None:
    """A caller-supplied `tenant_id` inside the submitted profile must never
    be trusted -- the verified principal's own tenant_id always wins,
    identical to `execute_action`'s and `get_profile`'s M6.3-1 pattern."""
    kernel, storage_engine, security_engine, connector_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    token = await _authorized_token(
        tmp_path, storage_engine, security_engine, tenant_id, "principal-1", "connector:write"
    )

    request = CapabilityRequest(
        capability_name="kortex.connector.profile.register",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
        parameters={"profile": _sample_profile("p1", tenant_id="someone-elses-tenant")},
    )
    result = await kernel.invoke_capability(request)

    assert result.tenant_id == tenant_id
    stored = await connector_engine.get_profile("p1", tenant_id=tenant_id)
    assert stored.tenant_id == tenant_id


@pytest.mark.asyncio
async def test_list_profiles_returns_only_the_callers_tenant(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, connector_engine = await _boot_kernel(tmp_path)
    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")

    write_token_a = await _authorized_token(
        tmp_path, storage_engine, security_engine, tenant_a, "principal-a", "connector:write"
    )
    await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.profile.register",
            session_token=write_token_a,
            context={"resource_tenant_id": tenant_a},
            parameters={"profile": _sample_profile("p-a")},
        )
    )

    write_token_b = await _authorized_token(
        tmp_path, storage_engine, security_engine, tenant_b, "principal-b", "connector:write"
    )
    await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.profile.register",
            session_token=write_token_b,
            context={"resource_tenant_id": tenant_b},
            parameters={"profile": _sample_profile("p-b")},
        )
    )

    read_token_a = await _authorized_token(
        tmp_path, storage_engine, security_engine, tenant_a, "reader-a", "connector:read"
    )
    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.profile.list",
            session_token=read_token_a,
            context={"resource_tenant_id": tenant_a},
        )
    )

    assert [p.profile_id for p in result] == ["p-a"]


@pytest.mark.asyncio
async def test_delete_profile_removes_own_tenants_profile(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, connector_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    token = await _authorized_token(
        tmp_path, storage_engine, security_engine, tenant_id, "principal-1", "connector:write"
    )
    await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.profile.register",
            session_token=token,
            context={"resource_tenant_id": tenant_id},
            parameters={"profile": _sample_profile("p1")},
        )
    )

    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.profile.delete",
            session_token=token,
            context={"resource_tenant_id": tenant_id},
            parameters={"profile_id": "p1"},
        )
    )
    assert result is True

    with pytest.raises(ConnectorProfileNotFoundError):
        await connector_engine.get_profile("p1", tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_delete_profile_cross_tenant_attempt_fails_closed(tmp_path: Path) -> None:
    """Tenant B holding valid `connector:write` cannot delete tenant A's
    profile by guessing its profile_id -- masked identically to a
    nonexistent profile (T2 in the M7.3 threat model), and the profile must
    still exist for tenant A afterward."""
    kernel, storage_engine, security_engine, connector_engine = await _boot_kernel(tmp_path)
    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")

    write_token_a = await _authorized_token(
        tmp_path, storage_engine, security_engine, tenant_a, "principal-a", "connector:write"
    )
    await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.profile.register",
            session_token=write_token_a,
            context={"resource_tenant_id": tenant_a},
            parameters={"profile": _sample_profile("shared-guessable-id")},
        )
    )

    write_token_b = await _authorized_token(
        tmp_path, storage_engine, security_engine, tenant_b, "principal-b", "connector:write"
    )
    with pytest.raises(ConnectorProfileNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.connector.profile.delete",
                session_token=write_token_b,
                context={"resource_tenant_id": tenant_b},
                parameters={"profile_id": "shared-guessable-id"},
            )
        )

    # Tenant A's profile must be untouched.
    still_there = await connector_engine.get_profile("shared-guessable-id", tenant_id=tenant_a)
    assert still_there.profile_id == "shared-guessable-id"


@pytest.mark.asyncio
async def test_register_profile_rejects_an_unregistered_driver_id(tmp_path: Path) -> None:
    """A profile referencing a driver_id that isn't currently registered
    must be rejected at registration time, not silently accepted and left
    to fail later at execution time inside `ConnectorPipeline`."""
    kernel, storage_engine, security_engine, _connector = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path)
    token = await _authorized_token(
        tmp_path, storage_engine, security_engine, tenant_id, "principal-1", "connector:write"
    )

    profile = _sample_profile("p1")
    profile["driver_id"] = "driver-that-does-not-exist"

    with pytest.raises(DriverNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.connector.profile.register",
                session_token=token,
                context={"resource_tenant_id": tenant_id},
                parameters={"profile": profile},
            )
        )
