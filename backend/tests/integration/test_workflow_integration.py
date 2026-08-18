"""
Integration tests for KORTEX Workflow Engine.

Verifies full Kernel boot sequence, DI Container resolution, Storage Engine persistent
snapshot integration, Event Engine pub/sub events, and capability dispatching.
"""

from __future__ import annotations

import asyncio
import pytest

from kortex.core.base_engine import EngineState
from kortex.core.kernel import Kernel
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalState,
    WorkflowDefinition,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)


@pytest.mark.asyncio
async def test_workflow_engine_kernel_integration(tmp_path) -> None:
    """Integration test: Kernel boot with StorageEngine + WorkflowEngine, event tracking, and capability execution."""
    kernel = Kernel()

    storage_engine = StorageEngine(base_directory=str(tmp_path / "wf_storage"))
    workflow_engine = WorkflowEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)

    # Boot Kernel runtime
    await kernel.boot()

    assert kernel.state.value == "RUNNING"
    assert storage_engine.state == EngineState.RUNNING
    assert workflow_engine.state == EngineState.RUNNING

    # Verify DI container resolution
    resolved_wf = kernel.container.resolve("engine.workflow")
    assert resolved_wf is workflow_engine

    # Track published events
    received_events = []

    def event_handler(event):
        if event.topic.startswith("workflow."):
            received_events.append(event)

    kernel.subscribe_event("*", event_handler)

    # Create & register workflow definition
    step1 = WorkflowStep(id="step_a", name="Step A")
    step2 = WorkflowStep(id="step_b", name="Step B", is_approval_step=True, required_approval_role="SUPERVISOR")
    step3 = WorkflowStep(id="step_c", name="Step C")

    wf_def = WorkflowDefinition(id="wf_integ", name="Integration Workflow", steps=[step1, step2, step3])
    workflow_engine.register_definition(wf_def)

    # Resolve capability from Kernel Capability Registry
    start_cap = kernel.get_capability("kortex.workflow.instance.start")
    assert start_cap.provider == "workflow"

    # Execute capability to start workflow instance (M8 test-only accessor)
    instance = await kernel._registry_engine.get_raw_handler_for_testing(
        "kortex.workflow.instance.start"
    )("wf_integ", {"project": "kortex_os"})
    assert instance.definition_id == "wf_integ"

    await asyncio.sleep(0.05)

    # Workflow should pause at step_b waiting for SUPERVISOR approval
    instance_state = workflow_engine.get_instance(instance.id)
    assert instance_state.state == WorkflowState.WAITING

    # Verify snapshot persisted in Storage Engine
    persisted_snapshot = await storage_engine.object.get_object("workflows", f"snapshots/{instance.id}.json")
    assert str(instance.id) in persisted_snapshot.decode("utf-8")

    # Approve step via capability
    pending_tickets = await workflow_engine.approval_manager.list_pending_requests(role_filter="SUPERVISOR")
    assert len(pending_tickets) == 1

    decision = ApprovalDecision(
        request_id=pending_tickets[0].id,
        approver_id="supervisor_kashan",
        decision=ApprovalState.APPROVED,
    )

    await kernel._registry_engine.get_raw_handler_for_testing("kortex.workflow.instance.approve")(decision)
    await asyncio.sleep(0.05)

    # Verify workflow completed cleanly
    final_instance = workflow_engine.get_instance(instance.id)
    assert final_instance.state == WorkflowState.COMPLETED
    assert final_instance.status == WorkflowStatus.COMPLETED

    # Verify events published
    topics = [e.topic for e in received_events]
    assert "workflow.created" in topics
    assert "workflow.started" in topics
    assert "workflow.waiting" in topics
    assert "workflow.approved" in topics
    assert "workflow.completed" in topics

    await kernel.shutdown()
    assert kernel.state.value == "STOPPED"
    assert workflow_engine.state == EngineState.STOPPED
