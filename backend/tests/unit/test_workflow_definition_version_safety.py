"""
KORTEX OS — Milestone F4 Test Suite
Workflow Definition Version Safety (D7/D8): every persisted-instance re-entry path must resolve
`instance.definition_id + instance.definition_version` to the EXACT immutable version it was
created against, never silently float to whatever is latest-published at re-entry time.

Covers the master-prompt-mandated regression scenarios A-F, plus one additional re-entry path
discovered during implementation: `WorkflowEngine._definitions`, the unversioned in-memory read-
through cache, which — left unfixed — could answer an exact-version request with stale or wrong
content, or have a versioned lookup poison it for later latest-published lookups.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import (
    ScheduleType,
    WorkflowInstance,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)
from kortex.engines.workflow.persistence import WorkflowStore
from kortex.engines.workflow.scheduler import DurableWorkflowScheduler

_TENANT = "f4_version_safety_tenant"


@pytest.fixture
async def test_store() -> AsyncGenerator[WorkflowStore, None]:
    db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    await db_manager.connect()
    await db_manager.create_all_tables()
    data_store = RelationalDataStore(db_manager)
    store = WorkflowStore(data_store)
    yield store
    if db_manager._engine:
        await db_manager._engine.dispose()


def _v1_steps() -> list[WorkflowStep]:
    return [WorkflowStep(id="s1", name="s1"), WorkflowStep(id="s2", name="s2"), WorkflowStep(id="s3", name="s3")]


def _v2_steps() -> list[WorkflowStep]:
    return [WorkflowStep(id="only", name="only")]


async def _wait_completed(engine: WorkflowEngine, instance_id: UUID) -> WorkflowInstance:
    for _ in range(60):
        inst = engine.get_instance(instance_id)
        if inst.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
            return inst
        await asyncio.sleep(0.05)
    return engine.get_instance(instance_id)


async def _publish_v1_then_v2(store: WorkflowStore, definition_id: str) -> None:
    """Seed a definition with v1 (3 steps) published, then a draft edit republished as v2 (1 step)
    — every scenario below distinguishes "ran under v1" vs "ran under v2" purely by the resulting
    `current_step_index` a completed no-op run reaches (3 vs 1), never by inspecting internals."""
    dv = await store.create_draft(definition_id, _TENANT, created_by="SYSTEM", name="v1", steps=_v1_steps())
    await store.publish_draft(definition_id, _TENANT, dv.lock_version, "1.0.0")
    updated = await store.update_draft(definition_id, _TENANT, dv.lock_version + 1, name="v2", steps=_v2_steps())
    await store.publish_draft(definition_id, _TENANT, updated.lock_version, "2.0.0")


# ============================================================================
# Scenario A — publish v1, start an instance, publish v2, resume: must stay on v1.
# ============================================================================


@pytest.mark.asyncio
async def test_scenario_a_resume_after_new_publish_stays_on_v1(test_store: WorkflowStore) -> None:
    engine = WorkflowEngine()
    engine.set_workflow_store(test_store)

    dv = await test_store.create_draft("scenario_a", _TENANT, created_by="SYSTEM", name="v1", steps=_v1_steps())
    await test_store.publish_draft("scenario_a", _TENANT, dv.lock_version, "1.0.0")

    instance = await engine.start_workflow("scenario_a", tenant_id=_TENANT)
    assert instance.definition_version == "1.0.0"

    # Simulate the instance having paused mid-flight (e.g. after step 1) rather than completing
    # instantly, so there is something meaningful left to resume.
    instance.current_step_index = 0
    instance.state = WorkflowState.RUNNING
    instance.status = WorkflowStatus.RUNNING
    await test_store.update_instance(instance)

    # Publish v2 (1 step) while the instance is paused.
    updated = await test_store.update_draft("scenario_a", _TENANT, dv.lock_version + 1, name="v2", steps=_v2_steps())
    await test_store.publish_draft("scenario_a", _TENANT, updated.lock_version, "2.0.0")

    await engine.resume_workflow(instance.id, tenant_id=_TENANT)
    completed = await _wait_completed(engine, instance.id)
    assert completed.state == WorkflowState.COMPLETED
    assert completed.current_step_index == 3  # v1's step count -- proves v1 executed, not v2


# ============================================================================
# Scenario B/C — schedule float-to-latest-at-creation, pinned-forever-after.
# ============================================================================


@pytest.mark.asyncio
async def test_scenario_b_schedule_trigger_pins_then_resume_preserves_v1(test_store: WorkflowStore) -> None:
    engine = WorkflowEngine()
    engine.set_workflow_store(test_store)
    scheduler = DurableWorkflowScheduler(data_store=test_store._data_store, workflow_engine=engine)
    engine.set_scheduler(scheduler)

    dv = await test_store.create_draft("scenario_b", _TENANT, created_by="SYSTEM", name="v1", steps=_v1_steps())
    sch = await scheduler.create_schedule(
        name="sched_b",
        definition_id="scenario_b",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=3600,
        tenant_id=_TENANT,
    )
    await test_store.publish_draft("scenario_b", _TENANT, dv.lock_version, "1.0.0")

    instance = await scheduler.trigger_schedule(sch.id, tenant_id=_TENANT)
    assert instance.definition_version == "1.0.0"  # D13: floats to latest-published AT trigger time

    # Simulate the schedule-created instance pausing mid-flight, then publish v2 before resuming it.
    instance.current_step_index = 0
    instance.state = WorkflowState.RUNNING
    instance.status = WorkflowStatus.RUNNING
    await test_store.update_instance(instance)

    updated = await test_store.update_draft("scenario_b", _TENANT, dv.lock_version + 1, name="v2", steps=_v2_steps())
    await test_store.publish_draft("scenario_b", _TENANT, updated.lock_version, "2.0.0")

    await engine.resume_workflow(instance.id, tenant_id=_TENANT)
    completed = await _wait_completed(engine, instance.id)
    assert completed.current_step_index == 3  # still v1 -- the instance never floats after creation


@pytest.mark.asyncio
async def test_scenario_c_schedule_trigger_after_v2_publish_creates_v2_instance(test_store: WorkflowStore) -> None:
    engine = WorkflowEngine()
    engine.set_workflow_store(test_store)
    scheduler = DurableWorkflowScheduler(data_store=test_store._data_store, workflow_engine=engine)
    engine.set_scheduler(scheduler)

    dv = await test_store.create_draft("scenario_c", _TENANT, created_by="SYSTEM", name="v1", steps=_v1_steps())
    sch = await scheduler.create_schedule(
        name="sched_c",
        definition_id="scenario_c",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=3600,
        tenant_id=_TENANT,
    )
    await test_store.publish_draft("scenario_c", _TENANT, dv.lock_version, "1.0.0")
    updated = await test_store.update_draft("scenario_c", _TENANT, dv.lock_version + 1, name="v2", steps=_v2_steps())
    await test_store.publish_draft("scenario_c", _TENANT, updated.lock_version, "2.0.0")

    instance = await scheduler.trigger_schedule(sch.id, tenant_id=_TENANT)
    assert instance.definition_version == "2.0.0"
    completed = await _wait_completed(engine, instance.id)
    assert completed.current_step_index == 1  # v2's step count


# ============================================================================
# Scenario D — approval-style pause/resume across a publish.
# ============================================================================


@pytest.mark.asyncio
async def test_scenario_d_paused_waiting_instance_resumes_on_original_version(test_store: WorkflowStore) -> None:
    """A WAITING (paused-for-approval) instance must resume against its own pinned version even
    after a newer version is published while it waits -- the same `resume_workflow` re-entry point
    a real approval decision ultimately calls (`_advance_workflow_after_approval` -> `resume_workflow`).
    """
    engine = WorkflowEngine()
    engine.set_workflow_store(test_store)
    await _publish_v1_then_v2(test_store, "scenario_d")

    paused = WorkflowInstance(
        definition_id="scenario_d",
        definition_version="1.0.0",  # pinned at creation time, before v2 existed
        tenant_id=_TENANT,
        current_step_index=0,
        current_step_id="s1",
        state=WorkflowState.WAITING,
        status=WorkflowStatus.WAITING_APPROVAL,
    )
    v1_def = await test_store.get_definition("scenario_d", tenant_id=_TENANT, version="1.0.0")
    assert v1_def is not None
    await test_store.save_instance(paused, definition=v1_def, tenant_id=_TENANT)

    await engine.resume_workflow(paused.id, tenant_id=_TENANT)
    completed = await _wait_completed(engine, paused.id)
    assert completed.current_step_index == 3  # v1's step count, despite v2 already being published


# ============================================================================
# Scenario E — process restart / crash recovery re-entry.
# ============================================================================


@pytest.mark.asyncio
async def test_scenario_e_restart_recovery_preserves_exact_version(test_store: WorkflowStore) -> None:
    await _publish_v1_then_v2(test_store, "scenario_e")

    interrupted = WorkflowInstance(
        definition_id="scenario_e",
        definition_version="1.0.0",
        tenant_id=_TENANT,
        current_step_index=1,
        current_step_id="s2",
        state=WorkflowState.RUNNING,
        status=WorkflowStatus.RUNNING,
    )
    v1_def = await test_store.get_definition("scenario_e", tenant_id=_TENANT, version="1.0.0")
    assert v1_def is not None
    await test_store.save_instance(interrupted, definition=v1_def, tenant_id=_TENANT)

    # Simulate a process restart: a brand-new WorkflowEngine, sharing only the durable store, with
    # nothing in its own in-memory `_definitions` cache.
    engine2 = WorkflowEngine()
    engine2.set_workflow_store(test_store)

    recovered = await engine2.hydrate_and_recover(tenant_id=_TENANT)
    assert len(recovered) == 1
    assert recovered[0].id == interrupted.id

    completed = await _wait_completed(engine2, interrupted.id)
    assert completed.state == WorkflowState.COMPLETED
    assert completed.current_step_index == 3  # v1's step count -- recovery never floated to v2


# ============================================================================
# Scenario F — a published version can never be mutated (see also the dedicated persistence-layer
# proof in test_workflow_definition_lifecycle.py::
# test_published_version_content_immutable_after_further_draft_edits).
# ============================================================================


@pytest.mark.asyncio
async def test_scenario_f_published_content_is_provably_immutable(test_store: WorkflowStore) -> None:
    await _publish_v1_then_v2(test_store, "scenario_f")
    v1_first_read = await test_store.get_published_version("scenario_f", _TENANT, "1.0.0")
    assert v1_first_read is not None
    assert len(v1_first_read.steps) == 3

    # Further draft churn after v2 -- publishing a v3 -- must never retroactively alter v1's content.
    # `v1_first_read.lock_version` is v1's OWN (immutable) lock_version, irrelevant to the draft --
    # fetch the draft control row's real current lock_version first.
    control = await test_store.get_control_row("scenario_f", _TENANT)
    assert control is not None
    updated_again = await test_store.update_draft(
        "scenario_f", _TENANT, control.lock_version, name="v3", steps=[WorkflowStep(id="only_one", name="only_one")]
    )
    await test_store.publish_draft("scenario_f", _TENANT, updated_again.lock_version, "3.0.0")

    v1_second_read = await test_store.get_published_version("scenario_f", _TENANT, "1.0.0")
    assert v1_second_read is not None
    assert len(v1_second_read.steps) == 3
    assert v1_second_read.name == "v1"
    assert v1_second_read == v1_first_read  # byte-for-byte identical


# ============================================================================
# Extra re-entry path discovered during implementation: the unversioned in-memory cache.
# ============================================================================


@pytest.mark.asyncio
async def test_versioned_lookup_bypasses_and_never_poisons_the_unversioned_cache(test_store: WorkflowStore) -> None:
    """`WorkflowEngine._definitions` has no notion of version. A versioned request must never read
    from it (it could return the wrong version's content) and must never write to it (it would
    poison later "give me latest published" lookups with one pinned historical version)."""
    engine = WorkflowEngine()
    engine.set_workflow_store(test_store)
    await _publish_v1_then_v2(test_store, "scenario_cache")

    # Prime the unversioned cache via a latest-published (version=None) lookup.
    latest_before = await engine.get_definition_async("scenario_cache", tenant_id=_TENANT)
    assert latest_before.version == "2.0.0"
    assert "scenario_cache" in engine._definitions

    exact_v1 = await engine.get_definition_async("scenario_cache", tenant_id=_TENANT, version="1.0.0")
    assert len(exact_v1.steps) == 3

    # The cache must still report the true latest -- the versioned call above must not have
    # overwritten it with v1's content.
    latest_after = await engine.get_definition_async("scenario_cache", tenant_id=_TENANT)
    assert latest_after.version == "2.0.0"
