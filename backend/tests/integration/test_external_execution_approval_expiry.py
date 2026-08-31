"""M6.4-3 regression suite: EXPIRED decision handling for external executions.

`ExternalExecutionManager.on_approval_decided` (M6.3-3) already treats any
`decision != "APPROVED"` as cancellation -- no special-cased
`if decision == "EXPIRED"` branch exists or is needed. This suite proves
that existing, unmodified logic is correct for the EXPIRED case
specifically (never resumes, never reaches the connector, tolerates
duplicate delivery, fails closed on tenant/identifier mismatches), and
proves the M6.4-3 audit enrichment: the cancellation audit context now
records the actual decision ("REJECTED" vs "EXPIRED") rather than a single
undifferentiated "cancelled".
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
from kortex.engines.event.engine import Event
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import ExternalExecutionRequest, ExternalExecutionStatus

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32
_ROLE = "EXT_EXEC_EXPIRY_TEST_ROLE"
_TENANT = "tenant_ext_exec_expiry"
_OTHER_TENANT = "tenant_ext_exec_expiry_other"


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_ext_expiry_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_ext_expiry_{uuid4().hex[:8]}"))
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
                principal_id="user_expiry_requester",
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
    await security_engine.put_secret("vault:expiry-secret", _TENANT, "real-secret-value")
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="prof-ext-expiry",
            tenant_id=_TENANT,
            name="External Execution Expiry Profile",
            driver_id="connector-dummy",
            secret_handle="vault:expiry-secret",
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


async def _submit_paused_execution(kernel: Kernel, requester_principal, approval_timeout_seconds=None):  # noqa: ANN001
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    request = ExternalExecutionRequest(
        tenant_id=_TENANT,
        operation_type="CAPABILITY",
        target="kortex.connector.action.execute",
        parameters={
            "request": ActionRequest(
                request_id=f"ext-expiry-{uuid4()}",
                profile_id="prof-ext-expiry",
                action_type=ConnectorActionType.FETCH,
            )
        },
        timeout_seconds=10.0,
        requires_approval=True,
        required_approval_role=_ROLE,
        approval_timeout_seconds=approval_timeout_seconds,
        created_by=requester_principal.principal_id,
    )
    record = await executor.execute_operation(request, principal=requester_principal)
    return executor, record


@pytest.mark.asyncio
async def test_real_expiry_cancels_execution_with_no_connector_call_and_audit_reason(kernel_env: Kernel) -> None:
    """End-to-end: a genuinely short-lived ticket, swept for real, cancels
    the execution -- never reaching the connector -- and the resulting
    audit entry's context records reason="EXPIRED" (M6.4-3 enrichment),
    distinguishable from a REJECTED cancellation without a table join."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_expiry_requester")
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")

    executor, record = await _submit_paused_execution(kernel, requester, approval_timeout_seconds=1)
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL

    # Let real wall-clock time pass the 1-second approval timeout.
    await asyncio.sleep(1.2)

    # Manual sweep call here is deliberate and fine -- M6.4-2 already proved
    # production reachability end-to-end without one; this suite is testing
    # the downstream handler's own adversarial correctness, not the trigger.
    await workflow_engine.sweep_expired_approvals(tenant_id=_TENANT)

    final = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert final is not None
    assert final.status == ExternalExecutionStatus.CANCELLED
    assert final.output is None

    security_engine: SecurityEngine = kernel.get_engine("security")
    entries = await security_engine.audit_manager.get_audit_entries(
        tenant_id=_TENANT, action="kortex.workflow.external.cancelled"
    )
    matching = [e for e in entries if e.resource_id == str(record.id)]
    assert len(matching) == 1
    assert matching[0].context.get("reason") == "EXPIRED"


@pytest.mark.asyncio
async def test_expired_event_never_resumes_execution(kernel_env: Kernel) -> None:
    """Adversarial: an EXPIRED decision must never take the resume path."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_expiry_requester")
    executor, record = await _submit_paused_execution(kernel, requester)

    forged_event = Event(
        topic="workflow.approval.decided",
        payload={
            "request_id": str(record.approval_request_id),
            "tenant_id": _TENANT,
            "decision": "EXPIRED",
            "context_snapshot": {"action": "external_execution", "execution_id": str(record.id), "target": record.target},
        },
    )
    await executor.on_approval_decided(forged_event)

    final = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert final is not None
    assert final.status == ExternalExecutionStatus.CANCELLED
    assert final.status != ExternalExecutionStatus.COMPLETED
    assert final.status != ExternalExecutionStatus.RUNNING
    assert final.output is None


@pytest.mark.asyncio
async def test_duplicate_expired_event_delivered_twice_cancels_exactly_once(kernel_env: Kernel) -> None:
    kernel = kernel_env
    requester = await _principal(kernel, "user_expiry_requester")
    executor, record = await _submit_paused_execution(kernel, requester)

    event = Event(
        topic="workflow.approval.decided",
        payload={
            "request_id": str(record.approval_request_id),
            "tenant_id": _TENANT,
            "decision": "EXPIRED",
            "context_snapshot": {"action": "external_execution", "execution_id": str(record.id), "target": record.target},
        },
    )
    await executor.on_approval_decided(event)
    first = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert first is not None
    assert first.status == ExternalExecutionStatus.CANCELLED

    await executor.on_approval_decided(event)
    second = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert second is not None
    assert second.status == ExternalExecutionStatus.CANCELLED
    assert second.completed_at == first.completed_at


@pytest.mark.asyncio
async def test_expired_event_with_wrong_tenant_fails_closed(kernel_env: Kernel) -> None:
    """Adversarial: a forged/wrong tenant_id in the event payload must not
    cross the tenant boundary to cancel another tenant's execution."""
    kernel = kernel_env
    requester = await _principal(kernel, "user_expiry_requester")
    executor, record = await _submit_paused_execution(kernel, requester)

    forged_event = Event(
        topic="workflow.approval.decided",
        payload={
            "request_id": str(record.approval_request_id),
            "tenant_id": _OTHER_TENANT,
            "decision": "EXPIRED",
            "context_snapshot": {"action": "external_execution", "execution_id": str(record.id), "target": record.target},
        },
    )
    await executor.on_approval_decided(forged_event)

    untouched = await executor.get_execution(record.id, tenant_id=_TENANT)
    assert untouched is not None
    assert untouched.status == ExternalExecutionStatus.WAITING_APPROVAL


@pytest.mark.asyncio
async def test_forged_expired_event_for_nonexistent_execution_is_a_safe_noop(kernel_env: Kernel) -> None:
    """Adversarial: a forged EXPIRED event referencing an execution_id that
    doesn't exist must be a safe no-op, not an error or a crash."""
    kernel = kernel_env
    workflow_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = workflow_engine.external_executor
    assert executor is not None

    forged_event = Event(
        topic="workflow.approval.decided",
        payload={
            "request_id": str(uuid4()),
            "tenant_id": _TENANT,
            "decision": "EXPIRED",
            "context_snapshot": {
                "action": "external_execution",
                "execution_id": str(uuid4()),
                "target": "kortex.connector.action.execute",
            },
        },
    )
    # Must not raise.
    await executor.on_approval_decided(forged_event)
