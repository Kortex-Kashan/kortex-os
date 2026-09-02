"""M6.4-5 canonical vertical slice: Approval Lifecycle Hardening & Expiry Resolution.

The final, six-case end-to-end certification for the complete M6.4 feature
set, driven against a real Kernel + RBAC + DurableApprovalManager +
ConnectorEngine, with the real background approval-expiry sweep daemon
(M6.4-2) running at a fast poll interval so Case C proves genuine production
reachability, not a manually-invoked sweep:

  A) Governance denial: a principal lacking `workflow:execute` is refused
     the outer capability outright (regression, unchanged from M6.3-5).
  B) Approval rejection: REJECTED cancels the execution, connector never
     invoked (regression, unchanged).
  C) Approval expiry: a genuinely short-lived ticket, propagated purely by
     the real running background daemon -- no manual sweep call anywhere
     in this test -- reaches EXPIRED, the execution is cancelled, the
     connector is never invoked, and the audit trail records
     reason="EXPIRED" (M6.4-1/M6.4-2/M6.4-3 together).
  D) Approval: APPROVED resumes and completes against the real connector
     with a full, inspectable trace (regression, unchanged).
  E) Cross-tenant attack: a second tenant can neither read, decide, nor
     forge an expiry decision against the first tenant's paused execution
     -- every attempt fails closed.
  F) Duplicate event: the same EXPIRED decision delivered twice cancels
     the execution exactly once.
"""

from __future__ import annotations

import asyncio
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
from kortex.engines.event.engine import Event
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import ExternalExecutionRequest, ExternalExecutionStatus, WorkflowSettings

