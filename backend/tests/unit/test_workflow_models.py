"""
Unit tests for KORTEX Workflow Engine Models & Settings.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
    RetryPolicy,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowPriority,
    WorkflowSettings,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
    WorkflowTrigger,
)


def test_retry_policy_validation() -> None:
    """Test RetryPolicy model default values and validation constraints."""
    policy = RetryPolicy()
    assert policy.max_attempts == 3
    assert policy.backoff_factor == 2.0
    assert policy.initial_delay_seconds == 1.0
    assert policy.jitter is True

    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=0)

    with pytest.raises(ValidationError):
        RetryPolicy(backoff_factor=0.5)


def test_workflow_step_model() -> None:
    """Test WorkflowStep creation and defaults."""
    step = WorkflowStep(id="step_1", name="Step 1", capability_name="test.cap")
    assert step.id == "step_1"
    assert step.name == "Step 1"
    assert step.capability_name == "test.cap"
    assert step.is_approval_step is False
    assert step.on_failure_continue is False


def test_workflow_definition_model() -> None:
    """Test WorkflowDefinition creation with steps."""
    step = WorkflowStep(id="s1", name="Step One")
    wf_def = WorkflowDefinition(
        id="def_1",
        name="Sample Workflow",
        version="1.0.0",
        steps=[step],
        priority=WorkflowPriority.HIGH,
    )
    assert wf_def.id == "def_1"
    assert len(wf_def.steps) == 1
    assert wf_def.trigger == WorkflowTrigger.MANUAL
    assert wf_def.priority == WorkflowPriority.HIGH


def test_workflow_instance_model() -> None:
    """Test WorkflowInstance defaults and context."""
    instance = WorkflowInstance(
        definition_id="def_1",
        definition_version="1.0.0",
        context=WorkflowContext(variables={"env": "test"}),
    )
    assert instance.state == WorkflowState.CREATED
    assert instance.status == WorkflowStatus.PENDING
    assert instance.context.variables["env"] == "test"
    assert instance.current_step_index == 0


def test_approval_models() -> None:
    """Test ApprovalRequest and ApprovalDecision models."""
    instance_id = WorkflowInstance(definition_id="d").id
    req = ApprovalRequest(
        instance_id=instance_id,
        step_id="approval_step",
        required_role="MANAGER",
    )
    assert req.state == ApprovalState.PENDING

    dec = ApprovalDecision(
        request_id=req.id,
        approver_id="user_admin",
        decision=ApprovalState.APPROVED,
        reason="Looks good",
    )
    assert dec.decision == ApprovalState.APPROVED


def test_workflow_settings_defaults() -> None:
    """Test WorkflowSettings defaults."""
    settings = WorkflowSettings()
    assert settings.execution_timeout_seconds == 3600
    assert settings.worker_count == 4
    assert settings.metrics_enabled is True
