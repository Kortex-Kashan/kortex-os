"""M6.3-1 regression suite: Connector Engine trust-boundary fix.

Prior to this fix, `ConnectorEngine.execute_action` never received the
Kernel's verified principal, and `ConnectorProfileManager.get_profile` had no
tenant parameter at all — profile lookup was entirely global. A caller
holding the coarse, non-tenant-scoped `connector:execute` RBAC permission
could reach any tenant's connector profile and secret by supplying that
tenant's `profile_id`.

Every test here drives the real Kernel capability-dispatch boundary — real
`SecurityEngine` authentication, real RBAC, real `kernel.invoke_capability`
— not a raw-manager shortcut, mirroring the established M6.0-3/M6.1-1/M6.2
adversarial-test methodology used throughout this codebase.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import ConnectorProfileNotFoundError
from kortex.engines.connector.models import ActionRequest, ConnectorActionType, ConnectorProfile
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x11" * 32
_TEST_SIGNING_KEY = b"\x22" * 32
_ROLE = "CONNECTOR_TENANT_ISO_TEST_ROLE"
_TENANT_A = "tenant_a_conn_iso"
_TENANT_B = "tenant_b_conn_iso"


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, ConnectorEngine]]:
    db_path = (tmp_path / f"kortex_conn_iso_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_conn_iso_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    connector_engine = ConnectorEngine(data_store=data_store)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:read"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_A,
                principal_id="user_conn_iso_a",
                principal_type="USER",
                credential_hash=hasher.hash("pass-a"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_B,
                principal_id="user_conn_iso_b",
                principal_type="USER",
                credential_hash=hasher.hash("pass-b"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    connector_engine.register_driver(DummyConnectorDriver())

    # Each tenant owns a profile of the same profile_id namespace but a
    # distinct real one, each pointing at a distinct, tenant-scoped secret.
    await security_engine.put_secret("vault:conn-iso-secret", _TENANT_A, "tenant-a-real-secret")
    await security_engine.put_secret("vault:conn-iso-secret", _TENANT_B, "tenant-b-real-secret")
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="prof-tenant-a",
            tenant_id=_TENANT_A,
            name="Tenant A Profile",
            driver_id="connector-dummy",
            secret_handle="vault:conn-iso-secret",
        )
    )
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="prof-tenant-b",
            tenant_id=_TENANT_B,
            name="Tenant B Profile",
            driver_id="connector-dummy",
            secret_handle="vault:conn-iso-secret",
        )
    )

    try:
        yield kernel, connector_engine
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _token(kernel: Kernel, tenant_id: str, principal_id: str, password: str):  # noqa: ANN001
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": password}
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.asyncio
async def test_case_a_tenant_a_can_execute_its_own_connector(kernel_env: tuple[Kernel, ConnectorEngine]) -> None:
    kernel, _connector = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_conn_iso_a", "pass-a")

    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.action.execute",
            session_token=token_a,
            parameters={
                "request": ActionRequest(
                    request_id=f"req-{uuid4()}",
                    profile_id="prof-tenant-a",
                    action_type=ConnectorActionType.FETCH,
                )
            },
            context={"resource_tenant_id": _TENANT_A},
        )
    )
    assert result.status == "SUCCESS"


@pytest.mark.asyncio
async def test_case_b_tenant_b_can_execute_its_own_connector(kernel_env: tuple[Kernel, ConnectorEngine]) -> None:
    kernel, _connector = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_conn_iso_b", "pass-b")

    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.action.execute",
            session_token=token_b,
            parameters={
                "request": ActionRequest(
                    request_id=f"req-{uuid4()}",
                    profile_id="prof-tenant-b",
                    action_type=ConnectorActionType.FETCH,
                )
            },
            context={"resource_tenant_id": _TENANT_B},
        )
    )
    assert result.status == "SUCCESS"


@pytest.mark.asyncio
async def test_case_c_tenant_b_cannot_execute_tenant_a_connector(kernel_env: tuple[Kernel, ConnectorEngine]) -> None:
    """Tenant B, authenticated as itself, targets tenant A's profile_id directly."""
    kernel, _connector = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_conn_iso_b", "pass-b")

    with pytest.raises(ConnectorProfileNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.connector.action.execute",
                session_token=token_b,
                parameters={
                    "request": ActionRequest(
                        request_id=f"req-{uuid4()}",
                        profile_id="prof-tenant-a",
                        action_type=ConnectorActionType.FETCH,
                    )
                },
                context={"resource_tenant_id": _TENANT_B},
            )
        )


