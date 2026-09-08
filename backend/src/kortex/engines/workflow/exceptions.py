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


class WorkflowGraphValidationError(WorkflowValidationError):
    """Milestone F2 — raised when a `WorkflowGraph` fails structural validation (duplicate node/edge
    IDs, a dangling node/port reference, a missing/invalid entry node, or a directed cycle)."""


class WorkflowGraphConversionError(WorkflowValidationError):
    """Milestone F2 — raised when a `WorkflowGraph` cannot be losslessly converted to the legacy flat
    `list[WorkflowStep]` shape (e.g. it branches or joins) — never silently flattened by an arbitrary
    traversal, and never has an edge discarded to force a fit."""


class WorkflowReferenceError(WorkflowValidationError):
    """Milestone F3 — raised when a `WorkflowReference` is invalid or fails to resolve: an unknown
    source node, an undeclared source_port, a self-reference, a reference to a non-ancestor node
    (a sibling branch, a descendant, or a disconnected node), or a missing path segment encountered
    at resolution time. Never carries the resolved value itself in its message (see
    `mapping.py`/`mapping_validation.py`'s own no-leakage discipline)."""


class WorkflowMappingValidationError(WorkflowValidationError):
    """Milestone F3 — raised when a `WorkflowMapping` fails structural validation: a resource limit
    exceeded (path depth, mapping value count, expression nesting depth, expression operand count),
    a type mismatch against a declared schema, or an otherwise malformed mapping. Distinct from
    `WorkflowReferenceError`, which is specific to one reference's own validity."""


class WorkflowExpressionError(WorkflowValidationError):
    """Milestone F3 — raised when a `WorkflowExpression` cannot be evaluated: an unsupported
    operator, a wrong operand count, or an operand of the wrong type for its operator (e.g. a
    non-numeric operand to SUM/SUBTRACT). Never raised for anything resembling code execution —
    every operator is a fixed, reviewed pure function (`expression.py`)."""


class WorkflowExecutionError(WorkflowError):
    """Raised when a workflow step or runtime execution encounters a fatal failure."""


class WorkflowApprovalError(WorkflowError):
    """Raised when an invalid approval decision or state transition occurs."""


class ApprovalConflictError(WorkflowApprovalError):
    """Raised when an approval ticket has already been decided or is in a conflicting state."""


class WorkflowStateConflictError(WorkflowError):
    """Raised when an optimistic concurrency version conflict occurs during instance mutation."""


class WorkflowPersistenceError(WorkflowError):
    """Raised when a durable state persistence operation fails."""


class WorkflowScheduleError(WorkflowError):
    """Base exception for all workflow scheduling errors."""


class ScheduleNotFoundError(WorkflowScheduleError):
    """Raised when a requested schedule definition is not found."""


class ScheduleConflictError(WorkflowScheduleError):
    """Raised when a schedule name or execution state conflicts."""


class ExternalExecutionError(WorkflowError):
    """Raised when a governed external operation execution fails."""


class ExternalExecutionTimeoutError(ExternalExecutionError):
    """Raised when an external operation exceeds its allocated timeout."""


class WorkflowDefinitionLifecycleError(WorkflowError):
    """Milestone F4 — base exception for all workflow definition lifecycle (draft/publish/archive/
    clone) errors. Distinct from `WorkflowError` subclasses tied to instance execution."""


class WorkflowDefinitionNotFoundError(WorkflowDefinitionLifecycleError):
    """Milestone F4 — raised when a referenced `WorkflowDefinition`/`WorkflowDefinitionVersion` does
    not exist, or does not exist within the caller's tenant (tenant isolation is enforced by simply
    never distinguishing "wrong tenant" from "does not exist" in this error)."""


class WorkflowDefinitionConflictError(WorkflowDefinitionLifecycleError):
    """Milestone F4 — raised on an optimistic-lock conflict updating the mutable DRAFT row (D14):
    the caller's `lock_version` no longer matches the persisted row's current `lock_version`."""


class WorkflowDefinitionStateError(WorkflowDefinitionLifecycleError):
    """Milestone F4 — raised on an illegal lifecycle state transition: mutating a PUBLISHED or
    ARCHIVED version, publishing/archiving a definition with no DRAFT, or any other transition not
    in the explicit DRAFT/PUBLISHED/ARCHIVED state model (D9)."""


class WorkflowDefinitionAuthorizationError(WorkflowDefinitionLifecycleError):
    """Milestone F4 — raised when the publishing principal is not authorized to invoke one or more
    capabilities referenced by the definition being published (D11), or a referenced capability does
    not exist in the Registry (D10)."""
