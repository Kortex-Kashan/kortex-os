"""
Unit tests for KORTEX WorkflowEngine facade, lifecycle, error paths, and diagnostics.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.exceptions import ResourceNotFoundError
from kortex.core.kernel import Kernel
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import (
    WorkflowApprovalError,
    WorkflowStateError,
    WorkflowValidationError,
)
from kortex.engines.workflow.interfaces import IWorkflowExecutor
from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalState,
    CompensationAction,
    RetryPolicy,
    WorkflowDefinition,
    WorkflowSettings,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)


@pytest.mark.asyncio
async def test_workflow_engine_properties_and_accessors() -> None:
    """Test WorkflowEngine properties, settings, definitions, instances, and diagnostics interfaces."""
    custom_settings = WorkflowSettings(worker_count=8)
    engine = WorkflowEngine(settings=custom_settings)

    assert engine.name == "workflow"
    assert engine.dependencies == ["configuration", "registry", "event", "storage"]
    assert engine.settings.worker_count == 8
    assert isinstance(engine, IWorkflowExecutor)
    assert engine.version() == "1.0.0"
    assert engine.status() == EngineState.UNINITIALIZED.value
    assert "kortex.workflow.instance.start" in engine.capabilities()

    step = WorkflowStep(id="s1", name="Step 1")
    wf_def = WorkflowDefinition(id="wf_acc", name="Accessors Workflow", steps=[step])
    engine.register_definition(wf_def)

    assert len(engine.list_definitions()) == 1
    assert engine.list_definitions()[0].id == "wf_acc"

    instance = await engine.start_workflow("wf_acc")
    await asyncio.sleep(0.05)

    assert len(engine.list_instances()) == 1
    assert engine.list_instances()[0].id == instance.id
    assert engine.get_instance(instance.id).id == instance.id


@pytest.mark.asyncio
async def test_workflow_engine_error_paths() -> None:
    """Test error handling for empty definitions, missing definitions, missing instances."""
    engine = WorkflowEngine()

    # Registering empty definition raises WorkflowValidationError
    empty_def = WorkflowDefinition(id="wf_empty", name="Empty", steps=[])
    with pytest.raises(WorkflowValidationError, match="must contain at least one step"):
        engine.register_definition(empty_def)

    # Getting missing definition raises ResourceNotFoundError
    with pytest.raises(ResourceNotFoundError, match="not found"):
        engine.get_definition("missing_def_id")

    # Getting missing instance raises ResourceNotFoundError
    with pytest.raises(ResourceNotFoundError, match="not found"):
        engine.get_instance(uuid4())


@pytest.mark.asyncio
async def test_workflow_engine_initialize_and_stop_lifecycle() -> None:
    """Test initialization failure path and redundant stop calling."""
    kernel = Kernel()
    engine = WorkflowEngine()

    # Initialize without Storage Engine in container raises exception
    with pytest.raises(Exception):
        await engine.initialize(kernel)

    assert engine.state == EngineState.FAILED

    # Calling stop on stopped/uninitialized engine returns safely
    engine._set_state(EngineState.UNINITIALIZED)
    await engine.stop()
    assert engine.state == EngineState.UNINITIALIZED


@pytest.mark.asyncio
async def test_approval_sweep_loop_stop_actually_awaits_task_completion() -> None:
    """M6.4 hardening regression: `_stop_approval_sweep_loop` previously
    called `.cancel()` and returned immediately without ever awaiting the
    task -- `WorkflowEngine.stop()` (and therefore `Kernel.shutdown()`)
    could return while the loop's coroutine was still unwinding mid-await.
    Because `approval_sweep_enabled` defaults to `True`, this ran for
    EVERY production-shaped Kernel boot, not just tests that exercise it
    directly -- a per-boot task leak that, at real-suite scale, produced a
    genuine hang during pytest-asyncio's own event-loop teardown (proven
    directly: the full backend suite hung reproducibly before this fix and
    completed cleanly, twice, after it).

    Proves the fix's actual contract: after `_stop_approval_sweep_loop()`
    returns, the task is unconditionally done -- not merely cancelled and
    still finishing up somewhere in the background.
    """
    engine = WorkflowEngine(settings=WorkflowSettings(approval_sweep_interval_seconds=60.0))
    engine._start_approval_sweep_loop(engine.settings.approval_sweep_interval_seconds)
    task = engine._approval_sweep_task
    assert task is not None
    assert not task.done()

    await engine._stop_approval_sweep_loop()

    assert task.done()
    assert engine._approval_sweep_task is None
    assert engine._approval_sweep_running is False


@pytest.mark.asyncio
async def test_workflow_engine_health_check_method() -> None:
    """Test health_check method delegates to health()."""
    engine = WorkflowEngine()
    report = await engine.health_check()
    assert report["engine"] == "workflow"
    assert report["healthy"] is False


@pytest.mark.asyncio
async def test_workflow_engine_event_and_storage_error_isolation() -> None:
    """Test _publish_event and _persist_instance_snapshot gracefully catch exceptions."""
    engine = WorkflowEngine()
    step = WorkflowStep(id="s1", name="Step 1")
    wf_def = WorkflowDefinition(id="wf_iso", name="Isolation", steps=[step])

    mock_kernel = MagicMock()
    mock_kernel.publish_event.side_effect = RuntimeError("Event bus down")
    engine._kernel = mock_kernel

    mock_storage = MagicMock()
    mock_storage.object.put_object.side_effect = RuntimeError("Disk full")
    engine._storage_engine = mock_storage

    engine.register_definition(wf_def)
    await asyncio.sleep(0.05)

    instance = await engine.start_workflow("wf_iso")
    await asyncio.sleep(0.05)

    assert instance.state == WorkflowState.COMPLETED


@pytest.mark.asyncio
async def test_workflow_engine_step_failure_and_rollback() -> None:
    """Test step failure triggering LIFO compensation stack and transitioning instance to FAILED."""
    engine = WorkflowEngine()

    mock_kernel = MagicMock()
    mock_kernel.publish_event = MagicMock(side_effect=lambda **kwargs: asyncio.sleep(0))

    def failing_handler(**kwargs):
        raise RuntimeError("Capability step failed")

    mock_desc = MagicMock()
    mock_desc.handler = failing_handler

    mock_kernel.get_capability.side_effect = lambda cap: mock_desc if cap == "dummy.fail" else None
    engine._kernel = mock_kernel

    comp_action = CompensationAction(name="Comp Step 0", capability_name="dummy.rollback")
    step0 = WorkflowStep(id="s0", name="Successful Step", compensation_action=comp_action)
    step1 = WorkflowStep(
        id="s1",
        name="Failing Step",
        capability_name="dummy.fail",
        retry_policy=RetryPolicy(max_attempts=1, initial_delay_seconds=0.01),
        on_failure_continue=False,
    )

    wf_def = WorkflowDefinition(id="wf_fail", name="Failing Workflow", steps=[step0, step1])
    engine.register_definition(wf_def)

    # Start workflow and await background step completion
    instance = await engine.start_workflow("wf_fail")
    await asyncio.sleep(0.05)

    failed_instance = engine.get_instance(instance.id)
    assert failed_instance.state == WorkflowState.FAILED
    assert failed_instance.status == WorkflowStatus.FAILED
    assert engine.metrics()["workflows_failed"] == 1
    assert engine.metrics()["compensations_executed"] == 1


@pytest.mark.asyncio
async def test_workflow_engine_pause_and_resume_validation() -> None:
    """Test illegal state transitions for pause and resume operations."""
    engine = WorkflowEngine()
    step = WorkflowStep(id="s1", name="Step 1", is_approval_step=True, required_approval_role="ADMIN")
    wf_def = WorkflowDefinition(id="wf_pr", name="PR Workflow", steps=[step])

    engine.register_definition(wf_def)
    instance = await engine.start_workflow("wf_pr")
    await asyncio.sleep(0.05)

    # Pausing a workflow in WAITING state raises WorkflowStateError
    with pytest.raises(WorkflowStateError, match="Cannot pause workflow in state 'WAITING'"):
        await engine.pause_workflow(instance.id)

    # Manually transition state to RUNNING to test pause_workflow success
    instance.state = WorkflowState.RUNNING
    paused = await engine.pause_workflow(instance.id)
    assert paused.status == WorkflowStatus.PAUSED

    # Resuming workflow from RUNNING state succeeds
    resumed = await engine.resume_workflow(instance.id)
    assert resumed.status == WorkflowStatus.RUNNING

    # Resuming workflow in FAILED state raises WorkflowStateError
    instance.state = WorkflowState.FAILED
    with pytest.raises(WorkflowStateError, match="Cannot resume workflow in state 'FAILED'"):
        await engine.resume_workflow(instance.id)


@pytest.mark.asyncio
async def test_workflow_engine_approval_rejection() -> None:
    """Test submitting a REJECTED decision fails the workflow instance."""
    engine = WorkflowEngine()
    step1 = WorkflowStep(id="s1", name="Approval Step", is_approval_step=True, required_approval_role="MANAGER")
    wf_def = WorkflowDefinition(id="wf_rej", name="Rejection Workflow", steps=[step1])

    engine.register_definition(wf_def)
    instance = await engine.start_workflow("wf_rej")
    await asyncio.sleep(0.05)

    assert instance.state == WorkflowState.WAITING

    pending = await engine.approval_manager.list_pending_requests(role_filter="MANAGER")
    assert len(pending) == 1

    decision = ApprovalDecision(
        request_id=pending[0].id,
        approver_id="manager_bob",
        decision=ApprovalState.REJECTED,
        reason="Budget exceeded",
    )

    rejected_instance = await engine.submit_approval_decision(decision)
    assert rejected_instance.state == WorkflowState.FAILED
    assert rejected_instance.status == WorkflowStatus.FAILED


@pytest.mark.asyncio
async def test_workflow_engine_diagnostics() -> None:
    """Test full health, metrics, and diagnostics reporting."""
    engine = WorkflowEngine()

    health = engine.health()
    assert health["engine"] == "workflow"
    assert health["healthy"] is False

    diag = engine.diagnostics()
    assert diag["engine"] == "workflow"
    assert diag["version"] == "1.0.0"
    assert "metrics" in diag
    assert "settings" in diag
    assert "capabilities" in diag
