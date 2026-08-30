"""M6.0-3 regression suite: tenant-isolation repair on 12 Workflow Engine handlers.

Prior to this fix, `create_approval_request`, `list_approval_requests`,
`get_approval_request`, `list_schedules`, `get_schedule`,
`list_instances_durable`, `get_instance_durable` (also reachable via the
legacy `kortex.workflow.state.get` alias), `get_external_execution`,
`list_external_executions`, `start_workflow`, `resume_workflow`, and
`cancel_workflow` all trusted a caller-supplied `tenant_id` parameter with no
cross-check against the authenticated principal's own tenant. Every test here
drives the real Kernel capability-dispatch boundary — real `SecurityEngine`
authentication, real RBAC — with two principals seeded in two distinct
tenants, exactly the shape the pre-existing "isolation" tests (which call
managers/stores directly) never exercised.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
from argon2 import PasswordHasher

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import ResourceNotFoundError
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import ScheduleNotFoundError
from kortex.engines.workflow.models import (
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)

_TEST_MASTER_KEY = b"\xcc" * 32
_TEST_SIGNING_KEY = b"\xdd" * 32
_ROLE = "TENANT_ISOLATION_TEST_ROLE"
_ALPHA = "tenant_alpha_iso"
_BETA = "tenant_beta_iso"


@pytest.fixture
async def kernel(tmp_path: Path) -> AsyncGenerator[Kernel, None]:
    db_file = tmp_path / f"test_tenant_iso_{uuid4().hex[:8]}.db"
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_file}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    k = Kernel()
    k._db_manager = db_manager

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_iso_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine()

    k.register_engine(storage_engine)
    k.register_engine(security_engine)
    k.register_engine(workflow_engine)
    await k.boot()

    hasher = PasswordHasher()

    async def _seed(session) -> None:  # noqa: ANN001
        perms = [
            "workflow:start",
            "workflow:read",
            "workflow:cancel",
            "workflow:schedule",
            "workflow:execute",
            "approval:write",
            "approval:read",
        ]
        session.add_all(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission=p) for p in perms)
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_ALPHA,
                principal_id="user_alpha",
                principal_type="USER",
                credential_hash=hasher.hash("pass-alpha"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_BETA,
                principal_id="user_beta",
                principal_type="USER",
                credential_hash=hasher.hash("pass-beta"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        await session.flush()

    await storage_engine.data.execute_in_transaction(_seed)

    plain_def = WorkflowDefinition(
        id="iso_test_def_plain",
        name="Plain Test Workflow",
        version="1.0.0",
        steps=[WorkflowStep(id="step_1", name="Step 1", capability_name=None)],
    )
    workflow_engine.register_definition(plain_def, tenant_id=_ALPHA)
    workflow_engine.register_definition(plain_def, tenant_id=_BETA)

    yield k

    await k.shutdown()
    await db_manager.disconnect()


async def _token(kernel: Kernel, tenant_id: str, principal_id: str, password: str):  # noqa: ANN001
    security_engine: SecurityEngine = kernel.get_engine("security")
    auth = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": password,
        }
    )
    return await security_engine.authentication_manager.issue_token(auth)


async def _tokens(kernel: Kernel):  # noqa: ANN001
    token_alpha = await _token(kernel, _ALPHA, "user_alpha", "pass-alpha")
    token_beta = await _token(kernel, _BETA, "user_beta", "pass-beta")
    return token_alpha, token_beta


def _waiting_instance(tenant_id: str, definition_id: str = "iso_test_def_plain") -> WorkflowInstance:
    return WorkflowInstance(
        definition_id=definition_id,
        tenant_id=tenant_id,
        current_step_index=0,
        current_step_id="step_1",
        state=WorkflowState.WAITING,
        status=WorkflowStatus.WAITING_APPROVAL,
    )


@pytest.mark.asyncio
async def test_create_approval_request_forces_principal_tenant_not_spoofed_tenant(kernel: Kernel) -> None:
    """A principal in tenant beta cannot create a ticket inside tenant alpha's namespace."""
    _, token_beta = await _tokens(kernel)

    res = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.approval.create",
            session_token=token_beta,
            parameters={"required_role": "approver", "tenant_id": _ALPHA},
            context={"resource_tenant_id": _BETA},
        )
    )
    assert res["tenant_id"] == _BETA
    assert res["tenant_id"] != _ALPHA


@pytest.mark.asyncio
async def test_list_approval_requests_excludes_other_tenant(kernel: Kernel) -> None:
    """A principal in tenant beta cannot list tenant alpha's approval tickets by spoofing tenant_id."""
    token_alpha, token_beta = await _tokens(kernel)

    created = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.approval.create",
            session_token=token_alpha,
            parameters={"required_role": "approver", "tenant_id": _ALPHA},
            context={"resource_tenant_id": _ALPHA},
        )
    )
    alpha_ticket_id = created["id"]

    res = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.approval.list",
            session_token=token_beta,
            parameters={"tenant_id": _ALPHA},
            context={"resource_tenant_id": _BETA},
        )
    )
    assert all(r["id"] != alpha_ticket_id for r in res)