@pytest.mark.asyncio
async def test_case_d_tenant_b_cannot_retrieve_tenant_a_profile_or_secret(
    kernel_env: tuple[Kernel, ConnectorEngine],
) -> None:
    """Even the read-only `kortex.connector.profile.get` capability must deny
    cross-tenant access -- proving the secret (reachable only via the
    profile's own `secret_handle`) is unreachable too."""
    kernel, _connector = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_conn_iso_b", "pass-b")

    with pytest.raises(ConnectorProfileNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.connector.profile.get",
                session_token=token_b,
                parameters={"profile_id": "prof-tenant-a"},
                context={"resource_tenant_id": _TENANT_B},
            )
        )


@pytest.mark.asyncio
async def test_case_e_caller_supplied_tenant_id_cannot_override_principal_tenant(
    kernel_env: tuple[Kernel, ConnectorEngine],
) -> None:
    """Tenant B authenticates as itself but forges `ActionRequest.tenant_id`
    to claim tenant A -- the verified principal must still win."""
    kernel, connector_engine = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_conn_iso_b", "pass-b")

    with pytest.raises(ConnectorProfileNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.connector.action.execute",
                session_token=token_b,
                parameters={
                    "request": ActionRequest(
                        request_id=f"req-{uuid4()}",
                        profile_id="prof-tenant-a",
                        action_type=ConnectorActionType.FETCH,
                        tenant_id=_TENANT_A,  # forged: caller is actually tenant B
                    )
                },
                context={"resource_tenant_id": _TENANT_B},
            )
        )

    # And even a request forging its OWN tenant (redundant but legitimate)
    # must still resolve under the real, principal-derived tenant, not the
    # forged claim -- proven by confirming tenant B's own profile succeeds
    # regardless of what the request's own tenant_id field says.
    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.action.execute",
            session_token=token_b,
            parameters={
                "request": ActionRequest(
                    request_id=f"req-{uuid4()}",
                    profile_id="prof-tenant-b",
                    action_type=ConnectorActionType.FETCH,
                    tenant_id=_TENANT_A,  # forged, must be ignored
                )
            },
            context={"resource_tenant_id": _TENANT_B},
        )
    )
    assert result.status == "SUCCESS"


@pytest.mark.asyncio
async def test_case_f_legitimate_same_tenant_behavior_unaffected(kernel_env: tuple[Kernel, ConnectorEngine]) -> None:
    """Regression guard: a principal executing under its own, correctly
    stated tenant is entirely unaffected by the fix -- proves the secret
    genuinely resolves and the driver genuinely executes."""
    kernel, _connector = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_conn_iso_a", "pass-a")

    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.action.execute",
            session_token=token_a,
            parameters={
                "request": ActionRequest(
                    request_id=f"req-{uuid4()}",
                    profile_id="prof-tenant-a",
                    action_type=ConnectorActionType.FETCH,
                )
            },
            context={"resource_tenant_id": _TENANT_A},
        )
    )
    assert result.status == "SUCCESS"
    assert result.response_payload.get("secret_authenticated") is True

    profile = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.profile.get",
            session_token=token_a,
            parameters={"profile_id": "prof-tenant-a"},
            context={"resource_tenant_id": _TENANT_A},
        )
    )
    assert profile.profile_id == "prof-tenant-a"
    assert profile.tenant_id == _TENANT_A


@pytest.mark.asyncio
async def test_authorization_denied_for_principal_without_connector_permission(
    kernel_env: tuple[Kernel, ConnectorEngine],
) -> None:
    """Regression guard: Kernel RBAC still gates this capability independently
    of the tenant fix -- a principal without `connector:execute` is denied
    before ever reaching the handler."""
    kernel, _connector = kernel_env
    security_engine: SecurityEngine = kernel.get_engine("security")
    storage_engine: StorageEngine = kernel.get_engine("storage")
    hasher = PasswordHasher()

    async def _seed_no_perm(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_A,
                principal_id="user_conn_iso_noperm",
                principal_type="USER",
                credential_hash=hasher.hash("pass-noperm"),
                roles=[],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await storage_engine.data.execute_in_transaction(_seed_no_perm)
    token = await _token(kernel, _TENANT_A, "user_conn_iso_noperm", "pass-noperm")

    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.connector.action.execute",
                session_token=token,
                parameters={
                    "request": ActionRequest(
                        request_id=f"req-{uuid4()}",
                        profile_id="prof-tenant-a",
                        action_type=ConnectorActionType.FETCH,
                    )
                },
                context={"resource_tenant_id": _TENANT_A},
            )
        )
