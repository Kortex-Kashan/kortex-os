"""M6.3-3 regression suite: durable approval decision -> external execution resume/cancel.

Prior to M6.3-3, `ExternalExecutionManager.execute_operation`'s
WAITING_APPROVAL branch created an approval ticket and then simply returned
-- there was no code path anywhere that ever resumed (or explicitly
cancelled) that paused execution once a human decided the ticket. The ticket
itself never recorded a requester identity, correlation ID, or action
fingerprint, so even if something *had* resumed it, there was no way to
detect an approve-one/execute-another substitution attack.

This suite proves, against a real Kernel + real DurableApprovalManager + real
ConnectorEngine, that:
  A) an APPROVED decision resumes the paused execution exactly once and it
     actually reaches the real Connector driver;
  B) a REJECTED decision transitions the execution to CANCELLED and the
     connector is never invoked;
  C) a decision whose recorded action fingerprint no longer matches the
     execution's current dispatch context is refused and the execution is
     cancelled rather than resumed;
  D) the ticket a `requires_approval=True` external execution creates
     records a real requester identity, so the pre-existing self-approval
     guard (`DurableApprovalManager.submit_decision`) actually applies to it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.models import ActionRequest, ConnectorActionType, ConnectorProfile
from kortex.engines.event.engine import Event
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import ExternalExecutionRequest, ExternalExecutionStatus

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32
_ROLE = "EXT_EXEC_APPROVAL_TEST_ROLE"
_TENANT = "tenant_ext_exec_approval"


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_ext_appr_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_ext_appr_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(data_store=data_store)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="workflow:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="workflow:approve"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="workflow:read"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:execute"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT,
                principal_id="user_ext_appr_requester",
                principal_type="USER",
                credential_hash=hasher.hash("pass"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT,
                principal_id="user_ext_appr_approver",
                principal_type="USER",
                credential_hash=hasher.hash("pass"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    connector_engine.register_driver(DummyConnectorDriver())
    await security_engine.put_secret("vault:ext-appr-secret", _TENANT, "real-secret-value")
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="prof-ext-appr",
            tenant_id=_TENANT,
            name="External Execution Approval Resume Profile",
            driver_id="connector-dummy",
            secret_handle="vault:ext-appr-secret",
        )
    )

    try:
        yield kernel
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _principal(kernel: Kernel, principal_id: str, password: str = "pass"):
    security_engine: SecurityEngine = kernel.get_engine("security")
    return await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": _TENANT, "principal_id": principal_id, "password": password}
    )


async def _submit_paused_execution(kernel: Kernel, requester_principal, profile_id: str = "prof-ext-appr"):
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    request = ExternalExecutionRequest(
        tenant_id=_TENANT,
        operation_type="CAPABILITY",
        target="kortex.connector.action.execute",
        parameters={
            "request": ActionRequest(
                request_id=f"ext-appr-{uuid4()}",
                profile_id=profile_id,
                action_type=ConnectorActionType.FETCH,
            ),
        },
        timeout_seconds=10.0,
        requires_approval=True,
        required_approval_role=_ROLE,
        created_by=requester_principal.principal_id,
    )
    record = await executor.execute_operation(request, principal=requester_principal)
    return executor, record


@pytest.mark.asyncio
async def test_approved_decision_resumes_execution_exactly_once(kernel_env: Kernel) -> None:
    """An APPROVED decision on the durable ticket resumes the paused execution
    and it genuinely reaches the real Connector driver -- not a fabricated
    success, and not a duplicate resume from a second event delivery."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_ext_appr_requester")
    approver = await _principal(kernel, "user_ext_appr_approver")

    executor, record = await _submit_paused_execution(kernel, requester)
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL
    assert record.approval_request_id is not None

    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    result = await workflow_engine.decide_approval_request(
        request_id=record.approval_request_id,
        decision="APPROVED",
        approver_id=approver.principal_id,
        principal=approver,
    )
    assert result["state"] == "APPROVED"

    resumed = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert resumed is not None
    assert resumed.status == ExternalExecutionStatus.COMPLETED
    assert resumed.output["response_payload"]["mock_driver_id"] == "connector-dummy"
    assert resumed.output["response_payload"]["secret_authenticated"] is True
    assert resumed.attempts == 1


@pytest.mark.asyncio
async def test_rejected_decision_cancels_execution_with_no_connector_call(kernel_env: Kernel) -> None:
    """A REJECTED decision transitions the execution straight to CANCELLED --
    the paused operation must never reach the connector."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_ext_appr_requester")
    approver = await _principal(kernel, "user_ext_appr_approver")

    executor, record = await _submit_paused_execution(kernel, requester)

    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    result = await workflow_engine.decide_approval_request(
        request_id=record.approval_request_id,
        decision="REJECTED",
        approver_id=approver.principal_id,
        principal=approver,
    )
    assert result["state"] == "REJECTED"

    final = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert final is not None
    assert final.status == ExternalExecutionStatus.CANCELLED
    assert final.output is None


@pytest.mark.asyncio
async def test_self_approval_denied_for_external_execution_ticket(kernel_env: Kernel) -> None:
    """The pre-existing self-approval guard (M6.2-3) actually applies to
    external-execution tickets now that a real requester identity is
    recorded on ticket creation."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_ext_appr_requester")

    executor, record = await _submit_paused_execution(kernel, requester)

    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    with pytest.raises(Exception, match="cannot decide an approval ticket it itself requested"):
        await workflow_engine.decide_approval_request(
            request_id=record.approval_request_id,
            decision="APPROVED",
            approver_id=requester.principal_id,
            principal=requester,
        )

    untouched = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert untouched is not None
    assert untouched.status == ExternalExecutionStatus.WAITING_APPROVAL


@pytest.mark.asyncio
async def test_fingerprint_mismatch_refuses_resume_and_cancels(kernel_env: Kernel) -> None:
    """A `workflow.approval.decided` event carrying an `action_fingerprint`
    that no longer matches the execution's current dispatch context (e.g. an
    approve-one/execute-another substitution, or a stale approval delivered
    after the underlying request changed) must be refused -- the execution
    is cancelled rather than resumed."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_ext_appr_requester")

    executor, record = await _submit_paused_execution(kernel, requester)
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL

    forged_event = Event(
        topic="workflow.approval.decided",
        payload={
            "request_id": str(record.approval_request_id),
            "tenant_id": _TENANT,
            "decision": "APPROVED",
            "correlation_id": None,
            "action_fingerprint": "0" * 64,  # deliberately wrong
            "context_snapshot": {
                "action": "external_execution",
                "execution_id": str(record.id),
                "target": record.target,
            },
            "decider_session_token": None,
        },
    )
    await executor.on_approval_decided(forged_event)

    final = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert final is not None
    assert final.status == ExternalExecutionStatus.CANCELLED
    assert final.output is None
