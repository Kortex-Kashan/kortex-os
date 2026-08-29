"""
KORTEX Workflow State Machine.

Enforces deterministic state transitions for WorkflowInstance objects according to
the canonical lifecycle specification:
CREATED -> VALIDATED -> READY -> RUNNING -> WAITING -> APPROVED -> RUNNING -> COMPLETED/FAILED/CANCELLED.
"""

from __future__ import annotations

import logging

from kortex.engines.workflow.exceptions import WorkflowStateError
from kortex.engines.workflow.models import WorkflowInstance, WorkflowState, WorkflowStatus

logger = logging.getLogger("kortex.engines.workflow.state_machine")

# Map of allowed transitions for each WorkflowState
ALLOWED_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.CREATED: {WorkflowState.VALIDATED, WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.VALIDATED: {WorkflowState.READY, WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.READY: {WorkflowState.RUNNING, WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.RUNNING: {
        WorkflowState.WAITING,
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
        WorkflowState.CANCELLED,
    },
    WorkflowState.WAITING: {WorkflowState.APPROVED, WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.APPROVED: {WorkflowState.RUNNING, WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.COMPLETED: set(),
    WorkflowState.FAILED: set(),
    WorkflowState.CANCELLED: set(),
}

# Map of WorkflowState to corresponding operational WorkflowStatus
STATE_TO_STATUS_MAP: dict[WorkflowState, WorkflowStatus] = {
    WorkflowState.CREATED: WorkflowStatus.PENDING,
    WorkflowState.VALIDATED: WorkflowStatus.PENDING,
    WorkflowState.READY: WorkflowStatus.PENDING,
    WorkflowState.RUNNING: WorkflowStatus.RUNNING,
    WorkflowState.WAITING: WorkflowStatus.WAITING_APPROVAL,
    WorkflowState.APPROVED: WorkflowStatus.RUNNING,
    WorkflowState.COMPLETED: WorkflowStatus.COMPLETED,
    WorkflowState.FAILED: WorkflowStatus.FAILED,
    WorkflowState.CANCELLED: WorkflowStatus.CANCELLED,
}


class WorkflowStateMachine:
    """Deterministic state machine manager for WorkflowInstance objects."""

    @staticmethod
    def can_transition(current_state: WorkflowState, target_state: WorkflowState) -> bool:
        """Check if a transition from current_state to target_state is allowed.

        Args:
            current_state: Current WorkflowState.
            target_state: Desired target WorkflowState.

        Returns:
            True if transition is allowed, False otherwise.
        """
        allowed = ALLOWED_TRANSITIONS.get(current_state, set())
        return target_state in allowed

    @classmethod
    def transition(cls, instance: WorkflowInstance, target_state: WorkflowState) -> WorkflowState:
        """Transition a WorkflowInstance to a new target WorkflowState.

        Args:
            instance: WorkflowInstance object to transition.
            target_state: Target WorkflowState.

        Returns:
            The new WorkflowState.

        Raises:
            WorkflowStateError: If the transition is illegal.
        """
        current_state = instance.state
        if current_state == target_state:
            return current_state

        if not cls.can_transition(current_state, target_state):
            logger.error(
                "Illegal workflow state transition attempted for instance '%s': %s -> %s",
                instance.id,
                current_state.value,
                target_state.value,
            )
            raise WorkflowStateError(
                f"Illegal workflow state transition: '{current_state.value}' -> '{target_state.value}' "
                f"for instance '{instance.id}'"
            )

        logger.info(
            "Workflow instance '%s' state transition: %s -> %s",
            instance.id,
            current_state.value,
            target_state.value,
        )
        instance.state = target_state
        instance.status = STATE_TO_STATUS_MAP.get(target_state, WorkflowStatus.FAILED)
        return instance.state
