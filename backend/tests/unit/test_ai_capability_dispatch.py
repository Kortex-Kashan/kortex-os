"""Slice 4.6 regression coverage: `kortex.ai.provider.list` and
`kortex.ai.model.list` through the real Kernel Capability Enforcement
Boundary (`kortex.core.dispatch`).

Mirrors `test_connector_capability_dispatch.py` / `test_workflow_capability_dispatch.py`
/ `test_marketplace_capability_dispatch.py`'s established bootstrap/seeding
pattern (real, unmodified Storage + Security Engines; no mocks on the
security decision path) but drives it against the real, production
`AIOrchestrationEngine`, wired the same production way `kernel_bootstrap.py`
wires it (`KernelBridgeAdapter` + `RelationalDataStore`, not a bare
`AIOrchestrationEngine()` — the engine's own certified integration test
requires either both `kernel_bridge`/`data_store` real and non-None, or
`environment="development"`; this module uses the same production
constructor `kernel_bootstrap.py` uses in production) — proving the three
states Slice 4.6 requires end to end:

    no token                          -> AuthenticationError  (401)
    valid token, missing permission   -> AuthorizationDeniedError (403)
    valid token, "ai:read"            -> [] on an empty registry (200)
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x99" * 32
_TEST_SIGNING_KEY = b"\xaa" * 32
_TEST_ROLE = "ai-dispatch-test-role"


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-ai-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "ai_dispatch_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    ai_bootstrap = KernelProductionBootstrap(AIEngineRuntimeConfig(environment="production"))
    ai_engine = ai_bootstrap.create_ai_engine(
        kernel_bridge=KernelBridgeAdapter(kernel),  # type: ignore[arg-type]
        data_store=RelationalDataStore(kernel.db),
        registered_engines=list(kernel.get_all_engines().keys()),
    )
    kernel.register_engine(ai_engine)
    return kernel, storage_engine, security_engine


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel, storage_engine, security_engine = _build_kernel(tmp_path)
    await kernel.boot()
    return kernel, storage_engine, security_engine


async def _seed_principal(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("ai-dispatch-test-credential")

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
            "password": "ai-dispatch-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.parametrize("capability_name", ["kortex.ai.provider.list", "kortex.ai.model.list"])
@pytest.mark.asyncio
async def test_no_token_is_denied_authentication(tmp_path: Path, capability_name: str) -> None:
    kernel, _storage, _security = await _boot_kernel(tmp_path)

    request = CapabilityRequest(capability_name=capability_name, session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.parametrize("capability_name", ["kortex.ai.provider.list", "kortex.ai.model.list"])
@pytest.mark.asyncio
async def test_authenticated_without_ai_read_permission_is_denied_authorization(
    tmp_path: Path, capability_name: str
) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    # Principal has no roles at all -> RBAC denies for lack of any granted permission.
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=capability_name,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.parametrize("capability_name", ["kortex.ai.provider.list", "kortex.ai.model.list"])
@pytest.mark.asyncio
async def test_authenticated_with_ai_read_permission_lists_empty_registry(
    tmp_path: Path, capability_name: str
) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "ai:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=capability_name,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert result == []
