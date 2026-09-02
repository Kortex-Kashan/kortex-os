"""M6.3-5 canonical vertical slice: Governed External Execution, end-to-end.

Drives the full path -- real Kernel, real RBAC, real DurableApprovalManager,
real ConnectorEngine + driver -- through the four cases the M6.3 master
implementation authorization requires as the milestone's own acceptance
proof:

  A) Governance denial: a principal lacking `workflow:execute` is refused
     the outer capability outright, before any approval/connector logic
     ever runs.
  B) Approval required -> REJECTED: the paused execution is cancelled, the
     connector is never invoked.
  C) Approval required -> APPROVED: the execution resumes and completes
     against the real connector, with a full, inspectable audit/trace
     lineage (ticket -> execution -> connector driver response).
  D) Cross-tenant attack: a second tenant's principal can neither read,
     approve, nor otherwise resume the first tenant's paused execution --
     every attempted cross-tenant access fails closed, masked as "not
     found" rather than a distinguishable authorization error where that
     masking is the existing convention (approval-ticket lookup), and as an
     explicit authorization denial where the ticket *is* resolvable but
     belongs to a different tenant (submit_decision's own tenant check).
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
from kortex.engines.connector.models import ActionRequest, ConnectorActionType, ConnectorProfile
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import ExternalExecutionError
from kortex.engines.workflow.models import ExternalExecutionRequest, ExternalExecutionStatus

_TEST_MASTER_KEY = b"\x11" * 32
_TEST_SIGNING_KEY = b"\x22" * 32
_ROLE_FULL = "EXT_EXEC_SLICE_FULL_ROLE"
_ROLE_READ_ONLY = "EXT_EXEC_SLICE_READ_ONLY_ROLE"
_TENANT_A = "tenant_ext_exec_slice_a"
_TENANT_B = "tenant_ext_exec_slice_b"


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_ext_slice_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_ext_slice_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(data_store=data_store)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE_FULL, permission="workflow:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE_FULL, permission="workflow:approve"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE_FULL, permission="workflow:read"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE_FULL, permission="connector:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE_READ_ONLY, permission="workflow:read"))

        for tenant, suffix in ((_TENANT_A, "a"), (_TENANT_B, "b")):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant,
                    principal_id=f"user_requester_{suffix}",
                    principal_type="USER",
                    credential_hash=hasher.hash("pass"),
                    roles=[_ROLE_FULL],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant,
                    principal_id=f"user_approver_{suffix}",
                    principal_type="USER",
                    credential_hash=hasher.hash("pass"),
                    roles=[_ROLE_FULL],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_A,
                principal_id="user_no_execute_perm",
                principal_type="USER",
                credential_hash=hasher.hash("pass"),
                roles=[_ROLE_READ_ONLY],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    connector_engine.register_driver(DummyConnectorDriver())
    for tenant, suffix in ((_TENANT_A, "a"), (_TENANT_B, "b")):
        await security_engine.put_secret(f"vault:ext-slice-secret-{suffix}", tenant, f"real-secret-{suffix}")
        await connector_engine.profile_manager.register_profile(
            ConnectorProfile(
                profile_id=f"prof-ext-slice-{suffix}",
                tenant_id=tenant,
                name=f"External Execution Slice Profile ({suffix})",
                driver_id="connector-dummy",
                secret_handle=f"vault:ext-slice-secret-{suffix}",
            )
        )

    try:
        yield kernel
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _principal(kernel: Kernel, tenant_id: str, principal_id: str, password: str = "pass"):
    security_engine: SecurityEngine = kernel.get_engine("security")
    return await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": password}
    )


async def _token(kernel: Kernel, principal) -> dict:
    security_engine: SecurityEngine = kernel.get_engine("security")
    minted = await security_engine.authentication_manager.issue_token(principal)
    return minted.model_dump() if hasattr(minted, "model_dump") else minted


def _outer_capability_request(token: dict, profile_suffix: str, extra_params: dict | None = None):
    params = {
        "target": "kortex.connector.action.execute",
        "operation_type": "CAPABILITY",
        "parameters": {
            "request": ActionRequest(
                request_id=f"ext-slice-{uuid4()}",
                profile_id=f"prof-ext-slice-{profile_suffix}",
                action_type=ConnectorActionType.FETCH,
            ),
        },
        "timeout_seconds": 10.0,
        "session_token": token,
    }
    if extra_params:
        params.update(extra_params)
    return CapabilityRequest(
        capability_name="kortex.workflow.external.execute",
        session_token=token,
        parameters=params,
        context={"resource_tenant_id": _TENANT_A},
    )


@pytest.mark.asyncio
async def test_case_a_governance_denial_without_workflow_execute_permission(kernel_env: Kernel) -> None:
    """A principal holding only `workflow:read` is refused the outer
    capability outright -- the RBAC gate, not the approval/connector layer,
    is what stops it."""
    kernel = kernel_env
    denied_principal = await _principal(kernel, _TENANT_A, "user_no_execute_perm")
    token = await _token(kernel, denied_principal)

    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(_outer_capability_request(token, "a"))


@pytest.mark.asyncio
async def test_case_b_approval_rejected_cancels_with_no_connector_call(kernel_env: Kernel) -> None:
    """Approval required, decided REJECTED: the execution is cancelled and
    the connector is never reached."""
    kernel = kernel_env
    requester = await _principal(kernel, _TENANT_A, "user_requester_a")
    approver = await _principal(kernel, _TENANT_A, "user_approver_a")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    record = await executor.execute_operation(
        ExternalExecutionRequest(
            tenant_id=_TENANT_A,
            operation_type="CAPABILITY",
            target="kortex.connector.action.execute",
            parameters={
                "request": ActionRequest(
                    request_id=f"ext-slice-b-{uuid4()}",
                    profile_id="prof-ext-slice-a",
                    action_type=ConnectorActionType.FETCH,
                )
            },
            timeout_seconds=10.0,
            requires_approval=True,
            required_approval_role=_ROLE_FULL,
            created_by=requester.principal_id,
        ),
        principal=requester,
    )
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL

    result = await workflow_engine.decide_approval_request(
        request_id=record.approval_request_id,
        decision="REJECTED",
        approver_id=approver.principal_id,
        principal=approver,
    )
    assert result["state"] == "REJECTED"

    final = await executor.get_execution(record.id, tenant_id=_TENANT_A)
    assert final is not None
    assert final.status == ExternalExecutionStatus.CANCELLED
    assert final.output is None


@pytest.mark.asyncio
async def test_case_c_approval_approved_completes_with_full_trace(kernel_env: Kernel) -> None:
    """Approval required, decided APPROVED: the execution resumes, reaches
    the real connector driver, and the full lineage (ticket, execution,
    correlation, connector response) is inspectable end-to-end."""
    kernel = kernel_env
    requester = await _principal(kernel, _TENANT_A, "user_requester_a")
    approver = await _principal(kernel, _TENANT_A, "user_approver_a")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    correlation_id = f"corr-{uuid4()}"
    record = await executor.execute_operation(
        ExternalExecutionRequest(
            tenant_id=_TENANT_A,
            operation_type="CAPABILITY",
            target="kortex.connector.action.execute",
            parameters={
                "request": ActionRequest(
                    request_id=f"ext-slice-c-{uuid4()}",
                    profile_id="prof-ext-slice-a",
                    action_type=ConnectorActionType.FETCH,
                )
            },
            timeout_seconds=10.0,
            requires_approval=True,
            required_approval_role=_ROLE_FULL,
            correlation_id=correlation_id,
            created_by=requester.principal_id,
        ),
        principal=requester,
    )
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL
    assert record.correlation_id == correlation_id

    ticket = await workflow_engine.approval_manager.get_request(record.approval_request_id, tenant_id=_TENANT_A)
    assert ticket is not None
    assert ticket.requester_principal_id == requester.principal_id
    assert ticket.correlation_id == correlation_id
    assert ticket.action_fingerprint is not None

    result = await workflow_engine.decide_approval_request(
        request_id=record.approval_request_id,
        decision="APPROVED",
        approver_id=approver.principal_id,
        principal=approver,
    )
    assert result["state"] == "APPROVED"

    final = await executor.get_execution(record.id, tenant_id=_TENANT_A)
    assert final is not None
    assert final.status == ExternalExecutionStatus.COMPLETED
    assert final.correlation_id == correlation_id
    assert final.output["response_payload"]["mock_driver_id"] == "connector-dummy"
    assert final.output["response_payload"]["secret_authenticated"] is True


@pytest.mark.asyncio
async def test_case_d_cross_tenant_attack_fails_closed(kernel_env: Kernel) -> None:
    """A second tenant's principal cannot read, approve, or resume the first
    tenant's paused execution through any of the exposed capabilities."""
    kernel = kernel_env
    requester_a = await _principal(kernel, _TENANT_A, "user_requester_a")
    approver_b = await _principal(kernel, _TENANT_B, "user_approver_b")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    record = await executor.execute_operation(
        ExternalExecutionRequest(
            tenant_id=_TENANT_A,
            operation_type="CAPABILITY",
            target="kortex.connector.action.execute",
            parameters={
                "request": ActionRequest(
                    request_id=f"ext-slice-d-{uuid4()}",
                    profile_id="prof-ext-slice-a",
                    action_type=ConnectorActionType.FETCH,
                )
            },
            timeout_seconds=10.0,
            requires_approval=True,
            required_approval_role=_ROLE_FULL,
            created_by=requester_a.principal_id,
        ),
        principal=requester_a,
    )
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL

    # 1. Read attempt: masked as not-found across the tenant boundary.
    cross_tenant_read = await executor.get_execution(record.id, tenant_id=_TENANT_B)
    assert cross_tenant_read is None

    # 2. Decide/approve attempt: the ticket itself is not resolvable within
    # tenant B's scope, so it is masked as not-found rather than leaking its
    # existence via a distinguishable authorization error.
    with pytest.raises(Exception, match="not found"):
        await workflow_engine.decide_approval_request(
            request_id=record.approval_request_id,
            decision="APPROVED",
            approver_id=approver_b.principal_id,
            principal=approver_b,
        )

    # 3. The execution must remain untouched -- still paused, never resumed
    # by the failed cross-tenant attempt.
    untouched = await executor.get_execution(record.id, tenant_id=_TENANT_A)
    assert untouched is not None
    assert untouched.status == ExternalExecutionStatus.WAITING_APPROVAL

    # 4. Cross-tenant profile substitution: tenant B cannot point its own
    # execution at tenant A's connector profile.
    with pytest.raises(ExternalExecutionError):
        await executor.execute_operation(
            ExternalExecutionRequest(
                tenant_id=_TENANT_B,
                operation_type="CAPABILITY",
                target="kortex.connector.action.execute",
                parameters={
                    "request": ActionRequest(
                        request_id=f"ext-slice-d-cross-{uuid4()}",
                        profile_id="prof-ext-slice-a",
                        action_type=ConnectorActionType.FETCH,
                    )
                },
                timeout_seconds=5.0,
                created_by=approver_b.principal_id,
            ),
            principal=approver_b,
            session_token=await _token(kernel, approver_b),
        )
