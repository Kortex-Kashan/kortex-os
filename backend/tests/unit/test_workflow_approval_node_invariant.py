"""
Unit tests for the Approval Node Invariant:
  is_approval_step == true => capability_name == null
Enforced by WorkflowDefinitionLifecycleManager._validate_content and blocking on publish().
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.exceptions import WorkflowDefinitionStateError
from kortex.engines.workflow.graph_compat import _STEP_METADATA_KEY
from kortex.engines.workflow.lifecycle import WorkflowDefinitionLifecycleManager
from kortex.engines.workflow.models import (
    WorkflowGraph,
    WorkflowGraphNode,
)
from kortex.engines.workflow.persistence import WorkflowStore


@pytest.fixture
async def lifecycle_manager() -> AsyncGenerator[WorkflowDefinitionLifecycleManager, None]:
    db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    await db_manager.connect()
    await db_manager.create_all_tables()
    data_store = RelationalDataStore(db_manager)
    store = WorkflowStore(data_store)
    mgr = WorkflowDefinitionLifecycleManager(store)
    yield mgr
    if db_manager._engine:
        await db_manager._engine.dispose()


@pytest.mark.asyncio
async def test_validate_rejects_step_with_approval_and_capability(
    lifecycle_manager: WorkflowDefinitionLifecycleManager,
) -> None:
    # An invalid step having both is_approval_step=True and a capability_name
    invalid_step = {
        "id": "step_1",
        "name": "Invalid Approval Mutation Step",
        "capability_name": "kortex.finance.invoice.create",
        "is_approval_step": True,
        "parameters": {},
    }

    draft = await lifecycle_manager.create(
        name="Invalid Approval Workflow",
        steps=[invalid_step],
        tenant_id="test_tenant",
    )
    def_id = draft["definition_id"]

    report = await lifecycle_manager.validate(def_id, tenant_id="test_tenant")
    assert not report["is_valid"]
    assert any("pure wait gates" in err for err in report["errors"])

    # Attempting to publish must be rejected with WorkflowDefinitionStateError
    with pytest.raises(WorkflowDefinitionStateError) as exc_info:
        await lifecycle_manager.publish(
            def_id,
            expected_lock_version=draft["lock_version"],
            tenant_id="test_tenant",
        )
    assert "pure wait gates" in str(exc_info.value)


@pytest.mark.asyncio
async def test_validate_rejects_graph_node_with_approval_and_capability(
    lifecycle_manager: WorkflowDefinitionLifecycleManager,
) -> None:
    # A graph with an approval node that also has capability_name
    invalid_node = WorkflowGraphNode(
        node_id="approval_gate",
        node_type="approval",
        capability_name="kortex.connector.notification.webhook.send",
        config={},
        metadata={_STEP_METADATA_KEY: {"name": "Approval Gate", "is_approval_step": True}},
    )
    graph = WorkflowGraph(
        entry_node_id="approval_gate",
        nodes=[invalid_node],
        edges=[],
    )

    draft = await lifecycle_manager.create(
        name="Invalid Graph Approval Workflow",
        graph=graph.model_dump(mode="json"),
        tenant_id="test_tenant",
    )
    def_id = draft["definition_id"]

    report = await lifecycle_manager.validate(def_id, tenant_id="test_tenant")
    assert not report["is_valid"]
    assert any("pure wait gates" in err for err in report["errors"])

    with pytest.raises(WorkflowDefinitionStateError) as exc_info:
        await lifecycle_manager.publish(
            def_id,
            expected_lock_version=draft["lock_version"],
            tenant_id="test_tenant",
        )
    assert "pure wait gates" in str(exc_info.value)


@pytest.mark.asyncio
async def test_valid_approval_gate_passes_validation(
    lifecycle_manager: WorkflowDefinitionLifecycleManager,
) -> None:
    # A valid workflow: step 1 is a pure wait gate (capability_name=None), step 2 is mutation
    step1 = {
        "id": "gate_1",
        "name": "Manager Approval Gate",
        "capability_name": None,
        "is_approval_step": True,
        "parameters": {},
    }
    step2 = {
        "id": "action_2",
        "name": "Send Webhook",
        "capability_name": "kortex.connector.notification.webhook.send",
        "is_approval_step": False,
        "parameters": {"url": "https://example.com/webhook", "body": {}},
    }

    draft = await lifecycle_manager.create(
        name="Valid Approval Workflow",
        steps=[step1, step2],
        tenant_id="test_tenant",
    )
    def_id = draft["definition_id"]

    report = await lifecycle_manager.validate(def_id, tenant_id="test_tenant")
    # There should be no approval error
    assert not any("pure wait gates" in err for err in report["errors"])
