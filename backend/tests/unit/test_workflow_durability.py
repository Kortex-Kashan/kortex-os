"""
KORTEX OS - Phase 5 / Milestone 5.1
Workflow Durability, State Hydration, Optimistic Concurrency, and Restart Recovery Tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import (
    WorkflowStateConflictError,
    WorkflowStateError,
)
from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalState,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)
from kortex.engines.workflow.persistence import (
    WorkflowInstanceModel,
    WorkflowStore,
    _instance_to_model,
)


@pytest.fixture
async def test_store() -> AsyncGenerator[WorkflowStore, None]:
    """Provide an isolated, in-memory SQLite WorkflowStore fixture."""
    db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    await db_manager.connect()
    await db_manager.create_all_tables()
    data_store = RelationalDataStore(db_manager)
    store = WorkflowStore(data_store)
    yield store
    if db_manager._engine:
        await db_manager._engine.dispose()


@pytest.mark.asyncio
async def test_durable_definition_crud_and_tenant_isolation(test_store: WorkflowStore) -> None:
    """Test saving, retrieving, and listing workflow definitions with tenant boundaries."""
    step1 = WorkflowStep(id="s1", name="Step 1")
    step2 = WorkflowStep(id="s2", name="Step 2")

    def_tenant_a = WorkflowDefinition(
        id="def_a",
        name="Tenant A Workflow",
        version="1.0.0",
        tenant_id="tenant-alpha",
        steps=[step1, step2],
    )
    def_tenant_b = WorkflowDefinition(
        id="def_b",
        name="Tenant B Workflow",
        version="1.0.0",
        tenant_id="tenant-beta",
        steps=[step1],
    )

    await test_store.save_definition(def_tenant_a, tenant_id="tenant-alpha")
    await test_store.save_definition(def_tenant_b, tenant_id="tenant-beta")

    # Read back tenant A definition
    loaded_a = await test_store.get_definition("def_a", tenant_id="tenant-alpha")
    assert loaded_a is not None
    assert loaded_a.id == "def_a"
    assert loaded_a.name == "Tenant A Workflow"
    assert len(loaded_a.steps) == 2
    assert loaded_a.steps[0].id == "s1"
    assert loaded_a.steps[1].id == "s2"

    # Tenant B cannot access Tenant A definition
    cross_tenant = await test_store.get_definition("def_a", tenant_id="tenant-beta")
    assert cross_tenant is None

    # List definitions per tenant
    list_a = await test_store.list_definitions(tenant_id="tenant-alpha")
    assert len(list_a) == 1
    assert list_a[0].id == "def_a"

    list_b = await test_store.list_definitions(tenant_id="tenant-beta")
    assert len(list_b) == 1
    assert list_b[0].id == "def_b"


@pytest.mark.asyncio
async def test_instance_persistence_sanitizes_session_token(test_store: WorkflowStore) -> None:
    """Test that context persistence strips raw ephemeral session tokens to prevent disk leaks."""
    step = WorkflowStep(id="s1", name="Step 1")
    wf_def = WorkflowDefinition(id="wf_sec", name="Security Flow", steps=[step])
    await test_store.save_definition(wf_def, tenant_id="tenant-sec")

    ctx = WorkflowContext(
        variables={"customer_id": "cust_123", "order_total": 450.0},
        session_token={
            "sub": "user_admin",
            "roles": ["admin"],
            "signature": "abcdef0123456789",
            "secret_key": "SUPER_SECRET",
        },
    )
    instance = WorkflowInstance(
        definition_id="wf_sec",
        tenant_id="tenant-sec",
        context=ctx,
        state=WorkflowState.RUNNING,
        status=WorkflowStatus.RUNNING,
    )

    await test_store.save_instance(instance, definition=wf_def, tenant_id="tenant-sec")

    # Retrieve instance and inspect persisted context
    loaded = await test_store.get_instance(instance.id, tenant_id="tenant-sec")
    assert loaded is not None
    assert loaded.context.variables["customer_id"] == "cust_123"
    assert loaded.context.variables["order_total"] == 450.0
    # Ephemeral session token MUST be omitted or None when rehydrated from disk
    assert loaded.context.session_token is None


@pytest.mark.asyncio
async def test_step_execution_ledger(test_store: WorkflowStore) -> None:
    """Test step execution ledger recording start, completion, outputs, and errors."""
    step = WorkflowStep(id="step_calc", name="Compute Total")
    wf_def = WorkflowDefinition(id="wf_ledger", name="Ledger Flow", steps=[step])
    await test_store.save_definition(wf_def, tenant_id="tenant-1")

    instance = WorkflowInstance(
        definition_id="wf_ledger",
        tenant_id="tenant-1",
        state=WorkflowState.RUNNING,
        status=WorkflowStatus.RUNNING,
    )
    await test_store.save_instance(instance, definition=wf_def, tenant_id="tenant-1")

    # Record step start
    run_id = await test_store.record_step_run_start(instance.id, "step_calc", attempt=1)
    assert run_id is not None

    runs = await test_store.list_step_runs(instance.id)
    assert len(runs) == 1
    assert runs[0]["status"] == "RUNNING"
    assert runs[0]["step_id"] == "step_calc"
    assert runs[0]["attempt"] == 1

    # Record atomic completion and advance
    instance.current_step_index = 1
    await test_store.record_step_complete_and_advance_instance(
        run_id=run_id,
        instance=instance,
        output={"computed_tax": 42.5},
        error=None,
    )

    runs_after = await test_store.list_step_runs(instance.id)
    assert len(runs_after) == 1
    assert runs_after[0]["status"] == "COMPLETED"
    assert "computed_tax" in runs_after[0]["output"]
    assert runs_after[0]["completed_at"] is not None

    # Verify instance version incremented
    loaded_inst = await test_store.get_instance(instance.id, tenant_id="tenant-1")
    assert loaded_inst is not None
    assert loaded_inst.version == 2
    assert loaded_inst.current_step_index == 1


@pytest.mark.asyncio
async def test_optimistic_concurrency_conflict_detection(test_store: WorkflowStore) -> None:
    """Test that concurrent writes on the same instance version raise WorkflowStateConflictError."""
    step = WorkflowStep(id="s1", name="Step 1")
    wf_def = WorkflowDefinition(id="wf_occ", name="OCC Flow", steps=[step])
    await test_store.save_definition(wf_def, tenant_id="default")

    instance = WorkflowInstance(
        definition_id="wf_occ",
        tenant_id="default",
        version=1,
        state=WorkflowState.RUNNING,
        status=WorkflowStatus.RUNNING,
    )
    await test_store.save_instance(instance, definition=wf_def, tenant_id="default")

    # Worker A and Worker B both read instance at version 1
    inst_a = await test_store.get_instance(instance.id)
    inst_b = await test_store.get_instance(instance.id)
    assert inst_a is not None
    assert inst_b is not None
    assert inst_a.version == 1
    assert inst_b.version == 1

    # Worker A updates instance successfully -> version becomes 2
    inst_a.current_step_index = 1
    await test_store.update_instance(inst_a)

    loaded = await test_store.get_instance(instance.id)
    assert loaded is not None
    assert loaded.version == 2

    # Worker B tries to update using stale version 1 -> MUST raise WorkflowStateConflictError
    inst_b.current_step_index = 2
    with pytest.raises(WorkflowStateConflictError, match="Optimistic concurrency conflict"):
        await test_store.update_instance(inst_b)


@pytest.mark.asyncio
async def test_terminal_states_never_resurrected_on_recovery(test_store: WorkflowStore) -> None:
    """Test that completed, failed, and cancelled workflow instances are never returned for recovery."""
    step = WorkflowStep(id="s1", name="Step 1")
    wf_def = WorkflowDefinition(id="wf_term", name="Terminal Flow", steps=[step])
    await test_store.save_definition(wf_def, tenant_id="default")

    # Completed instance
    inst_completed = WorkflowInstance(
        definition_id="wf_term",
        state=WorkflowState.COMPLETED,
        status=WorkflowStatus.COMPLETED,
    )
    # Failed instance
    inst_failed = WorkflowInstance(
        definition_id="wf_term",
        state=WorkflowState.FAILED,
        status=WorkflowStatus.FAILED,
    )
    # Cancelled instance
    inst_cancelled = WorkflowInstance(
        definition_id="wf_term",
        state=WorkflowState.CANCELLED,
        status=WorkflowStatus.CANCELLED,
    )
    # Unfinalized active instance
    inst_running = WorkflowInstance(
        definition_id="wf_term",
        state=WorkflowState.RUNNING,
        status=WorkflowStatus.RUNNING,
    )
    # Unfinalized waiting approval instance
    inst_waiting = WorkflowInstance(
        definition_id="wf_term",
        state=WorkflowState.WAITING,
        status=WorkflowStatus.PAUSED,
    )

    await test_store.save_instance(inst_completed, definition=wf_def)
    await test_store.save_instance(inst_failed, definition=wf_def)
    await test_store.save_instance(inst_cancelled, definition=wf_def)
    await test_store.save_instance(inst_running, definition=wf_def)
    await test_store.save_instance(inst_waiting, definition=wf_def)

    unfinalized = await test_store.get_unfinalized_instances()
    unfinalized_ids = {i.id for i in unfinalized}

    assert len(unfinalized) == 2
    assert inst_running.id in unfinalized_ids
    assert inst_waiting.id in unfinalized_ids
    assert inst_completed.id not in unfinalized_ids
    assert inst_failed.id not in unfinalized_ids
    assert inst_cancelled.id not in unfinalized_ids


@pytest.mark.asyncio
async def test_restart_recovery_waiting_workflow(test_store: WorkflowStore) -> None:
    """Test process restart simulation for a workflow in WAITING approval state."""
    step1 = WorkflowStep(id="step_init", name="Initialize")
    step2 = WorkflowStep(
        id="step_approval",
        name="Manager Signoff",
        is_approval_step=True,
        required_approval_role="REGIONAL_DIRECTOR",
    )
    step3 = WorkflowStep(id="step_deploy", name="Deploy")
    wf_def = WorkflowDefinition(
        id="wf_hire",
        name="Hire Approval",
        steps=[step1, step2, step3],
        tenant_id="tenant-hr",
    )

    # 1. First engine process creates and pauses workflow waiting for approval
    engine1 = WorkflowEngine()
    engine1.set_workflow_store(test_store)
    engine1.register_definition(wf_def, tenant_id="tenant-hr")

    instance = await engine1.start_workflow("wf_hire", tenant_id="tenant-hr")
    for _ in range(20):
        if engine1.get_instance(instance.id).state == WorkflowState.WAITING:
            break
        await asyncio.sleep(0.05)

    assert engine1.get_instance(instance.id).state == WorkflowState.WAITING
    assert engine1.get_instance(instance.id).current_step_id == "step_approval"

    # 2. Simulate complete process crash / shutdown
    await engine1.stop()
    del engine1

    # 3. Second engine process starts up pointing to the exact same persistent store
    engine2 = WorkflowEngine()
    engine2.set_workflow_store(test_store)
    engine2.register_definition(wf_def, tenant_id="tenant-hr")

    # Execute startup hydration
    recovered = await engine2.hydrate_and_recover(tenant_id="tenant-hr")
    assert len(recovered) == 1
    recovered_instance = recovered[0]
    assert recovered_instance.id == instance.id
    assert recovered_instance.state == WorkflowState.WAITING
    assert recovered_instance.current_step_id == "step_approval"

    # Approval ticket was restored in approval manager
    pending = await engine2.approval_manager.list_pending_requests(role_filter="REGIONAL_DIRECTOR")
    assert len(pending) == 1
    assert pending[0].instance_id == instance.id
    assert pending[0].step_id == "step_approval"

    # Now approve the ticket in the new engine process
    decision = ApprovalDecision(
        request_id=pending[0].id,
        approver_id="director_alice",
        decision=ApprovalState.APPROVED,
    )
    await engine2.submit_approval_decision(decision, tenant_id="tenant-hr")

    for _ in range(20):
        if engine2.get_instance(instance.id).state == WorkflowState.COMPLETED:
            break
        await asyncio.sleep(0.05)

    final_inst = engine2.get_instance(instance.id)
    assert final_inst.state == WorkflowState.COMPLETED
    assert final_inst.status == WorkflowStatus.COMPLETED

    # Check database reflects terminal completion
    db_inst = await test_store.get_instance(instance.id, tenant_id="tenant-hr")
    assert db_inst is not None
    assert db_inst.state == WorkflowState.COMPLETED


@pytest.mark.asyncio
async def test_restart_recovery_interrupted_running_workflow(test_store: WorkflowStore) -> None:
    """Test process restart simulation for an interrupted RUNNING workflow resuming mid-execution."""
    step1 = WorkflowStep(id="step_1", name="Step 1")
    step2 = WorkflowStep(id="step_2", name="Step 2")
    step3 = WorkflowStep(id="step_3", name="Step 3")
    wf_def = WorkflowDefinition(
        id="wf_batch",
        name="Batch Processing",
        steps=[step1, step2, step3],
        tenant_id="tenant-ops",
    )

    # Save definition and a RUNNING instance simulating process crash right after Step 1 completed
    await test_store.save_definition(wf_def, tenant_id="tenant-ops")

    interrupted_instance = WorkflowInstance(
        definition_id="wf_batch",
        tenant_id="tenant-ops",
        current_step_index=1,
        current_step_id="step_2",
        state=WorkflowState.RUNNING,
        status=WorkflowStatus.RUNNING,
    )
    await test_store.save_instance(interrupted_instance, definition=wf_def, tenant_id="tenant-ops")

    # Start new engine and recover
    engine = WorkflowEngine()
    engine.set_workflow_store(test_store)
    engine.register_definition(wf_def, tenant_id="tenant-ops")

    recovered = await engine.hydrate_and_recover(tenant_id="tenant-ops")
    assert len(recovered) == 1
    assert recovered[0].id == interrupted_instance.id

    # Wait for execution task to complete from step 2 onward
    for _ in range(20):
        if engine.get_instance(interrupted_instance.id).state == WorkflowState.COMPLETED:
            break
        await asyncio.sleep(0.05)

    completed = engine.get_instance(interrupted_instance.id)
    assert completed.state == WorkflowState.COMPLETED
    assert completed.current_step_index == 3

    # Verify ledger recorded runs for step_2 and step_3
    runs = await test_store.list_step_runs(interrupted_instance.id)
    recorded_steps = [r["step_id"] for r in runs]
    assert "step_2" in recorded_steps
    assert "step_3" in recorded_steps


@pytest.mark.asyncio
async def test_terminal_state_resumption_and_cancellation_rejections(test_store: WorkflowStore) -> None:
    """Test that attempting to resume or cancel terminal workflows is rejected with WorkflowStateError."""
    step = WorkflowStep(id="s1", name="Step 1")
    wf_def = WorkflowDefinition(id="wf_terminal_checks", name="Terminal Checks", steps=[step])
    await test_store.save_definition(wf_def)

    engine = WorkflowEngine()
    engine.set_workflow_store(test_store)
    engine.register_definition(wf_def)

    # 1. Completed workflow
    inst_comp = WorkflowInstance(definition_id="wf_terminal_checks", state=WorkflowState.COMPLETED)
    await test_store.save_instance(inst_comp, definition=wf_def)

    with pytest.raises(WorkflowStateError, match="Cannot resume workflow in state 'COMPLETED'"):
        await engine.resume_workflow(inst_comp.id)

    with pytest.raises(WorkflowStateError, match="already in terminal state 'COMPLETED'"):
        await engine.cancel_workflow(inst_comp.id)

    # 2. Failed workflow
    inst_fail = WorkflowInstance(definition_id="wf_terminal_checks", state=WorkflowState.FAILED)
    await test_store.save_instance(inst_fail, definition=wf_def)

    with pytest.raises(WorkflowStateError, match="Cannot resume workflow in state 'FAILED'"):
        await engine.resume_workflow(inst_fail.id)

    with pytest.raises(WorkflowStateError, match="already in terminal state 'FAILED'"):
        await engine.cancel_workflow(inst_fail.id)

    # 3. Cancelled workflow
    inst_canc = WorkflowInstance(definition_id="wf_terminal_checks", state=WorkflowState.CANCELLED)
    await test_store.save_instance(inst_canc, definition=wf_def)

    with pytest.raises(WorkflowStateError, match="Cannot resume workflow in state 'CANCELLED'"):
        await engine.resume_workflow(inst_canc.id)

    with pytest.raises(WorkflowStateError, match="already in terminal state 'CANCELLED'"):
        await engine.cancel_workflow(inst_canc.id)


@pytest.mark.asyncio
async def test_restart_recovery_ready_and_approved_workflows(test_store: WorkflowStore) -> None:
    """Test restart recovery for workflows in READY and APPROVED states."""
    step1 = WorkflowStep(id="s1", name="Step 1")
    step2 = WorkflowStep(id="s2", name="Step 2")
    wf_def = WorkflowDefinition(id="wf_ra", name="Ready and Approved Flow", steps=[step1, step2])
    await test_store.save_definition(wf_def)

    # 1. Instance in READY state
    inst_ready = WorkflowInstance(
        definition_id="wf_ra",
        state=WorkflowState.READY,
        status=WorkflowStatus.PENDING,
    )
    # 2. Instance in APPROVED state (already passed step 1 approval)
    inst_approved = WorkflowInstance(
        definition_id="wf_ra",
        current_step_index=1,
        current_step_id="s2",
        state=WorkflowState.APPROVED,
        status=WorkflowStatus.RUNNING,
    )
    await test_store.save_instance(inst_ready, definition=wf_def)
    await test_store.save_instance(inst_approved, definition=wf_def)

    engine = WorkflowEngine()
    engine.set_workflow_store(test_store)
    engine.register_definition(wf_def)

    recovered = await engine.hydrate_and_recover()
    rec_ids = {i.id for i in recovered}
    assert inst_ready.id in rec_ids
    assert inst_approved.id in rec_ids

    # Wait for both recovered background execution tasks to complete
    for _ in range(20):
        if (
            engine.get_instance(inst_ready.id).state == WorkflowState.COMPLETED
            and engine.get_instance(inst_approved.id).state == WorkflowState.COMPLETED
        ):
            break
        await asyncio.sleep(0.05)

    assert engine.get_instance(inst_ready.id).state == WorkflowState.COMPLETED
    assert engine.get_instance(inst_approved.id).state == WorkflowState.COMPLETED


@pytest.mark.asyncio
async def test_persistence_transaction_rollback_on_failure(test_store: WorkflowStore) -> None:
    """Test that database operations roll back cleanly if an exception occurs mid-transaction."""
    step = WorkflowStep(id="s1", name="Step 1")
    wf_def = WorkflowDefinition(id="wf_rb", name="Rollback Test", steps=[step])
    await test_store.save_definition(wf_def)

    inst_id = uuid4()
    inst = WorkflowInstance(id=inst_id, definition_id="wf_rb")

    # Attempt a transaction that writes and then deliberately raises an error
    async def failing_action(session: AsyncSession) -> None:
        model = _instance_to_model(inst, tenant_id="default")
        session.add(model)
        await session.flush()
        # Deliberate failure
        raise RuntimeError("Simulated transaction failure!")

    with pytest.raises(RuntimeError, match="Simulated transaction failure!"):
        await test_store._data_store.execute_in_transaction(failing_action)

    # Verify that the rollback was clean and no row was persisted
    persisted = await test_store.get_instance(inst_id)
    assert persisted is None


@pytest.mark.asyncio
async def test_step_ledger_and_instance_advancement_atomicity(test_store: WorkflowStore) -> None:
    """Test atomicity: OCC version conflict causes rollback of both step run completion and instance update."""
    step = WorkflowStep(id="step_atomic", name="Atomic Step")
    wf_def = WorkflowDefinition(id="wf_atomic", name="Atomic Flow", steps=[step])
    await test_store.save_definition(wf_def)

    inst = WorkflowInstance(
        definition_id="wf_atomic",
        version=1,
        state=WorkflowState.RUNNING,
        status=WorkflowStatus.RUNNING,
    )
    await test_store.save_instance(inst, definition=wf_def)

    run_id = await test_store.record_step_run_start(inst.id, "step_atomic", attempt=1)

    # Now intentionally alter instance version in DB to 99 to force an OCC conflict
    async def corrupt_version(session: AsyncSession) -> None:
        db_row = await session.scalar(select(WorkflowInstanceModel).where(WorkflowInstanceModel.id == str(inst.id)))
        assert db_row is not None
        db_row.version = 99

    await test_store._data_store.execute_in_transaction(corrupt_version)

    # Attempt atomic step complete + instance advance with stale version 1
    with pytest.raises(WorkflowStateConflictError, match="Optimistic lock conflict"):
        await test_store.record_step_complete_and_advance_instance(
            run_id=run_id,
            instance=inst,  # inst.version is 1, DB has 99
            output={"result": "fail"},
        )

    # Verify that the step run was rolled back and is STILL "RUNNING" (not "COMPLETED")
    runs = await test_store.list_step_runs(inst.id)
    assert len(runs) == 1
    assert runs[0]["status"] == "RUNNING"
    assert runs[0]["completed_at"] is None


@pytest.mark.asyncio
async def test_context_ephemeral_credentials_deep_scrubbing(test_store: WorkflowStore) -> None:
    """Test that session_token and ephemeral credential keys in variables are scrubbed from disk."""
    step = WorkflowStep(id="s1", name="Step 1")
    wf_def = WorkflowDefinition(id="wf_scrub", name="Scrub Flow", steps=[step])
    await test_store.save_definition(wf_def)

    ctx = WorkflowContext(
        variables={
            "order_id": "ORD-999",
            "amount": 199.99,
            "session_token": "token-1234",
            "auth_token": "bearer-secret-xyz",
            "password": "super-secret-password",
            "secret_key": "raw-key-material",
            "bearer_token": "eyJh...jwt",
        },
        session_token={"sub": "admin", "roles": ["admin"]},
    )
    inst = WorkflowInstance(
        definition_id="wf_scrub",
        context=ctx,
        state=WorkflowState.RUNNING,
    )
    await test_store.save_instance(inst, definition=wf_def)

    # Load from disk and verify
    loaded = await test_store.get_instance(inst.id)
    assert loaded is not None
    assert loaded.context.session_token is None
    vars_loaded = loaded.context.variables
    assert vars_loaded["order_id"] == "ORD-999"
    assert vars_loaded["amount"] == 199.99
    # All sensitive keys must be completely removed
    assert "session_token" not in vars_loaded
    assert "auth_token" not in vars_loaded
    assert "password" not in vars_loaded
    assert "secret_key" not in vars_loaded
    assert "bearer_token" not in vars_loaded


@pytest.mark.asyncio
async def test_production_workflow_capabilities_dispatch_and_security(tmp_path: Path) -> None:
    """Test production workflow capabilities through Kernel.invoke_capability dispatch enforcement."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "wf_dispatch_storage"))
    test_master_key = b"\x55" * 32
    test_signing_key = b"\x66" * 32
    security_engine = SecurityEngine(master_key=test_master_key, signing_private_key=test_signing_key)
    workflow_engine = WorkflowEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(workflow_engine)
    await kernel.boot()

    # Seed test users and roles
    tenant_a = f"tenant-alpha-{uuid4().hex[:6]}"
    tenant_b = f"tenant-beta-{uuid4().hex[:6]}"
    role_admin = f"WF_ADMIN_{uuid4().hex[:6]}"
    role_viewer = f"WF_VIEWER_{uuid4().hex[:6]}"
    hasher = PasswordHasher()
    cred_hash = hasher.hash("secure-pass")

    async def seed(session: AsyncSession) -> None:
        # Operator with full workflow permissions
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=tenant_a,
                principal_id="user_operator",
                principal_type="USER",
                enabled=True,
                credential_hash=cred_hash,
                roles=[role_admin],
                attributes={"clearance_level": "INTERNAL"},
            )
        )
        # Viewer with read-only permissions
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=tenant_a,
                principal_id="user_viewer",
                principal_type="USER",
                enabled=True,
                credential_hash=cred_hash,
                roles=[role_viewer],
                attributes={"clearance_level": "INTERNAL"},
            )
        )
        # Cross-tenant operator
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=tenant_b,
                principal_id="user_tenant_b",
                principal_type="USER",
                enabled=True,
                credential_hash=cred_hash,
                roles=[role_admin],
                attributes={"clearance_level": "INTERNAL"},
            )
        )
        # Role permissions
        session.add(RolePermissionRecord(id=str(uuid4()), role=role_admin, permission="workflow:start"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=role_admin, permission="workflow:read"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=role_admin, permission="workflow:cancel"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=role_viewer, permission="workflow:read"))

    await storage_engine.data.execute_in_transaction(seed)

    # Issue tokens
    operator_principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "user_operator", "password": "secure-pass"}
    )
    operator_token = await security_engine.authentication_manager.issue_token(operator_principal)

    viewer_principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "user_viewer", "password": "secure-pass"}
    )
    viewer_token = await security_engine.authentication_manager.issue_token(viewer_principal)

    tenant_b_principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_b, "principal_id": "user_tenant_b", "password": "secure-pass"}
    )
    tenant_b_token = await security_engine.authentication_manager.issue_token(tenant_b_principal)

    # Register definition with an approval step so it pauses in WAITING state
    def_id = f"wf_prod_test_{uuid4().hex[:6]}"
    step1 = WorkflowStep(id="step_hello", name="Hello Step")
    step2 = WorkflowStep(id="step_pause", name="Approval Step", is_approval_step=True, required_approval_role="ADMIN")
    wf_def = WorkflowDefinition(id=def_id, name="Prod Test Flow", steps=[step1, step2], tenant_id=tenant_a)
    workflow_engine.register_definition(wf_def, tenant_id=tenant_a)

    # 1. Unauthenticated invocation -> raises AuthenticationError
    unauth_req = CapabilityRequest(
        capability_name="kortex.workflow.instance.start",
        session_token=None,
        parameters={"definition_id": def_id, "tenant_id": tenant_a},
        context={"resource_tenant_id": tenant_a},
    )
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(unauth_req)

    # 2. Unauthorized invocation (viewer without workflow:start)
    # Expect AuthorizationDeniedError
    denied_req = CapabilityRequest(
        capability_name="kortex.workflow.instance.start",
        session_token=viewer_token,
        parameters={"definition_id": def_id, "tenant_id": tenant_a},
        context={"resource_tenant_id": tenant_a},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(denied_req)

    # 3. Authorized invocation by operator -> succeeds
    start_req = CapabilityRequest(
        capability_name="kortex.workflow.instance.start",
        session_token=operator_token,
        parameters={"definition_id": def_id, "tenant_id": tenant_a},
        context={"resource_tenant_id": tenant_a},
    )
    inst = await kernel.invoke_capability(start_req)
    assert inst.definition_id == def_id
    assert inst.tenant_id == tenant_a

    # Wait for workflow to pause at approval step
    for _ in range(20):
        if workflow_engine.get_instance(inst.id).state == WorkflowState.WAITING:
            break
        await asyncio.sleep(0.05)

    # 4. Authorized invocation: list instances
    list_req = CapabilityRequest(
        capability_name="kortex.workflow.instance.list",
        session_token=viewer_token,
        parameters={"tenant_id": tenant_a},
        context={"resource_tenant_id": tenant_a},
    )
    instances = await kernel.invoke_capability(list_req)
    assert any(i.id == inst.id for i in instances)

    # 5. Authorized invocation: get instance
    get_req = CapabilityRequest(
        capability_name="kortex.workflow.instance.get",
        session_token=viewer_token,
        parameters={"instance_id": str(inst.id), "tenant_id": tenant_a},
        context={"resource_tenant_id": tenant_a},
    )
    got = await kernel.invoke_capability(get_req)
    assert got.id == inst.id

    # 6. Tenant mismatch rejection: tenant B tries to access tenant A instance
    cross_tenant_req = CapabilityRequest(
        capability_name="kortex.workflow.instance.cancel",
        session_token=tenant_b_token,
        parameters={"instance_id": str(inst.id), "reason": "Cross-tenant cancel", "tenant_id": tenant_b},
        context={"resource_tenant_id": tenant_a},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(cross_tenant_req)

    # 7. Authorized cancel by operator
    cancel_req = CapabilityRequest(
        capability_name="kortex.workflow.instance.cancel",
        session_token=operator_token,
        parameters={"instance_id": str(inst.id), "reason": "Operator cancelled", "tenant_id": tenant_a},
        context={"resource_tenant_id": tenant_a},
    )
    cancelled_inst = await kernel.invoke_capability(cancel_req)
    assert cancelled_inst.state == WorkflowState.CANCELLED

    await kernel.shutdown()
