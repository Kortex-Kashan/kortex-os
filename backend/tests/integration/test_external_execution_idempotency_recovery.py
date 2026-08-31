"""M6.3-4 regression suite: idempotency/replay protection and boot recovery.

Prior to M6.3-4, `ExternalExecutionModel` had only a non-unique index on
`(tenant_id, idempotency_key)` -- nothing stopped two requests sharing the
same caller-supplied key from creating two separate execution records and
genuinely dispatching to the external system twice. There was also no
recovery path at all for a `RUNNING` row left behind by a crash/restart --
on restart it would simply sit forever in `RUNNING`, invisible to any
scan.

This suite proves, against a real Kernel + real ConnectorEngine, that:
  A) two `execute_operation` calls sharing an idempotency key result in
     exactly one real connector invocation -- the second call replays the
     first's stored outcome instead of re-dispatching;
  B) the same replay guard also short-circuits a duplicate
     `requires_approval=True` request -- a second ticket is never created;
  C) delivering the same `workflow.approval.decided` event twice for one
     execution resumes it exactly once, never twice;
  D) a row stranded in RUNNING by a simulated crash is reconciled to FAILED
     by `ExternalExecutionManager.recover_stranded_executions` on boot,
     rather than being silently resumed or left invisible forever.
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
from kortex.engines.workflow.models import ExternalExecutionRecord, ExternalExecutionRequest, ExternalExecutionStatus

_TEST_MASTER_KEY = b"\x77" * 32
_TEST_SIGNING_KEY = b"\x88" * 32
_ROLE = "EXT_EXEC_IDEMPOTENCY_TEST_ROLE"
_TENANT = "tenant_ext_exec_idem"


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_ext_idem_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_ext_idem_{uuid4().hex[:8]}"))
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
                principal_id="user_ext_idem_requester",
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
                principal_id="user_ext_idem_approver",
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
    await security_engine.put_secret("vault:ext-idem-secret", _TENANT, "real-secret-value")
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="prof-ext-idem",
            tenant_id=_TENANT,
            name="External Execution Idempotency Profile",
            driver_id="connector-dummy",
            secret_handle="vault:ext-idem-secret",
        )
    )

    try:
        yield kernel
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _principal(kernel: Kernel, principal_id: str, password: str = "pass"):  # noqa: ANN001
    security_engine: SecurityEngine = kernel.get_engine("security")
    return await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": _TENANT, "principal_id": principal_id, "password": password}
    )


async def _token(kernel: Kernel, principal) -> dict:  # noqa: ANN001
    security_engine: SecurityEngine = kernel.get_engine("security")
    minted = await security_engine.authentication_manager.issue_token(principal)
    return minted.model_dump() if hasattr(minted, "model_dump") else minted


def _build_request(idempotency_key: str, requester, requires_approval: bool = False):  # noqa: ANN001
    return ExternalExecutionRequest(
        tenant_id=_TENANT,
        operation_type="CAPABILITY",
        target="kortex.connector.action.execute",
        parameters={
            "request": ActionRequest(
                request_id=f"ext-idem-{uuid4()}",
                profile_id="prof-ext-idem",
                action_type=ConnectorActionType.FETCH,
            ),
        },
        timeout_seconds=10.0,
        idempotency_key=idempotency_key,
        requires_approval=requires_approval,
        required_approval_role=_ROLE if requires_approval else None,
        created_by=requester.principal_id,
    )


@pytest.mark.asyncio
async def test_duplicate_immediate_execution_replays_instead_of_redispatching(kernel_env: Kernel) -> None:
    """Two `execute_operation` calls sharing an idempotency key result in
    exactly one real connector dispatch -- the second returns the first
    call's own record, not a fresh execution."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_ext_idem_requester")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    key = f"idem-key-{uuid4()}"
    token = await _token(kernel, requester)
    first = await executor.execute_operation(_build_request(key, requester), principal=requester, session_token=token)
    assert first.status == ExternalExecutionStatus.COMPLETED

    second = await executor.execute_operation(
        _build_request(key, requester), principal=requester, session_token=token
    )
    assert second.id == first.id
    assert second.status == ExternalExecutionStatus.COMPLETED
    assert second.attempts == first.attempts

    all_matching = [r for r in await executor.list_executions(tenant_id=_TENANT, limit=100) if r.idempotency_key == key]
    assert len(all_matching) == 1


