"""
KORTEX OS — Milestone M5.4 Test Suite
Governed External Execution, Timeouts, Retries, Human Approvals, Tenancy & Outbox Verification.
"""

from __future__ import annotations

import asyncio
import http
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.errors import map_exception
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.core.outbox import OutboxStore
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import (
    PrincipalRecord,
    PrincipalType,
    RolePermissionRecord,
    SecurityPrincipal,
)
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import (
    ExternalExecutionError,
    ExternalExecutionTimeoutError,
)
from kortex.engines.workflow.models import (
    ExternalExecutionRequest,
    ExternalExecutionStatus,
    RetryPolicy,
)

_TEST_MASTER_KEY = b"\xaa" * 32
_TEST_SIGNING_KEY = b"\xbb" * 32


@pytest.fixture
async def kernel(tmp_path: Path) -> AsyncGenerator[Kernel, None]:
    db_file = tmp_path / f"test_ext_{uuid4().hex[:8]}.db"
    sqlite_url = f"sqlite+aiosqlite:///{db_file}"
    db_manager = DatabaseEngineManager(connection_url=sqlite_url)
    await db_manager.connect()
    await db_manager.create_all_tables()



    k = Kernel()
    k._db_manager = db_manager

    storage_dir = tmp_path / f"storage_ext_{uuid4().hex[:8]}"
    storage_engine = StorageEngine(base_directory=str(storage_dir))
    security_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_SIGNING_KEY,
    )
    workflow_engine = WorkflowEngine()

    k.register_engine(storage_engine)
    k.register_engine(security_engine)
    k.register_engine(workflow_engine)

    await k.boot()

    yield k

    await k.shutdown()
    await db_manager.disconnect()



@pytest.mark.asyncio
async def test_external_execution_happy_path(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = wf_engine.external_executor
    assert executor is not None

    async def mock_handler(target_url: str) -> dict[str, Any]:
        return {"status": "SUCCESS", "url": target_url, "data": 42}

    req = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="HTTP_CALL",
        target="https://api.example.com/data",
        parameters={"target_url": "https://api.example.com/data", "_handler": mock_handler},
        timeout_seconds=5.0,
    )

    record = await executor.execute_operation(req)
    assert record.status == ExternalExecutionStatus.COMPLETED
    assert record.output == {"status": "SUCCESS", "url": "https://api.example.com/data", "data": 42}
    assert record.attempts == 1
    assert record.execution_time_ms >= 0


@pytest.mark.asyncio
async def test_external_execution_timeout(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = wf_engine.external_executor
    assert executor is not None

    async def slow_handler() -> str:
        await asyncio.sleep(2.0)
        return "done"

    req = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="HTTP_CALL",
        target="https://slow.service.com",
        parameters={"_handler": slow_handler},
        timeout_seconds=0.1,  # Short timeout
    )

    with pytest.raises(ExternalExecutionTimeoutError, match="timed out"):
        await executor.execute_operation(req)

    # Verify persisted status is TIMED_OUT
    saved = await executor.get_execution(req.id, tenant_id="tenant_alpha")
    assert saved is not None
    assert saved.status == ExternalExecutionStatus.TIMED_OUT
    assert "timed out" in (saved.error or "").lower()


@pytest.mark.asyncio
async def test_external_execution_timeout_participates_in_retry_policy(kernel: Kernel) -> None:
    """M5-A5 regression: a timeout on one attempt must still consume only
    one attempt of a configured retry budget and be retried on the next,
    exactly like any other transient failure — not fail permanently on the
    very first timeout regardless of `max_attempts`. The first call sleeps
    past the timeout; the second returns immediately."""
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = wf_engine.external_executor
    assert executor is not None

    call_count = 0

    async def sometimes_slow_handler() -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await asyncio.sleep(1.0)  # exceeds the 0.1s timeout below
        return "eventually_fast"

    req = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="HTTP_CALL",
        target="https://sometimes-slow.service.com",
        parameters={"_handler": sometimes_slow_handler},
        timeout_seconds=0.1,
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0.01, backoff_factor=1.0),
    )

    record = await executor.execute_operation(req)
    assert record.status == ExternalExecutionStatus.COMPLETED
    assert record.output == "eventually_fast"
    assert record.attempts == 2
    assert call_count == 2


