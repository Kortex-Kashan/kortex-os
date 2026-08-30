"""M6 regression coverage: `kortex.workflow.definition.list` through the real
Kernel Capability Enforcement Boundary (`kortex.core.dispatch`).

Mirrors `test_connector_capability_dispatch.py`'s established bootstrap/
seeding pattern (real, unmodified Storage + Security Engines; no mocks on
the security decision path) but drives it against the real, production
`kortex.workflow.definition.list` capability registered by `WorkflowEngine`
— proving the three states M6 requires end to end:

    no token                          -> AuthenticationError  (401)
    valid token, missing permission   -> AuthorizationDeniedError (403)
    valid token, "workflow:read"      -> [] on an empty registry (200)
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
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import WorkflowDefinition, WorkflowStep

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32
_TEST_ROLE = "workflow-dispatch-test-role"
_CAPABILITY = "kortex.workflow.definition.list"


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-workflow-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, WorkflowEngine]:
    kernel = Kernel()
    # M5-A8: explicit isolated in-memory DB per test, matching the pattern
    # used throughout the rest of the suite (e.g. test_workflow_approval_durable.py's
    # `durable_env` fixture) rather than relying solely on the default's
    # own pytest auto-isolation — a Kernel() built here must never be able
    # to see a workflow definition another test (or a real local run) saved.
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "workflow_dispatch_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(workflow_engine)
    return kernel, storage_engine, security_engine, workflow_engine


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, WorkflowEngine]:
    kernel, storage_engine, security_engine, workflow_engine = _build_kernel(tmp_path)
    await kernel.boot()
    return kernel, storage_engine, security_engine, workflow_engine


async def _seed_principal(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("workflow-dispatch-test-credential")

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
            "password": "workflow-dispatch-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.asyncio
async def test_no_token_is_denied_authentication(tmp_path: Path) -> None:
    kernel, _storage, _security, _workflow = await _boot_kernel(tmp_path)

    request = CapabilityRequest(capability_name=_CAPABILITY, session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_without_workflow_read_permission_is_denied_authorization(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _workflow = await _boot_kernel(tmp_path)

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
async def test_authenticated_with_workflow_read_permission_lists_empty_registry(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _workflow = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "workflow:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert result == []


@pytest.mark.asyncio
async def test_authenticated_with_workflow_read_permission_lists_registered_definition(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, workflow_engine = await _boot_kernel(tmp_path)
    step = WorkflowStep(id="s1", name="Step 1")
    workflow_engine.register_definition(WorkflowDefinition(id="wf_demo", name="Demo Workflow", steps=[step]))

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "workflow:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name=_CAPABILITY,
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert len(result) == 1
    assert result[0].id == "wf_demo"
    assert result[0].name == "Demo Workflow"
