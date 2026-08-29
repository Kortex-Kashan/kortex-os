"""
KORTEX OS — Milestone M5.4 Test Suite
Durable Workflow Scheduling, Cron/Interval Execution, Missed-Run Recovery, Tenancy & Outbox Verification.
"""

from __future__ import annotations

import asyncio
import http
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    ScheduleConflictError,
    ScheduleNotFoundError,
    WorkflowScheduleError,
)
from kortex.engines.workflow.models import (
    ScheduleStatus,
    ScheduleType,
    WorkflowDefinition,
    WorkflowStep,
)

_TEST_MASTER_KEY = b"\xaa" * 32
_TEST_SIGNING_KEY = b"\xbb" * 32


@pytest.fixture
async def kernel(tmp_path: Path) -> AsyncGenerator[Kernel, None]:
    db_file = tmp_path / f"test_sched_{uuid4().hex[:8]}.db"
    sqlite_url = f"sqlite+aiosqlite:///{db_file}"
    db_manager = DatabaseEngineManager(connection_url=sqlite_url)
    await db_manager.connect()
    await db_manager.create_all_tables()



    k = Kernel()
    k._db_manager = db_manager

    storage_dir = tmp_path / f"storage_sched_{uuid4().hex[:8]}"
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

    # Pre-create test definitions
    dummy_wf = WorkflowDefinition(
        id="test_scheduled_def",
        name="Scheduled Test Workflow",
        version="1.0.0",
        steps=[WorkflowStep(id="step_1", name="Step 1", capability_name=None)],
    )

    workflow_engine.register_definition(dummy_wf)

    yield k

    await k.shutdown()
    await db_manager.disconnect()




