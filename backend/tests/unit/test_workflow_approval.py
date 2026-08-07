"""
Unit tests for KORTEX Workflow Approval Subsystem.
"""

from __future__ import annotations

import pytest

from kortex.engines.workflow.approval import MemoryApprovalManager
from kortex.engines.workflow.exceptions import WorkflowApprovalError
from kortex.engines.workflow.models import ApprovalDecision, ApprovalState, WorkflowInstance


@pytest.mark.asyncio
async def test_approval_manager_request_creation() -> None:
    """Test creating and listing approval requests in MemoryApprovalManager."""
    manager = MemoryApprovalManager()
    instance_id = WorkflowInstance(definition_id="def_1").id

    req = await manager.create_request(
        instance_id=instance_id,
        step_id="approval_step_1",
        required_role="FINANCE_MANAGER",
    )

    assert req.instance_id == instance_id
    assert req.step_id == "approval_step_1"
    assert req.required_role == "FINANCE_MANAGER"
    assert req.state == ApprovalState.PENDING

    pending_finance = await manager.list_pending_requests(role_filter="FINANCE_MANAGER")
    assert len(pending_finance) == 1
    assert pending_finance[0].id == req.id

    pending_hr = await manager.list_pending_requests(role_filter="HR_MANAGER")
    assert len(pending_hr) == 0


@pytest.mark.asyncio
async def test_approval_manager_decision_submission() -> None:
    """Test submitting approved and rejected decisions."""
    manager = MemoryApprovalManager()
    instance_id = WorkflowInstance(definition_id="def_1").id

    req = await manager.create_request(
        instance_id=instance_id,
        step_id="step_1",
        required_role="ADMIN",
    )

    decision = ApprovalDecision(
        request_id=req.id,
        approver_id="user_admin_1",
        decision=ApprovalState.APPROVED,
        reason="Approved by Admin",
    )

    updated_req = await manager.submit_decision(decision)
    assert updated_req.state == ApprovalState.APPROVED

    # Submitting decision again for non-pending request raises error
    with pytest.raises(WorkflowApprovalError, match="is already in state 'APPROVED'"):
        await manager.submit_decision(decision)


@pytest.mark.asyncio
async def test_approval_manager_invalid_decision() -> None:
    """Test submitting an invalid decision state raises error."""
    manager = MemoryApprovalManager()
    instance_id = WorkflowInstance(definition_id="def_1").id

    req = await manager.create_request(
        instance_id=instance_id,
        step_id="step_1",
        required_role="ADMIN",
    )

    decision = ApprovalDecision(
        request_id=req.id,
        approver_id="user_admin_1",
        decision=ApprovalState.PENDING,  # PENDING is invalid as a decision
    )

    with pytest.raises(WorkflowApprovalError, match="Invalid decision state"):
        await manager.submit_decision(decision)