@pytest.mark.asyncio
async def test_external_execution_all_attempts_timing_out_reports_timed_out(kernel: Kernel) -> None:
    """M5-A5 regression: when EVERY attempt times out, the final persisted
    status/exception must still be TIMED_OUT (not a generic FAILED) so an
    operator can distinguish "the target never responded" from "the target
    responded with an error" — even though multiple attempts were made."""
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = wf_engine.external_executor
    assert executor is not None

    async def always_slow_handler() -> str:
        await asyncio.sleep(1.0)
        return "never_gets_here"

    req = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="HTTP_CALL",
        target="https://always-slow.service.com",
        parameters={"_handler": always_slow_handler},
        timeout_seconds=0.1,
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0.01, backoff_factor=1.0),
    )

    with pytest.raises(ExternalExecutionTimeoutError, match="timed out after 2 attempt"):
        await executor.execute_operation(req)

    saved = await executor.get_execution(req.id, tenant_id="tenant_alpha")
    assert saved is not None
    assert saved.status == ExternalExecutionStatus.TIMED_OUT
    assert saved.attempts == 2


@pytest.mark.asyncio
async def test_external_execution_retries_with_exponential_backoff(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = wf_engine.external_executor
    assert executor is not None

    call_count = 0

    async def flaky_handler() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionResetError("Connection reset by peer")
        return "recovered"

    req = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="CONNECTOR",
        target="connector.flaky",
        parameters={"_handler": flaky_handler},
        timeout_seconds=5.0,
        retry_policy=RetryPolicy(max_attempts=4, initial_delay_seconds=0.01, backoff_factor=1.5),
    )

    record = await executor.execute_operation(req)
    assert record.status == ExternalExecutionStatus.COMPLETED
    assert record.output == "recovered"
    assert record.attempts == 3
    assert call_count == 3


@pytest.mark.asyncio
async def test_external_execution_exhausted_retries_fails(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = wf_engine.external_executor
    assert executor is not None

    async def failing_handler() -> None:
        raise ValueError("Invalid remote payload")

    req = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="HTTP",
        target="https://broken.service.com",
        parameters={"_handler": failing_handler},
        timeout_seconds=5.0,
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0.01),
    )

    with pytest.raises(ExternalExecutionError, match="failed after 2 attempts"):
        await executor.execute_operation(req)

    saved = await executor.get_execution(req.id, tenant_id="tenant_alpha")
    assert saved is not None
    assert saved.status == ExternalExecutionStatus.FAILED
    assert saved.attempts == 2


@pytest.mark.asyncio
async def test_external_execution_requires_approval_gates(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = wf_engine.external_executor
    assert executor is not None

    req = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="DATABASE_MIGRATION",
        target="db.execute_drop_table",
        parameters={"table": "legacy_users"},
        requires_approval=True,
        required_approval_role="DB_ADMIN",
    )

    record = await executor.execute_operation(req)
    assert record.status == ExternalExecutionStatus.WAITING_APPROVAL
    assert record.approval_request_id is not None

    # Ticket should be created in DurableApprovalManager
    ticket = await wf_engine.approval_manager.get_request(
        record.approval_request_id, tenant_id="tenant_alpha"
    )
    assert ticket is not None
    assert ticket.required_role == "DB_ADMIN"


@pytest.mark.asyncio
async def test_external_execution_tenant_isolation(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = wf_engine.external_executor
    assert executor is not None

    req_a = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="CAPABILITY",
        target="cap.alpha",
    )
    req_b = ExternalExecutionRequest(
        tenant_id="tenant_beta",
        operation_type="CAPABILITY",
        target="cap.beta",
    )

    rec_a = await executor.execute_operation(req_a)
    rec_b = await executor.execute_operation(req_b)

    # Listing isolation
    list_a = await executor.list_executions(tenant_id="tenant_alpha")
    assert len(list_a) == 1
    assert list_a[0].id == rec_a.id

    list_b = await executor.list_executions(tenant_id="tenant_beta")
    assert len(list_b) == 1
    assert list_b[0].id == rec_b.id

    # Get isolation
    assert await executor.get_execution(rec_a.id, tenant_id="tenant_beta") is None
    assert await executor.get_execution(rec_b.id, tenant_id="tenant_alpha") is None


