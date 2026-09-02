"""M6.4-4 regression suite: boot-time reconciliation for stranded WAITING_APPROVAL executions.

The Event Engine's `publish` is a direct, synchronous, in-process call with
no retry and no outbox/queue backing it. If a process crashes between an
approval ticket's DB transition to a terminal state (APPROVED/REJECTED/
EXPIRED) and `ExternalExecutionManager.on_approval_decided` actually
completing, the execution is left parked in WAITING_APPROVAL forever even
though its ticket has already resolved -- M6.3-4's own
`recover_stranded_executions` explicitly does NOT touch WAITING_APPROVAL
rows (by design, for a different reason: nothing dispatches them without a
real event).

This suite proves `ExternalExecutionManager.reconcile_stranded_waiting_approvals`
(wired into `WorkflowEngine.start()`) closes that gap, failing closed on
every ambiguity: a REJECTED/EXPIRED ticket cancels the execution, a missing
ticket cancels the execution (never guesses), a still-PENDING ticket is left
alone, and an APPROVED ticket is deliberately NEVER auto-resumed.
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
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import (
    ExternalExecutionRecord,
    ExternalExecutionRequest,
    ExternalExecutionStatus,
)

_TEST_MASTER_KEY = b"\x66" * 32
_TEST_SIGNING_KEY = b"\x77" * 32
_ROLE = "EXT_EXEC_RECOVERY_TEST_ROLE"
_TENANT = "tenant_ext_exec_recovery"


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_recovery_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_recovery_{uuid4().hex[:8]}"))
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
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:execute"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT,
                principal_id="user_recovery_requester",
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
    await security_engine.put_secret("vault:recovery-secret", _TENANT, "real-secret-value")
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="prof-ext-recovery",
            tenant_id=_TENANT,
            name="Expiry Recovery Test Profile",
            driver_id="connector-dummy",
            secret_handle="vault:recovery-secret",
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


async def _submit_paused_execution(kernel: Kernel, requester_principal):
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    request = ExternalExecutionRequest(
        tenant_id=_TENANT,
        operation_type="CAPABILITY",
        target="kortex.connector.action.execute",
        parameters={
            "request": ActionRequest(
                request_id=f"ext-recovery-{uuid4()}",
                profile_id="prof-ext-recovery",
                action_type=ConnectorActionType.FETCH,
            )
        },
        timeout_seconds=10.0,
        requires_approval=True,
        required_approval_role=_ROLE,
        created_by=requester_principal.principal_id,
    )
    record = await executor.execute_operation(request, principal=requester_principal)
    return executor, record


@pytest.mark.asyncio
async def test_stranded_waiting_execution_with_expired_ticket_is_cancelled_on_reconciliation(
    kernel_env: Kernel,
) -> None:
    """Simulates the exact crash scenario: the ticket already reached
    EXPIRED (a real DB transition, e.g. via a real sweep), but the process
    is presumed to have crashed before the propagating event was ever
    delivered -- the execution is still WAITING_APPROVAL. Reconciliation
    must cancel it, matching what `on_approval_decided` would have done had
    the event actually arrived."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_recovery_requester")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor, record = await _submit_paused_execution(kernel, requester)
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL

    # Directly force the ticket to EXPIRED at the store level -- simulating
    # "the DB transition happened, but the event never reached the handler"
    # without needing to wait on a real timeout.
    await workflow_engine.approval_manager._store.atomic_expire_request(
        request_id=record.approval_request_id, tenant_id=_TENANT, outbox_store=None
    )
    ticket = await workflow_engine.approval_manager.get_request(record.approval_request_id, tenant_id=_TENANT)
    assert ticket is not None
    assert ticket.state.value == "EXPIRED"

    # Still WAITING_APPROVAL -- the "crash" prevented propagation.
    still_waiting = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert still_waiting is not None
    assert still_waiting.status == ExternalExecutionStatus.WAITING_APPROVAL

    reconciled = await executor.reconcile_stranded_waiting_approvals(tenant_id=_TENANT)
    assert any(r.id == record.id for r in reconciled)

    final = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert final is not None
    assert final.status == ExternalExecutionStatus.CANCELLED
    assert final.output is None


@pytest.mark.asyncio
async def test_stranded_waiting_execution_with_missing_ticket_fails_closed(kernel_env: Kernel) -> None:
    """A WAITING_APPROVAL execution whose approval_request_id points at a
    ticket that no longer exists must fail closed (cancelled), never
    resumed and never silently ignored."""
    kernel = kernel_env
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    orphan_id = uuid4()
    orphan = ExternalExecutionRecord(
        id=orphan_id,
        request_id=orphan_id,
        tenant_id=_TENANT,
        operation_type="CAPABILITY",
        target="kortex.connector.action.execute",
        status=ExternalExecutionStatus.WAITING_APPROVAL,
        approval_request_id=uuid4(),  # a ticket id that was never actually created
        created_by="SYSTEM",
    )
    await executor._store.save_execution(
        record=orphan,
        parameters={"request": {"request_id": "orphan", "profile_id": "prof-ext-recovery", "action_type": "FETCH"}},
        tenant_id=_TENANT,
        outbox_store=executor._outbox_store,
    )

    reconciled = await executor.reconcile_stranded_waiting_approvals(tenant_id=_TENANT)
    assert any(r.id == orphan_id for r in reconciled)

    final = await executor.get_execution(orphan_id, tenant_id=_TENANT)
    assert final is not None
    assert final.status == ExternalExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_stranded_waiting_execution_with_pending_ticket_is_left_untouched(kernel_env: Kernel) -> None:
    """A genuinely still-pending ticket means the execution is correctly
    waiting -- reconciliation must not touch it."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_recovery_requester")
    executor, record = await _submit_paused_execution(kernel, requester)
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL

    reconciled = await executor.reconcile_stranded_waiting_approvals(tenant_id=_TENANT)
    assert all(r.id != record.id for r in reconciled)

    still_waiting = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert still_waiting is not None
    assert still_waiting.status == ExternalExecutionStatus.WAITING_APPROVAL


@pytest.mark.asyncio
async def test_stranded_waiting_execution_with_approved_ticket_is_never_auto_resumed(kernel_env: Kernel) -> None:
    """The one deliberately conservative case: even though the ticket is
    APPROVED (which, in isolation, would be 'safe' to resume since nothing
    was ever dispatched), boot-time reconciliation must NOT auto-resume --
    only on_approval_decided's own real event-driven path may do that."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_recovery_requester")
    await _principal(kernel, "user_recovery_requester")  # reused; role is what matters here
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor, record = await _submit_paused_execution(kernel, requester)

    # Force the ticket straight to APPROVED at the store level, bypassing
    # submit_decision's self-approval guard and the normal event publish --
    # simulating "the decision committed, but the resume event was lost".
    from kortex.engines.workflow.models import ApprovalDecision, ApprovalState

    decision = ApprovalDecision(
        request_id=record.approval_request_id,
        tenant_id=_TENANT,
        approver_id="someone_else",
        decision=ApprovalState.APPROVED,
    )
    await workflow_engine.approval_manager._store.atomic_submit_decision(
        decision=decision, tenant_id=_TENANT, outbox_store=None
    )
    ticket = await workflow_engine.approval_manager.get_request(record.approval_request_id, tenant_id=_TENANT)
    assert ticket is not None
    assert ticket.state.value == "APPROVED"

    reconciled = await executor.reconcile_stranded_waiting_approvals(tenant_id=_TENANT)
    assert all(r.id != record.id for r in reconciled)

    final = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert final is not None
    assert final.status == ExternalExecutionStatus.WAITING_APPROVAL
    assert final.output is None