@pytest.mark.asyncio
async def test_get_approval_request_denies_cross_tenant(kernel: Kernel) -> None:
    """A principal in tenant beta cannot fetch tenant alpha's known ticket by spoofing tenant_id."""
    token_alpha, token_beta = await _tokens(kernel)

    created = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.approval.create",
            session_token=token_alpha,
            parameters={"required_role": "approver", "tenant_id": _ALPHA},
            context={"resource_tenant_id": _ALPHA},
        )
    )
    alpha_ticket_id = created["id"]

    with pytest.raises(ResourceNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.approval.get",
                session_token=token_beta,
                parameters={"request_id": alpha_ticket_id, "tenant_id": _ALPHA},
                context={"resource_tenant_id": _BETA},
            )
        )


@pytest.mark.asyncio
async def test_list_schedules_excludes_other_tenant(kernel: Kernel) -> None:
    token_alpha, token_beta = await _tokens(kernel)

    created = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.schedule.create",
            session_token=token_alpha,
            parameters={
                "name": "iso_alpha_schedule",
                "definition_id": "iso_test_def_plain",
                "schedule_type": "INTERVAL",
                "interval_seconds": 3600,
                "tenant_id": _ALPHA,
            },
            context={"resource_tenant_id": _ALPHA},
        )
    )
    alpha_schedule_id = created["id"]

    res = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.schedule.list",
            session_token=token_beta,
            parameters={"tenant_id": _ALPHA},
            context={"resource_tenant_id": _BETA},
        )
    )
    assert all(s["id"] != alpha_schedule_id for s in res)


@pytest.mark.asyncio
async def test_get_schedule_denies_cross_tenant(kernel: Kernel) -> None:
    token_alpha, token_beta = await _tokens(kernel)

    created = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.schedule.create",
            session_token=token_alpha,
            parameters={
                "name": "iso_alpha_schedule_get",
                "definition_id": "iso_test_def_plain",
                "schedule_type": "INTERVAL",
                "interval_seconds": 3600,
                "tenant_id": _ALPHA,
            },
            context={"resource_tenant_id": _ALPHA},
        )
    )
    alpha_schedule_id = created["id"]

    with pytest.raises(ScheduleNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.schedule.get",
                session_token=token_beta,
                parameters={"schedule_id": alpha_schedule_id, "tenant_id": _ALPHA},
                context={"resource_tenant_id": _BETA},
            )
        )


@pytest.mark.asyncio
async def test_list_instances_durable_excludes_other_tenant(kernel: Kernel) -> None:
    token_alpha, token_beta = await _tokens(kernel)

    created = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.instance.start",
            session_token=token_alpha,
            parameters={"definition_id": "iso_test_def_plain", "tenant_id": _ALPHA},
            context={"resource_tenant_id": _ALPHA},
        )
    )
    alpha_instance_id = str(created.id)

    res = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.instance.list",
            session_token=token_beta,
            parameters={"tenant_id": _ALPHA},
            context={"resource_tenant_id": _BETA},
        )
    )
    assert all(i["id"] != alpha_instance_id for i in res)


@pytest.mark.asyncio
async def test_get_instance_durable_denies_cross_tenant(kernel: Kernel) -> None:
    """Covers both `kortex.workflow.instance.get` and its legacy `state.get` alias (same handler)."""
    token_alpha, token_beta = await _tokens(kernel)

    created = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.instance.start",
            session_token=token_alpha,
            parameters={"definition_id": "iso_test_def_plain", "tenant_id": _ALPHA},
            context={"resource_tenant_id": _ALPHA},
        )
    )
    alpha_instance_id = str(created.id)

    for capability_name in ("kortex.workflow.instance.get", "kortex.workflow.state.get"):
        with pytest.raises(ResourceNotFoundError):
            await kernel.invoke_capability(
                CapabilityRequest(
                    capability_name=capability_name,
                    session_token=token_beta,
                    parameters={"instance_id": alpha_instance_id, "tenant_id": _ALPHA},
                    context={"resource_tenant_id": _BETA},
                )
            )


@pytest.mark.asyncio
async def test_get_external_execution_denies_cross_tenant(kernel: Kernel) -> None:
    token_alpha, token_beta = await _tokens(kernel)

    created = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.external.execute",
            session_token=token_alpha,
            parameters={
                "target": "iso-test-target",
                "requires_approval": True,
                "required_approval_role": "approver",
                "tenant_id": _ALPHA,
            },
            context={"resource_tenant_id": _ALPHA},
        )
    )
    alpha_execution_id = created["id"]
    assert created["tenant_id"] == _ALPHA

    with pytest.raises(ResourceNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.external.get",
                session_token=token_beta,
                parameters={"execution_id": alpha_execution_id, "tenant_id": _ALPHA},
                context={"resource_tenant_id": _BETA},
            )
        )


