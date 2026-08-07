"""
Unit tests for KORTEX Workflow State Machine.
"""

from __future__ import annotations

import pytest

from kortex.engines.workflow.exceptions import WorkflowStateError
from kortex.engines.workflow.models import WorkflowInstance, WorkflowState, WorkflowStatus
from kortex.engines.workflow.state_machine import WorkflowStateMachine


def test_valid_state_transitions() -> None:
    """Test standard allowed lifecycle transitions:
    CREATED -> VALIDATED -> READY -> RUNNING -> WAITING -> APPROVED -> RUNNING -> COMPLETED.
    """
    instance = WorkflowInstance(definition_id="def_test")
    assert instance.state == WorkflowState.CREATED

    WorkflowStateMachine.transition(instance, WorkflowState.VALIDATED)
    assert instance.state == WorkflowState.VALIDATED
    assert instance.status == WorkflowStatus.PENDING

    WorkflowStateMachine.transition(instance, WorkflowState.READY)
    assert instance.state == WorkflowState.READY

    WorkflowStateMachine.transition(instance, WorkflowState.RUNNING)
    assert instance.state == WorkflowState.RUNNING
    assert instance.status == WorkflowStatus.RUNNING

    WorkflowStateMachine.transition(instance, WorkflowState.WAITING)
    assert instance.state == WorkflowState.WAITING
    assert instance.status == WorkflowStatus.WAITING_APPROVAL

    WorkflowStateMachine.transition(instance, WorkflowState.APPROVED)
    assert instance.state == WorkflowState.APPROVED
    assert instance.status == WorkflowStatus.RUNNING

    WorkflowStateMachine.transition(instance, WorkflowState.RUNNING)
    assert instance.state == WorkflowState.RUNNING

    WorkflowStateMachine.transition(instance, WorkflowState.COMPLETED)
    assert instance.state == WorkflowState.COMPLETED
    assert instance.status == WorkflowStatus.COMPLETED


def test_illegal_state_transition_raises_error() -> None:
    """Test that illegal state transitions raise WorkflowStateError."""
    instance = WorkflowInstance(definition_id="def_test")
    assert instance.state == WorkflowState.CREATED

    # Cannot transition directly from CREATED to COMPLETED
    with pytest.raises(WorkflowStateError, match="Illegal workflow state transition"):
        WorkflowStateMachine.transition(instance, WorkflowState.COMPLETED)


def test_terminal_state_transitions_fail() -> None:
    """Test that transitions out of terminal states (COMPLETED/FAILED/CANCELLED) fail."""
    instance = WorkflowInstance(definition_id="def_test")
    instance.state = WorkflowState.COMPLETED

    with pytest.raises(WorkflowStateError):
        WorkflowStateMachine.transition(instance, WorkflowState.RUNNING)
