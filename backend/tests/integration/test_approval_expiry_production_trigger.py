"""M6.4-2 regression suite: a real, running production trigger for approval expiry.

Prior to M6.4-2, `DurableApprovalManager.sweep_expired_requests()` (and its
`WorkflowEngine.sweep_expired_approvals()` wrapper) were fully implemented
and unit-tested, but had ZERO production callers anywhere in the repository
-- their only callers in the entire codebase were unit tests that invoked
them directly. A ticket could time out and simply sit as PENDING forever;
nothing in a running KORTEX server would ever notice.

This suite proves the fix does NOT require calling `sweep_expired_requests`/
`sweep_expired_approvals` manually. It boots a real Kernel with a real
WorkflowEngine whose background approval-expiry sweep daemon
(`WorkflowEngine._approval_sweep_loop`, wired into `start()`) is enabled
with a fast poll interval, creates a genuinely short-lived approval ticket
through the real `ExternalExecutionManager.execute_operation` path, lets
real wall-clock time pass, and simply WAITS -- proving the ticket reaches
EXPIRED and the paused execution is cancelled purely because the server
kept running, with no test code ever touching the sweep method directly.
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
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.models import ActionRequest, ConnectorActionType, ConnectorProfile
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import ExternalExecutionRequest, ExternalExecutionStatus, WorkflowSettings

_TEST_MASTER_KEY = b"\x99" * 32
_TEST_SIGNING_KEY = b"\xaa" * 32
_ROLE = "EXT_EXEC_TRIGGER_TEST_ROLE"
_TENANT = "tenant_ext_exec_trigger"


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_trigger_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_trigger_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    # M6.4-2: a fast sweep interval so this test doesn't need to wait
    # anywhere near real-world approval-timeout durations -- the daemon
    # itself is unmodified production code, just configured for a quick tick.
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
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="workflow:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="workflow:read"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:execute"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT,
                principal_id="user_trigger_requester",
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
    await security_engine.put_secret("vault:trigger-secret", _TENANT, "real-secret-value")
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="prof-trigger",
            tenant_id=_TENANT,
            name="Production Trigger Test Profile",
            driver_id="connector-dummy",
            secret_handle="vault:trigger-secret",
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


@pytest.mark.asyncio
async def test_expired_approval_propagates_via_real_background_daemon_with_no_manual_sweep_call(
    kernel_env: Kernel,
) -> None:
    """The full, mandatory M6.4-2 production-reachability proof.

    Explicitly never calls `sweep_expired_requests` or
    `sweep_expired_approvals` -- only `execute_operation` (to create the
    paused execution) and plain `asyncio.sleep` (to let real time pass and
    let the already-running background daemon do its job).
    """
    kernel = kernel_env
    requester = await _principal(kernel, "user_trigger_requester")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    record = await executor.execute_operation(
        ExternalExecutionRequest(
            tenant_id=_TENANT,
            operation_type="CAPABILITY",
            target="kortex.connector.action.execute",
            parameters={
                "request": ActionRequest(
                    request_id=f"ext-trigger-{uuid4()}",
                    profile_id="prof-trigger",
                    action_type=ConnectorActionType.FETCH,
                )
            },
            timeout_seconds=10.0,
            requires_approval=True,
            required_approval_role=_ROLE,
            # A genuinely short-lived approval -- eligible for expiry
            # almost immediately, so the real background daemon (not this
            # test) is what actually transitions and propagates it.
            approval_timeout_seconds=1,
            created_by=requester.principal_id,
        ),
        principal=requester,
    )
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL
    ticket_id = record.approval_request_id
    assert ticket_id is not None

    ticket_before = await workflow_engine.approval_manager.get_request(ticket_id, tenant_id=_TENANT)
    assert ticket_before is not None
    assert ticket_before.state.value == "PENDING"

    # Wait for real wall-clock time to pass the 1-second approval timeout,
    # plus enough sweep-daemon ticks (0.5s interval) to guarantee at least
    # one real tick has fired and completed since expiry became eligible.
    await asyncio.sleep(3.0)

    ticket_after = await workflow_engine.approval_manager.get_request(ticket_id, tenant_id=_TENANT)
    assert ticket_after is not None
    assert ticket_after.state.value == "EXPIRED"

    final_execution = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert final_execution is not None
    assert final_execution.status == ExternalExecutionStatus.CANCELLED
    # No external side effect: the operation was never dispatched.
    assert final_execution.output is None