_TEST_MASTER_KEY = b"\x12" * 32
_TEST_SIGNING_KEY = b"\x21" * 32
_ROLE_FULL = "EXT_EXEC_M64_SLICE_FULL_ROLE"
_ROLE_READ_ONLY = "EXT_EXEC_M64_SLICE_READ_ONLY_ROLE"
_TENANT_A = "tenant_m64_slice_a"
_TENANT_B = "tenant_m64_slice_b"


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_m64_slice_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_m64_slice_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine(
        settings=WorkflowSettings(approval_sweep_enabled=True, approval_sweep_interval_seconds=0.5)
    )
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
        await security_engine.put_secret(f"vault:m64-slice-secret-{suffix}", tenant, f"real-secret-{suffix}")
        await connector_engine.profile_manager.register_profile(
            ConnectorProfile(
                profile_id=f"prof-m64-slice-{suffix}",
                tenant_id=tenant,
                name=f"M6.4 Slice Profile ({suffix})",
                driver_id="connector-dummy",
                secret_handle=f"vault:m64-slice-secret-{suffix}",
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


def _outer_capability_request(token: dict, profile_suffix: str):
    return CapabilityRequest(
        capability_name="kortex.workflow.external.execute",
        session_token=token,
        parameters={
            "target": "kortex.connector.action.execute",
            "operation_type": "CAPABILITY",
            "parameters": {
                "request": ActionRequest(
                    request_id=f"ext-m64-{uuid4()}",
                    profile_id=f"prof-m64-slice-{profile_suffix}",
                    action_type=ConnectorActionType.FETCH,
                ),
            },
            "timeout_seconds": 10.0,
            "session_token": token,
        },
        context={"resource_tenant_id": _TENANT_A},
    )


async def _submit_paused_execution(kernel: Kernel, tenant: str, suffix: str, requester, approval_timeout_seconds=None):
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None
    request = ExternalExecutionRequest(
        tenant_id=tenant,
        operation_type="CAPABILITY",
        target="kortex.connector.action.execute",
        parameters={
            "request": ActionRequest(
                request_id=f"ext-m64-{uuid4()}",
                profile_id=f"prof-m64-slice-{suffix}",
                action_type=ConnectorActionType.FETCH,
            )
        },
        timeout_seconds=10.0,
        requires_approval=True,
        required_approval_role=_ROLE_FULL,
        approval_timeout_seconds=approval_timeout_seconds,
        created_by=requester.principal_id,
    )
    record = await executor.execute_operation(request, principal=requester)
    return executor, record


@pytest.mark.asyncio
async def test_case_a_governance_denial_without_workflow_execute_permission(kernel_env: Kernel) -> None:
    kernel = kernel_env
    denied_principal = await _principal(kernel, _TENANT_A, "user_no_execute_perm")
    token = await _token(kernel, denied_principal)

    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(_outer_capability_request(token, "a"))


@pytest.mark.asyncio
async def test_case_b_approval_rejected_cancels_with_no_connector_call(kernel_env: Kernel) -> None:
    kernel = kernel_env
    requester = await _principal(kernel, _TENANT_A, "user_requester_a")
    approver = await _principal(kernel, _TENANT_A, "user_approver_a")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")

    executor, record = await _submit_paused_execution(kernel, _TENANT_A, "a", requester)
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
async def test_case_c_approval_expiry_via_real_production_daemon(kernel_env: Kernel) -> None:
    """The centerpiece of M6.4: real expiry, real propagation, real
    cancellation, real audit -- with no manual sweep call anywhere in this
    test. The background daemon (started by WorkflowEngine.start(), which
    already ran when this fixture's kernel booted) is what actually does
    the work while this test just waits."""
    kernel = kernel_env
    requester = await _principal(kernel, _TENANT_A, "user_requester_a")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")

    executor, record = await _submit_paused_execution(kernel, _TENANT_A, "a", requester, approval_timeout_seconds=1)
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL

    await asyncio.sleep(3.0)

    ticket = await workflow_engine.approval_manager.get_request(record.approval_request_id, tenant_id=_TENANT_A)
    assert ticket is not None
    assert ticket.state.value == "EXPIRED"

    final = await executor.get_execution(record.id, tenant_id=_TENANT_A)
    assert final is not None
    assert final.status == ExternalExecutionStatus.CANCELLED
    assert final.output is None

    security_engine: SecurityEngine = kernel.get_engine("security")
    entries = await security_engine.audit_manager.get_audit_entries(
        tenant_id=_TENANT_A, action="kortex.workflow.external.cancelled"
    )
    matching = [e for e in entries if e.resource_id == str(record.id)]
    assert len(matching) == 1
    assert matching[0].context.get("reason") == "EXPIRED"


@pytest.mark.asyncio
async def test_case_d_approval_approved_completes_with_full_trace(kernel_env: Kernel) -> None:
    kernel = kernel_env
    requester = await _principal(kernel, _TENANT_A, "user_requester_a")
    approver = await _principal(kernel, _TENANT_A, "user_approver_a")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")

    correlation_id = f"corr-m64-{uuid4()}"
    executor = workflow_engine.external_executor
    assert executor is not None

    record = await executor.execute_operation(
        ExternalExecutionRequest(
            tenant_id=_TENANT_A,
            operation_type="CAPABILITY",
            target="kortex.connector.action.execute",
            parameters={
                "request": ActionRequest(
                    request_id=f"ext-m64-d-{uuid4()}",
                    profile_id="prof-m64-slice-a",
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
async def test_case_e_cross_tenant_attack_against_expiry_fails_closed(kernel_env: Kernel) -> None:
    """A second tenant can neither read the first tenant's paused execution
    nor forge an expiry decision that cancels it cross-tenant."""
    kernel = kernel_env
    requester_a = await _principal(kernel, _TENANT_A, "user_requester_a")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor, record = await _submit_paused_execution(kernel, _TENANT_A, "a", requester_a)
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL

    # 1. Cross-tenant read: masked as not-found.
    cross_read = await executor.get_execution(record.id, tenant_id=_TENANT_B)
    assert cross_read is None

    # 2. Forged EXPIRED event carrying tenant B: must not cancel tenant A's execution.
    forged_event = Event(
        topic="workflow.approval.decided",
        payload={
            "request_id": str(record.approval_request_id),
            "tenant_id": _TENANT_B,
            "decision": "EXPIRED",
            "context_snapshot": {
                "action": "external_execution",
                "execution_id": str(record.id),
                "target": record.target,
            },
        },
    )
    await executor.on_approval_decided(forged_event)

    untouched = await executor.get_execution(record.id, tenant_id=_TENANT_A)
    assert untouched is not None
    assert untouched.status == ExternalExecutionStatus.WAITING_APPROVAL

    # 3. Tenant B cannot decide tenant A's ticket at all (masked not-found).
    approver_b = await _principal(kernel, _TENANT_B, "user_approver_b")
    with pytest.raises(Exception, match="not found"):
        await workflow_engine.decide_approval_request(
            request_id=record.approval_request_id,
            decision="APPROVED",
            approver_id=approver_b.principal_id,
            principal=approver_b,
        )

    still_untouched = await executor.get_execution(record.id, tenant_id=_TENANT_A)
    assert still_untouched is not None
    assert still_untouched.status == ExternalExecutionStatus.WAITING_APPROVAL


@pytest.mark.asyncio
async def test_case_f_duplicate_expired_event_cancels_exactly_once(kernel_env: Kernel) -> None:
    kernel = kernel_env
    requester = await _principal(kernel, _TENANT_A, "user_requester_a")
    executor, record = await _submit_paused_execution(kernel, _TENANT_A, "a", requester)

    event = Event(
        topic="workflow.approval.decided",
        payload={
            "request_id": str(record.approval_request_id),
            "tenant_id": _TENANT_A,
            "decision": "EXPIRED",
            "context_snapshot": {
                "action": "external_execution",
                "execution_id": str(record.id),
                "target": record.target,
            },
        },
    )
    await executor.on_approval_decided(event)
    first = await executor.get_execution(record.id, tenant_id=_TENANT_A)
    assert first is not None
    assert first.status == ExternalExecutionStatus.CANCELLED

    await executor.on_approval_decided(event)
    second = await executor.get_execution(record.id, tenant_id=_TENANT_A)
    assert second is not None
    assert second.status == ExternalExecutionStatus.CANCELLED
    assert second.completed_at == first.completed_at