@pytest.mark.asyncio
async def test_schedule_creation_cron(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    sch = await scheduler.create_schedule(
        name="nightly_backup",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.CRON,
        cron_expression="0 2 * * *",
        tenant_id="tenant_alpha",
    )
    assert sch.name == "nightly_backup"
    assert sch.schedule_type == ScheduleType.CRON
    assert sch.cron_expression == "0 2 * * *"
    assert sch.status == ScheduleStatus.ACTIVE
    assert sch.next_run_at is not None
    assert sch.next_run_at.hour == 2
    assert sch.next_run_at.minute == 0


@pytest.mark.asyncio
async def test_schedule_creation_interval(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    sch = await scheduler.create_schedule(
        name="heartbeat_every_10m",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=600,
        tenant_id="tenant_alpha",
    )
    assert sch.name == "heartbeat_every_10m"
    assert sch.schedule_type == ScheduleType.INTERVAL
    assert sch.interval_seconds == 600
    assert sch.status == ScheduleStatus.ACTIVE
    assert sch.next_run_at is not None


@pytest.mark.asyncio
async def test_schedule_creation_once(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    target_time = datetime.now(UTC) + timedelta(hours=3)
    sch = await scheduler.create_schedule(
        name="one_off_migration",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.ONCE,
        run_at=target_time,
        tenant_id="tenant_alpha",
    )
    assert sch.name == "one_off_migration"
    assert sch.schedule_type == ScheduleType.ONCE
    assert sch.next_run_at == target_time


@pytest.mark.asyncio
async def test_schedule_unique_name_constraint_under_tenant(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    await scheduler.create_schedule(
        name="unique_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=300,
        tenant_id="tenant_alpha",
    )

    with pytest.raises(ScheduleConflictError):
        await scheduler.create_schedule(
            name="unique_task",
            definition_id="test_scheduled_def",
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=600,
            tenant_id="tenant_alpha",
        )


@pytest.mark.asyncio
async def test_schedule_same_name_distinct_tenants(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    sch1 = await scheduler.create_schedule(
        name="shared_name_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=300,
        tenant_id="tenant_alpha",
    )
    sch2 = await scheduler.create_schedule(
        name="shared_name_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=300,
        tenant_id="tenant_beta",
    )
    assert sch1.id != sch2.id
    assert sch1.tenant_id == "tenant_alpha"
    assert sch2.tenant_id == "tenant_beta"


@pytest.mark.asyncio
async def test_schedule_tenant_isolated_listing_and_retrieval(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    sch_a = await scheduler.create_schedule(
        name="task_a",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        tenant_id="tenant_alpha",
    )
    sch_b = await scheduler.create_schedule(
        name="task_b",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        tenant_id="tenant_beta",
    )

    # Listing isolation
    list_a = await scheduler.list_schedules(tenant_id="tenant_alpha")
    assert len(list_a) == 1
    assert list_a[0].id == sch_a.id

    list_b = await scheduler.list_schedules(tenant_id="tenant_beta")
    assert len(list_b) == 1
    assert list_b[0].id == sch_b.id

    # Cross-tenant get isolation
    assert await scheduler.get_schedule(sch_a.id, tenant_id="tenant_beta") is None
    assert await scheduler.get_schedule(sch_b.id, tenant_id="tenant_alpha") is None


@pytest.mark.asyncio
async def test_schedule_pause_and_resume(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    sch = await scheduler.create_schedule(
        name="pausable_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=120,
        tenant_id="tenant_alpha",
    )

    paused = await scheduler.pause_schedule(sch.id, tenant_id="tenant_alpha")
    assert paused.status == ScheduleStatus.PAUSED

    resumed = await scheduler.resume_schedule(sch.id, tenant_id="tenant_alpha")
    assert resumed.status == ScheduleStatus.ACTIVE
    assert resumed.next_run_at is not None


@pytest.mark.asyncio
async def test_schedule_cancel(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    sch = await scheduler.create_schedule(
        name="cancellable_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=120,
        tenant_id="tenant_alpha",
    )

    cancelled = await scheduler.cancel_schedule(sch.id, tenant_id="tenant_alpha")
    assert cancelled.status == ScheduleStatus.DISABLED


@pytest.mark.asyncio
async def test_schedule_atomic_tick_execution(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    # Create schedule due in the past
    sch = await scheduler.create_schedule(
        name="due_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        tenant_id="tenant_alpha",
    )

    # Perform tick with future timestamp
    future_time = datetime.now(UTC) + timedelta(seconds=120)
    triggered = await scheduler.tick(now_dt=future_time, tenant_id="tenant_alpha")

    assert len(triggered) == 1
    t_sch = triggered[0]
    assert t_sch.id == sch.id
    assert t_sch.run_count == 1
    assert t_sch.last_run_at is not None
    assert t_sch.last_instance_id is not None
    assert t_sch.next_run_at is not None
    assert t_sch.next_run_at > future_time


@pytest.mark.asyncio
async def test_schedule_max_runs_termination(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    sch = await scheduler.create_schedule(
        name="limited_runs_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=10,
        max_runs=2,
        tenant_id="tenant_alpha",
    )
    assert sch.max_runs == 2

    # Tick 1

    now1 = datetime.now(UTC) + timedelta(seconds=20)
    trig1 = await scheduler.tick(now_dt=now1, tenant_id="tenant_alpha")
    await asyncio.sleep(0.05)
    assert len(trig1) == 1
    assert trig1[0].run_count == 1
    assert trig1[0].status == ScheduleStatus.ACTIVE

    # Tick 2
    now2 = now1 + timedelta(seconds=20)
    trig2 = await scheduler.tick(now_dt=now2, tenant_id="tenant_alpha")
    await asyncio.sleep(0.05)
    assert len(trig2) == 1
    assert trig2[0].run_count == 2
    assert trig2[0].status == ScheduleStatus.COMPLETED
    assert trig2[0].next_run_at is None

    # Tick 3 should NOT trigger
    now3 = now2 + timedelta(seconds=20)
    trig3 = await scheduler.tick(now_dt=now3, tenant_id="tenant_alpha")
    assert len(trig3) == 0


@pytest.mark.asyncio
async def test_schedule_manual_trigger(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    sch = await scheduler.create_schedule(
        name="manual_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=3600,
        tenant_id="tenant_alpha",
    )

    instance = await scheduler.trigger_schedule(sch.id, tenant_id="tenant_alpha")
    await asyncio.sleep(0.05)
    assert instance is not None
    assert instance.definition_id == "test_scheduled_def"

    updated = await scheduler.get_schedule(sch.id, tenant_id="tenant_alpha")
    assert updated is not None
    assert updated.run_count == 1
    assert updated.last_instance_id == instance.id


@pytest.mark.asyncio
async def test_schedule_offline_catchup_recovery(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    # Create schedule with next_run in the past
    sch = await scheduler.create_schedule(
        name="missed_offline_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        tenant_id="tenant_alpha",
    )

    # Force next_run_at to past
    past_time = datetime.now(UTC) - timedelta(hours=2)
    sch.next_run_at = past_time
    await scheduler._store.save_schedule(sch, tenant_id="tenant_alpha")

    # Run hydration recovery
    recovered = await scheduler.hydrate_and_recover_schedules(tenant_id="tenant_alpha")
    await asyncio.sleep(0.05)
    assert len(recovered) == 1
    assert recovered[0].id == sch.id
    assert recovered[0].run_count == 1
    assert recovered[0].next_run_at > datetime.now(UTC)



@pytest.mark.asyncio
async def test_schedule_transactional_outbox_integration(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    storage: StorageEngine = kernel.get_engine("storage")
    outbox_store = OutboxStore(storage.data)

    sch = await scheduler.create_schedule(
        name="outbox_scheduled_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        tenant_id="tenant_alpha",
    )
    assert sch.name == "outbox_scheduled_task"

    # Verify outbox event staged

    events = await outbox_store.get_pending_events(limit=10)
    topics = [e.topic for e in events]
    assert "workflow.schedule.created" in topics


@pytest.mark.asyncio
async def test_schedule_audit_lineage(kernel: Kernel) -> None:
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    security_engine: SecurityEngine = kernel.get_engine("security")
    scheduler = wf_engine.scheduler
    assert scheduler is not None

    principal = SecurityPrincipal(
        principal_id="user_admin",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_alpha",
        roles=["admin"],
        permissions=["workflow:schedule"],
    )

    sch = await scheduler.create_schedule(
        name="audited_task",
        definition_id="test_scheduled_def",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        tenant_id="tenant_alpha",
        principal=principal,
    )

    entries = await security_engine.audit_manager.get_audit_entries(
        tenant_id="tenant_alpha", action="kortex.workflow.schedule.create"
    )
    assert len(entries) >= 1
    assert entries[0].actor_id == "user_admin"
    assert entries[0].resource_id == str(sch.id)


@pytest.mark.asyncio
async def test_schedule_capability_dispatch_flow(kernel: Kernel) -> None:
    storage: StorageEngine = kernel.get_engine("storage")
    security_engine: SecurityEngine = kernel.get_engine("security")
    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        r1 = RolePermissionRecord(id=str(uuid4()), role="SCHED_DISPATCH_ROLE", permission="workflow:schedule")
        r2 = RolePermissionRecord(id=str(uuid4()), role="SCHED_DISPATCH_ROLE", permission="workflow:read")
        r3 = RolePermissionRecord(id=str(uuid4()), role="SCHED_DISPATCH_ROLE", permission="workflow:start")
        p1 = PrincipalRecord(
            id=str(uuid4()),
            tenant_id="tenant_alpha",
            principal_id="user_sched_operator",
            principal_type="USER",
            credential_hash=hasher.hash("pass123"),
            roles=["SCHED_DISPATCH_ROLE"],
            attributes={"clearance_level": "RESTRICTED"},
        )
        session.add_all([r1, r2, r3, p1])
        await session.flush()




    await storage.data.execute_in_transaction(_seed_rbac)

    p_auth = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": "tenant_alpha",
            "principal_id": "user_sched_operator",
            "password": "pass123",
        }
    )
    token = await security_engine.authentication_manager.issue_token(p_auth)

    # 1. Create schedule via capability


    req_create = CapabilityRequest(
        capability_name="kortex.workflow.schedule.create",
        session_token=token,
        parameters={
            "name": "capability_scheduled_job",
            "definition_id": "test_scheduled_def",
            "schedule_type": "INTERVAL",
            "interval_seconds": 120,
            "tenant_id": "tenant_alpha",
        },
        context={"resource_tenant_id": "tenant_alpha"},
    )
    res_create = await kernel.invoke_capability(req_create)
    assert "id" in res_create
    sch_id = res_create["id"]

    # 2. Get schedule via capability
    req_get = CapabilityRequest(
        capability_name="kortex.workflow.schedule.get",
        session_token=token,
        parameters={"schedule_id": sch_id, "tenant_id": "tenant_alpha"},
        context={"resource_tenant_id": "tenant_alpha"},
    )
    res_get = await kernel.invoke_capability(req_get)
    assert res_get["name"] == "capability_scheduled_job"

    # 3. List schedules via capability
    req_list = CapabilityRequest(
        capability_name="kortex.workflow.schedule.list",
        session_token=token,
        parameters={"tenant_id": "tenant_alpha"},
        context={"resource_tenant_id": "tenant_alpha"},
    )
    res_list = await kernel.invoke_capability(req_list)
    assert any(s["id"] == sch_id for s in res_list)

    # 4. Trigger schedule via capability
    req_trig = CapabilityRequest(
        capability_name="kortex.workflow.schedule.trigger",
        session_token=token,
        parameters={"schedule_id": sch_id, "tenant_id": "tenant_alpha"},
        context={"resource_tenant_id": "tenant_alpha"},
    )
    res_trig = await kernel.invoke_capability(req_trig)
    assert "instance_id" in res_trig




def test_schedule_api_error_mapping() -> None:
    mapping_not_found = map_exception(ScheduleNotFoundError("Schedule not found"))
    assert mapping_not_found.category == "CAPABILITY_NOT_FOUND"
    assert mapping_not_found.http_status == http.HTTPStatus.NOT_FOUND

    mapping_conflict = map_exception(ScheduleConflictError("Schedule already exists"))
    assert mapping_conflict.category == "EXECUTION_FAILED"
    assert mapping_conflict.http_status == http.HTTPStatus.CONFLICT

    mapping_sched_err = map_exception(WorkflowScheduleError("Scheduler failed"))
    assert mapping_sched_err.category == "EXECUTION_FAILED"
    assert mapping_sched_err.http_status == http.HTTPStatus.BAD_REQUEST
