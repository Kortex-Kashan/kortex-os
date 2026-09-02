"""
Integration tests for KORTEX Workflow Engine.

Verifies full Kernel boot sequence, DI Container resolution, Storage Engine persistent
snapshot integration, Event Engine pub/sub events, and capability dispatching.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.base_engine import EngineState
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.event.engine import Event
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
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

_TEST_MASTER_KEY = b"\xcc" * 32
_TEST_SIGNING_KEY = b"\xdd" * 32


@pytest.mark.asyncio
async def test_workflow_engine_kernel_integration(tmp_path: Path) -> None:
    """Integration test: Kernel boot with StorageEngine + WorkflowEngine, event tracking, and capability execution."""
    kernel = Kernel()
    # M5-A8: explicit isolated in-memory DB — this test must never read or
    # write the shared default local database.
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")

    storage_engine = StorageEngine(base_directory=str(tmp_path / "wf_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(workflow_engine)

    # Boot Kernel runtime
    await kernel.boot()

    assert kernel.state.value == "RUNNING"
    assert storage_engine.state == EngineState.RUNNING
    assert workflow_engine.state == EngineState.RUNNING

    # Seed a real SUPERVISOR principal and issue a session token — the
    # approval decision below (M5-A1/M5-A2) must be authorized against a
    # genuinely verified principal, not a caller-supplied approver_id alone.
    hasher = PasswordHasher()

    async def _seed_supervisor(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid.uuid4()), role="SUPERVISOR", permission="workflow:approve"))
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id="default",
                principal_id="supervisor_kashan",
                principal_type="USER",
                credential_hash=hasher.hash("integration-test-password"),
                roles=["SUPERVISOR"],
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await storage_engine.data.execute_in_transaction(_seed_supervisor)

    supervisor_principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": "default",
            "principal_id": "supervisor_kashan",
            "password": "integration-test-password",
        }
    )
    supervisor_token = await security_engine.authentication_manager.issue_token(supervisor_principal)

    # Verify DI container resolution
    resolved_wf = kernel.container.resolve("engine.workflow")
    assert resolved_wf is workflow_engine

    # Track published events
    received_events: list[Event] = []

    def event_handler(event: Event) -> None:
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
    start_handler = kernel._registry_engine.get_raw_handler_for_testing("kortex.workflow.instance.start")
    assert start_handler is not None
    instance = await start_handler("wf_integ", {"project": "kortex_os"})
    assert instance.definition_id == "wf_integ"

    for _ in range(20):
        if workflow_engine.get_instance(instance.id).state == WorkflowState.WAITING:
            break
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

    # Approve via the real Kernel dispatch boundary (not the raw test-only
    # handler accessor used above for `instance.start`): approval decisions
    # are exactly the sensitive path M5-A1/M5-A2 harden, so this must be
    # exercised through actual authenticated dispatch to mean anything.
    approve_request = CapabilityRequest(
        capability_name="kortex.workflow.instance.approve",
        session_token=supervisor_token,
        parameters={"decision": decision},
        context={"resource_tenant_id": "default"},
    )
    await kernel.invoke_capability(approve_request)
    for _ in range(20):
        if workflow_engine.get_instance(instance.id).state == WorkflowState.COMPLETED and "workflow.completed" in [
            e.topic for e in received_events
        ]:
            break
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
    assert workflow_engine.state.value == EngineState.STOPPED.value