@pytest.mark.asyncio
async def test_list_external_executions_excludes_other_tenant(kernel: Kernel) -> None:
    token_alpha, token_beta = await _tokens(kernel)

    created = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.external.execute",
            session_token=token_alpha,
            parameters={
                "target": "iso-test-target-list",
                "requires_approval": True,
                "required_approval_role": "approver",
                "tenant_id": _ALPHA,
            },
            context={"resource_tenant_id": _ALPHA},
        )
    )
    alpha_execution_id = created["id"]

    res = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.external.list",
            session_token=token_beta,
            parameters={"tenant_id": _ALPHA},
            context={"resource_tenant_id": _BETA},
        )
    )
    assert all(e["id"] != alpha_execution_id for e in res)


@pytest.mark.asyncio
async def test_resume_workflow_denies_cross_tenant_and_leaves_state_unchanged(kernel: Kernel) -> None:
    """A principal in tenant beta cannot resume tenant alpha's waiting instance by spoofing tenant_id."""
    _, token_beta = await _tokens(kernel)

    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    instance = _waiting_instance(_ALPHA)
    assert workflow_engine.workflow_store is not None
    await workflow_engine.workflow_store.save_instance(instance, tenant_id=_ALPHA)

    with pytest.raises(ResourceNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.instance.resume",
                session_token=token_beta,
                parameters={"instance_id": str(instance.id), "tenant_id": _ALPHA},
                context={"resource_tenant_id": _BETA},
            )
        )

    reloaded = await workflow_engine.workflow_store.get_instance(instance.id, tenant_id=_ALPHA)
    assert reloaded is not None
    assert reloaded.state == WorkflowState.WAITING


@pytest.mark.asyncio
async def test_cancel_workflow_denies_cross_tenant_and_leaves_state_unchanged(kernel: Kernel) -> None:
    """A principal in tenant beta cannot cancel tenant alpha's waiting instance by spoofing tenant_id."""
    _, token_beta = await _tokens(kernel)

    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    instance = _waiting_instance(_ALPHA)
    assert workflow_engine.workflow_store is not None
    await workflow_engine.workflow_store.save_instance(instance, tenant_id=_ALPHA)

    with pytest.raises(ResourceNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.instance.cancel",
                session_token=token_beta,
                parameters={"instance_id": str(instance.id), "tenant_id": _ALPHA},
                context={"resource_tenant_id": _BETA},
            )
        )

    reloaded = await workflow_engine.workflow_store.get_instance(instance.id, tenant_id=_ALPHA)
    assert reloaded is not None
    assert reloaded.state != WorkflowState.CANCELLED
    assert reloaded.state == WorkflowState.WAITING


@pytest.mark.asyncio
async def test_start_workflow_forces_principal_tenant_not_spoofed_tenant(kernel: Kernel) -> None:
    """A principal in tenant beta cannot create a workflow instance inside tenant alpha's namespace,
    whether by a spoofed `tenant_id` parameter or a forged `session_token` tenant claim."""
    _, token_beta = await _tokens(kernel)

    created = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.instance.start",
            session_token=token_beta,
            parameters={
                "definition_id": "iso_test_def_plain",
                "tenant_id": _ALPHA,
                "session_token": {"tenant_id": _ALPHA},
            },
            context={"resource_tenant_id": _BETA},
        )
    )
    assert created.tenant_id == _BETA
    assert created.tenant_id != _ALPHA


@pytest.mark.asyncio
async def test_legitimate_same_tenant_operations_still_work(kernel: Kernel) -> None:
    """Regression guard: a principal operating on its own tenant's resources is unaffected by the fix."""
    token_alpha, _ = await _tokens(kernel)

    created = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.approval.create",
            session_token=token_alpha,
            parameters={"required_role": "approver", "tenant_id": _ALPHA},
            context={"resource_tenant_id": _ALPHA},
        )
    )
    assert created["tenant_id"] == _ALPHA

    fetched = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.approval.get",
            session_token=token_alpha,
            parameters={"request_id": created["id"], "tenant_id": _ALPHA},
            context={"resource_tenant_id": _ALPHA},
        )
    )
    assert fetched["id"] == created["id"]

    listed = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.approval.list",
            session_token=token_alpha,
            parameters={"tenant_id": _ALPHA},
            context={"resource_tenant_id": _ALPHA},
        )
    )
    assert any(r["id"] == created["id"] for r in listed)

    instance = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.instance.start",
            session_token=token_alpha,
            parameters={"definition_id": "iso_test_def_plain", "tenant_id": _ALPHA},
            context={"resource_tenant_id": _ALPHA},
        )
    )
    assert instance.tenant_id == _ALPHA