@pytest.mark.asyncio
async def test_external_execution_cancellation(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = wf_engine.external_executor
    assert executor is not None

    req = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="LONG_OP",
        target="op.batch_export",
        requires_approval=True,
    )
    rec = await executor.execute_operation(req)
    assert rec.status == ExternalExecutionStatus.WAITING_APPROVAL

    cancelled = await executor.cancel_execution(rec.id, tenant_id="tenant_alpha")
    assert cancelled.status == ExternalExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_external_execution_transactional_outbox_integration(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    executor = wf_engine.external_executor
    storage: StorageEngine = kernel.get_engine("storage")
    outbox_store = OutboxStore(storage.data)
    assert executor is not None

    req = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="HTTP_CALL",
        target="https://api.outbox.test/ping",
        parameters={"_handler": lambda: "pong"},
    )
    await executor.execute_operation(req)

    # Verify outbox event staged
    events = await outbox_store.get_pending_events(limit=10)
    topics = [e.topic for e in events]
    assert "workflow.external.started" in topics
    assert "workflow.external.completed" in topics


@pytest.mark.asyncio
async def test_external_execution_audit_lineage(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    security_engine: SecurityEngine = kernel.get_engine("security")
    executor = wf_engine.external_executor
    assert executor is not None

    principal = SecurityPrincipal(
        principal_id="user_ext_operator",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_alpha",
        roles=["operator"],
        permissions=["workflow:execute"],
    )

    req = ExternalExecutionRequest(
        tenant_id="tenant_alpha",
        operation_type="HTTP",
        target="https://api.audit.test",
        parameters={"_handler": lambda: "audited"},
    )

    record = await executor.execute_operation(req, principal=principal)

    entries = await security_engine.audit_manager.get_audit_entries(
        tenant_id="tenant_alpha", action="kortex.workflow.external.completed"
    )
    assert len(entries) >= 1
    assert entries[0].actor_id == "user_ext_operator"
    assert entries[0].resource_id == str(record.id)


@pytest.mark.asyncio
async def test_external_execution_capability_dispatch_flow(kernel: Kernel) -> None:
    storage: StorageEngine = kernel.get_engine("storage")
    security_engine: SecurityEngine = kernel.get_engine("security")
    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role="EXT_DISPATCH_ROLE", permission="workflow:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role="EXT_DISPATCH_ROLE", permission="workflow:read"))
        session.add(RolePermissionRecord(id=str(uuid4()), role="EXT_DISPATCH_ROLE", permission="workflow:cancel"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id="tenant_alpha",
                principal_id="user_ext_cap",
                principal_type="USER",
                credential_hash=hasher.hash("pass123"),
                roles=["EXT_DISPATCH_ROLE"],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )



    await storage.data.execute_in_transaction(_seed_rbac)

    p_auth = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": "tenant_alpha",
            "principal_id": "user_ext_cap",
            "password": "pass123",
        }
    )
    token = await security_engine.authentication_manager.issue_token(p_auth)

    # 1. Execute external operation via capability
    req_exec = CapabilityRequest(
        capability_name="kortex.workflow.external.execute",
        session_token=token,
        parameters={
            "target": "https://api.capability.test",
            "operation_type": "HTTP",
            "parameters": {"_handler": lambda: {"data": "cap_ok"}},
            "tenant_id": "tenant_alpha",
        },
        context={"resource_tenant_id": "tenant_alpha"},
    )
    res_exec = await kernel.invoke_capability(req_exec)
    assert res_exec["status"] == "COMPLETED"
    exec_id = res_exec["id"]

    # 2. Get external execution via capability
    req_get = CapabilityRequest(
        capability_name="kortex.workflow.external.get",
        session_token=token,
        parameters={"execution_id": exec_id, "tenant_id": "tenant_alpha"},
        context={"resource_tenant_id": "tenant_alpha"},
    )
    res_get = await kernel.invoke_capability(req_get)
    assert res_get["id"] == exec_id
    assert res_get["target"] == "https://api.capability.test"

    # 3. List external executions via capability
    req_list = CapabilityRequest(
        capability_name="kortex.workflow.external.list",
        session_token=token,
        parameters={"tenant_id": "tenant_alpha"},
        context={"resource_tenant_id": "tenant_alpha"},
    )
    res_list = await kernel.invoke_capability(req_list)
    assert any(r["id"] == exec_id for r in res_list)




def test_external_execution_api_error_mapping() -> None:
    mapping_timeout = map_exception(ExternalExecutionTimeoutError("Operation timed out"))
    assert mapping_timeout.category == "TIMEOUT_EXCEEDED"
    assert mapping_timeout.http_status == http.HTTPStatus.REQUEST_TIMEOUT

    mapping_error = map_exception(ExternalExecutionError("Operation failed"))
    assert mapping_error.category == "EXECUTION_FAILED"
    assert mapping_error.http_status == http.HTTPStatus.BAD_REQUEST
