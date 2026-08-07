"""
Unit tests for KORTEX StepEvaluator, retries, capability dispatch, and compensation stack.
"""

from __future__ import annotations

import asyncio
import pytest

from kortex.engines.workflow.approval import MemoryApprovalManager
from kortex.engines.workflow.evaluator import StepEvaluator
from kortex.engines.workflow.models import (
    ApprovalState,
    CompensationAction,
    RetryPolicy,
    WorkflowInstance,
    WorkflowState,
    WorkflowStep,
)


@pytest.mark.asyncio
async def test_step_evaluator_approval_manager_property() -> None:
    """Test approval_manager property on StepEvaluator."""
    approval_manager = MemoryApprovalManager()
    evaluator = StepEvaluator(approval_manager)
    assert evaluator.approval_manager is approval_manager


@pytest.mark.asyncio
async def test_step_evaluator_basic_execution() -> None:
    """Test successful step evaluation and output generation."""
    approval_manager = MemoryApprovalManager()
    evaluator = StepEvaluator(approval_manager)
    instance = WorkflowInstance(definition_id="def_1")
    step = WorkflowStep(id="step_1", name="Step 1")

    res = await evaluator.execute_step(instance, step)
    assert res.success is True
    assert res.attempts == 1
    assert "step_1" in instance.context.step_outputs


@pytest.mark.asyncio
async def test_step_evaluator_sync_and_async_capability_resolvers() -> None:
    """Test executing steps with sync, async, and missing capability handlers."""
    approval_manager = MemoryApprovalManager()
    evaluator = StepEvaluator(approval_manager)
    instance = WorkflowInstance(definition_id="def_1")

    # Sync handler
    step_sync = WorkflowStep(id="s_sync", name="Sync", capability_name="cap.sync", parameters={"x": 10})

    def sync_resolver(cap_name: str):
        if cap_name == "cap.sync":
            return lambda x: x * 2
        return None

    res_sync = await evaluator.execute_step(instance, step_sync, capability_resolver=sync_resolver)
    assert res_sync.success is True
    assert res_sync.output == 20

    # Async handler
    step_async = WorkflowStep(id="s_async", name="Async", capability_name="cap.async", parameters={"y": 5})

    async def async_handler(y: int):
        await asyncio.sleep(0.01)
        return y + 100

    def async_resolver(cap_name: str):
        if cap_name == "cap.async":
            return async_handler
        return None

    res_async = await evaluator.execute_step(instance, step_async, capability_resolver=async_resolver)
    assert res_async.success is True
    assert res_async.output == 105

    # Missing handler (resolver returns None)
    step_missing = WorkflowStep(id="s_missing", name="Missing", capability_name="cap.missing")

    def missing_resolver(cap_name: str):
        return None

    res_missing = await evaluator.execute_step(instance, step_missing, capability_resolver=missing_resolver)
    assert res_missing.success is True
    assert "Executed capability cap.missing" in res_missing.output


@pytest.mark.asyncio
async def test_step_evaluator_retry_policy_and_on_failure_continue() -> None:
    """Test retry policy execution and on_failure_continue behavior."""
    approval_manager = MemoryApprovalManager()
    evaluator = StepEvaluator(approval_manager)
    instance = WorkflowInstance(definition_id="def_1")

    # Step that fails and does not continue
    step_fail = WorkflowStep(
        id="fail_stop",
        name="Fail Stop",
        capability_name="failing.cap",
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0.01, backoff_factor=1.0),
        on_failure_continue=False,
    )

    def failing_resolver(cap_name: str):
        raise ValueError("Simulated failure")

    res_fail = await evaluator.execute_step(instance, step_fail, capability_resolver=failing_resolver)
    assert res_fail.success is False
    assert res_fail.attempts == 2
    assert "Simulated failure" in res_fail.error

    # Step that fails but has on_failure_continue=True
    step_continue = WorkflowStep(
        id="fail_continue",
        name="Fail Continue",
        capability_name="failing.cap",
        retry_policy=RetryPolicy(max_attempts=1, initial_delay_seconds=0.01),
        on_failure_continue=True,
    )

    res_cont = await evaluator.execute_step(instance, step_continue, capability_resolver=failing_resolver)
    assert res_cont.success is False
    assert "Simulated failure" in res_cont.error


@pytest.mark.asyncio
async def test_step_evaluator_approval_checkpoint() -> None:
    """Test step with is_approval_step=True pauses instance in WAITING state."""
    approval_manager = MemoryApprovalManager()
    evaluator = StepEvaluator(approval_manager)
    instance = WorkflowInstance(definition_id="def_1")
    instance.state = WorkflowState.RUNNING

    step = WorkflowStep(
        id="approval_step",
        name="Approval Step",
        is_approval_step=True,
        required_approval_role="DIRECTOR",
    )

    res = await evaluator.execute_step(instance, step)
    assert res.success is True
    assert instance.state == WorkflowState.WAITING

    pending = await approval_manager.list_pending_requests(role_filter="DIRECTOR")
    assert len(pending) == 1
    assert pending[0].step_id == "approval_step"


@pytest.mark.asyncio
async def test_compensation_stack_execution_sync_async_and_failures() -> None:
    """Test executing LIFO compensation stack with sync, async, and erroring compensation actions."""
    approval_manager = MemoryApprovalManager()
    evaluator = StepEvaluator(approval_manager)
    instance = WorkflowInstance(definition_id="def_1")

    execution_log = []

    async def async_comp_handler(arg: str):
        await asyncio.sleep(0.01)
        execution_log.append(f"async_{arg}")

    def sync_comp_handler(arg: str):
        execution_log.append(f"sync_{arg}")

    def failing_comp_handler(arg: str):
        raise RuntimeError("Compensation error")

    def compensation_resolver(cap_name: str):
        if cap_name == "comp.async":
            return async_comp_handler
        elif cap_name == "comp.sync":
            return sync_comp_handler
        elif cap_name == "comp.fail":
            return failing_comp_handler
        return None

    # Register compensation actions
    action_sync = CompensationAction(name="Sync Action", capability_name="comp.sync", parameters={"arg": "val1"})
    action_async = CompensationAction(name="Async Action", capability_name="comp.async", parameters={"arg": "val2"})
    action_fail = CompensationAction(name="Failing Action", capability_name="comp.fail", parameters={"arg": "val3"})

    instance.compensation_stack.append(action_sync)
    instance.compensation_stack.append(action_async)
    instance.compensation_stack.append(action_fail)

    # Execute LIFO compensation stack (fail -> async -> sync)
    results = await evaluator.execute_compensation_stack(instance, capability_resolver=compensation_resolver)
    assert len(results) == 3

    assert results[0]["name"] == "Failing Action"
    assert results[0]["status"] == "FAILED"
    assert "Compensation error" in results[0]["error"]

    assert results[1]["name"] == "Async Action"
    assert results[1]["status"] == "COMPENSATED"

    assert results[2]["name"] == "Sync Action"
    assert results[2]["status"] == "COMPENSATED"

    assert execution_log == ["async_val2", "sync_val1"]
    assert len(instance.compensation_stack) == 0
