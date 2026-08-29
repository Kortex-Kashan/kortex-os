"""
KORTEX Workflow Engine Exceptions.

Defines the exception hierarchy for workflow validation, state machine transitions,
approval operations, and step execution failures.
"""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class WorkflowError(KortexError):
    """Base exception for all Workflow Engine errors."""


class WorkflowStateError(WorkflowError):
    """Raised when an illegal workflow state transition is attempted."""


class WorkflowValidationError(WorkflowError):
    """Raised when a workflow definition or step schema fails validation."""


class WorkflowExecutionError(WorkflowError):
    """Raised when a workflow step or runtime execution encounters a fatal failure."""


class WorkflowApprovalError(WorkflowError):
    """Raised when an invalid approval decision or state transition occurs."""


class WorkflowStateConflictError(WorkflowError):
    """Raised when an optimistic concurrency version conflict occurs during instance mutation."""


class WorkflowPersistenceError(WorkflowError):
    """Raised when a durable state persistence operation fails."""