@pytest.mark.asyncio
async def test_duplicate_approval_gated_request_does_not_create_second_ticket(kernel_env: Kernel) -> None:
    """A duplicate of an already-waiting-for-approval request (same
    idempotency key) must not create a second approval ticket."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_ext_idem_requester")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    key = f"idem-key-appr-{uuid4()}"
    first = await executor.execute_operation(
        _build_request(key, requester, requires_approval=True), principal=requester
    )
    assert first.status == ExternalExecutionStatus.WAITING_APPROVAL
    first_ticket_id = first.approval_request_id
    assert first_ticket_id is not None

    second = await executor.execute_operation(
        _build_request(key, requester, requires_approval=True), principal=requester
    )
    assert second.id == first.id
    assert second.approval_request_id == first_ticket_id


@pytest.mark.asyncio
async def test_duplicate_approval_decided_event_resumes_exactly_once(kernel_env: Kernel) -> None:
    """Delivering the same `workflow.approval.decided` event twice for one
    execution must resume it exactly once -- the second delivery is an
    idempotent no-op because the record has already left WAITING_APPROVAL."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_ext_idem_requester")
    approver = await _principal(kernel, "user_ext_idem_approver")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    key = f"idem-key-decide-{uuid4()}"
    record = await executor.execute_operation(
        _build_request(key, requester, requires_approval=True), principal=requester
    )
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL

    ticket = await workflow_engine.approval_manager.get_request(record.approval_request_id, tenant_id=_TENANT)
    assert ticket is not None

    event = Event(
        topic="workflow.approval.decided",
        payload={
            "request_id": str(ticket.id),
            "tenant_id": _TENANT,
            "decision": "APPROVED",
            "correlation_id": ticket.correlation_id,
            "action_fingerprint": ticket.action_fingerprint,
            "context_snapshot": ticket.context_snapshot,
            "decider_session_token": (await _token(kernel, approver)),
        },
    )

    await executor.on_approval_decided(event)
    once = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert once is not None
    assert once.status == ExternalExecutionStatus.COMPLETED
    assert once.attempts == 1

    # Second, duplicate delivery of the identical event.
    await executor.on_approval_decided(event)
    twice = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert twice is not None
    assert twice.status == ExternalExecutionStatus.COMPLETED
    assert twice.attempts == 1
    assert twice.completed_at == once.completed_at


@pytest.mark.asyncio
async def test_stranded_running_execution_is_reconciled_on_boot_recovery(kernel_env: Kernel) -> None:
    """A row stranded in RUNNING by a simulated crash is deterministically
    failed closed by the boot recovery scan -- never silently re-dispatched,
    and never left invisible forever."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_ext_idem_requester")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    stranded_id = uuid4()
    stranded = ExternalExecutionRecord(
        id=stranded_id,
        request_id=stranded_id,
        tenant_id=_TENANT,
        operation_type="CAPABILITY",
        target="kortex.connector.action.execute",
        status=ExternalExecutionStatus.RUNNING,
        idempotency_key=f"idem-key-stranded-{uuid4()}",
        created_by=requester.principal_id,
    )
    await executor._store.save_execution(
        record=stranded,
        parameters={"request": {"request_id": "stranded", "profile_id": "prof-ext-idem", "action_type": "FETCH"}},
        tenant_id=_TENANT,
        outbox_store=executor._outbox_store,
    )

    before = await executor.get_execution(stranded_id, tenant_id=_TENANT)
    assert before is not None
    assert before.status == ExternalExecutionStatus.RUNNING

    recovered = await executor.recover_stranded_executions(tenant_id=_TENANT)
    assert any(r.id == stranded_id for r in recovered)

    after = await executor.get_execution(stranded_id, tenant_id=_TENANT)
    assert after is not None
    assert after.status == ExternalExecutionStatus.FAILED
    assert after.error is not None and "interrupted" in after.error.lower()
