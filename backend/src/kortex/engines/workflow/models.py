"""
KORTEX Workflow Engine Pydantic v2 Models.

Defines all domain models, state enums, step descriptors, context wrappers,
retry policies, compensation actions, and approval models for the Workflow Engine.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class WorkflowState(str, enum.Enum):
    """Deterministic lifecycle state of a workflow instance."""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    APPROVED = "APPROVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowStatus(str, enum.Enum):
    """Operational status indicator for runtime monitoring."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowPriority(str, enum.Enum):
    """Execution priority levels for workflow scheduling."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WorkflowTrigger(str, enum.Enum):
    """Trigger sources for workflow instantiation."""

    MANUAL = "MANUAL"
    EVENT = "EVENT"
    SCHEDULED = "SCHEDULED"
    API = "API"
    RECIPE = "RECIPE"


class ApprovalState(str, enum.Enum):
    """State descriptor for approval checkpoints."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class RetryPolicy(BaseModel):
    """Policy governing automatic step retries with backoff."""

    max_attempts: int = Field(3, ge=1, description="Maximum execution attempts")
    backoff_factor: float = Field(2.0, ge=1.0, description="Exponential backoff multiplier")
    initial_delay_seconds: float = Field(1.0, ge=0.0, description="Initial delay before first retry")
    jitter: bool = Field(True, description="Add random jitter to backoff delay")


class CompensationAction(BaseModel):
    """Action descriptor for LIFO rollback/compensation execution upon step failure."""

    id: str = Field(default_factory=lambda: str(uuid4()), description="Compensation action ID")
    name: str = Field(..., description="Action name or title")
    capability_name: Optional[str] = Field(None, description="Kernel capability to execute for rollback")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Rollback parameter context")


class WorkflowStep(BaseModel):
    """Single step specification within a workflow definition."""

    id: str = Field(..., description="Unique step identifier within the workflow definition")
    name: str = Field(..., description="Human-readable step title")
    capability_name: Optional[str] = Field(None, description="Kernel capability name to invoke")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Step invocation parameters")
    is_approval_step: bool = Field(False, description="True if step requires human approval")
    required_approval_role: Optional[str] = Field(None, description="Role authorized to approve this step")
    retry_policy: Optional[RetryPolicy] = Field(None, description="Custom retry policy for this step")
    compensation_action: Optional[CompensationAction] = Field(None, description="Rollback compensation action")
    on_failure_continue: bool = Field(False, description="If True, step failure does not abort workflow")


class WorkflowContext(BaseModel):
    """Execution state context payload passed between workflow steps."""

    variables: Dict[str, Any] = Field(default_factory=dict, description="Input and runtime context variables")
    step_outputs: Dict[str, Any] = Field(default_factory=dict, description="Outputs collected from executed steps")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Execution metadata and system tags")
    session_token: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Opaque caller-supplied session token blob, stored verbatim as a plain, JSON-safe dict — "
            "never a Security Engine `TokenPayload` import here, to keep Workflow models decoupled "
            "from Security Engine types. Fields match `TokenPayload` exactly except `signature`, which "
            "MUST be a hex string here (not raw bytes) so this context remains "
            "`model_dump_json()`-safe (used by both snapshot persistence and secret-leakage "
            "inspection) — raw signature bytes are not UTF-8-representable and break JSON "
            "serialization outright. `workflow/engine.py`'s dispatch closure decodes it back to bytes "
            "and reconstructs a real `TokenPayload`, which `AuthenticationManager.verify_token()` "
            "cryptographically verifies — storing or reconstructing it here is schema validation only, "
            "never proof of authenticity."
        ),
    )


class WorkflowDefinition(BaseModel):
    """Declarative workflow definition specification."""

    id: str = Field(..., description="Unique workflow definition ID")
    name: str = Field(..., description="Workflow definition title")
    version: str = Field("1.0.0", description="Semantic version string")
    description: str = Field("", description="Detailed workflow description")
    steps: List[WorkflowStep] = Field(default_factory=list, description="Ordered execution steps")
    trigger: WorkflowTrigger = Field(WorkflowTrigger.MANUAL, description="Default trigger source")
    priority: WorkflowPriority = Field(WorkflowPriority.NORMAL, description="Workflow execution priority")
    timeout_seconds: int = Field(3600, ge=1, description="Execution timeout in seconds")


class WorkflowInstance(BaseModel):
    """Runtime instance of an executing workflow definition."""

    id: UUID = Field(default_factory=uuid4, description="Unique workflow instance UUID")
    definition_id: str = Field(..., description="Reference definition ID")
    definition_version: str = Field("1.0.0", description="Definition version string")
    current_step_index: int = Field(0, ge=0, description="Index of the currently executing step")
    current_step_id: Optional[str] = Field(None, description="ID of current step")
    state: WorkflowState = Field(WorkflowState.CREATED, description="Current lifecycle state")
    status: WorkflowStatus = Field(WorkflowStatus.PENDING, description="Current status indicator")
    context: WorkflowContext = Field(default_factory=WorkflowContext, description="Execution context")
    compensation_stack: List[CompensationAction] = Field(
        default_factory=list,
        description="LIFO stack of compensation actions registered for rollback",
    )
    trace_id: str = Field(default_factory=lambda: str(uuid4()), description="Traceability ID")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Last update timestamp")


class ExecutionResult(BaseModel):
    """Result payload for an individual step execution."""

    step_id: str = Field(..., description="Executed step ID")
    success: bool = Field(..., description="True if step executed successfully")
    output: Optional[Any] = Field(None, description="Step output payload")
    error: Optional[str] = Field(None, description="Error message if step failed")
    execution_time_ms: float = Field(0.0, ge=0.0, description="Step duration in milliseconds")
    attempts: int = Field(1, ge=1, description="Total execution attempts made")


class WorkflowResult(BaseModel):
    """Final result payload for a completed or failed workflow instance."""

    instance_id: UUID = Field(..., description="Workflow instance UUID")
    definition_name: str = Field(..., description="Name of executed definition")
    state: WorkflowState = Field(..., description="Final workflow lifecycle state")
    status: WorkflowStatus = Field(..., description="Final status indicator")
    context: WorkflowContext = Field(..., description="Final execution context")
    step_results: List[ExecutionResult] = Field(default_factory=list, description="List of step execution results")
    duration_ms: float = Field(0.0, ge=0.0, description="Total workflow duration in milliseconds")
    error: Optional[str] = Field(None, description="Terminal error message if workflow failed")


class ApprovalRequest(BaseModel):
    """Ticket representing an approval checkpoint requirement."""

    id: UUID = Field(default_factory=uuid4, description="Approval request UUID")
    instance_id: UUID = Field(..., description="Associated workflow instance UUID")
    step_id: str = Field(..., description="Step ID requiring approval")
    required_role: str = Field(..., description="Role authorized to approve")
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Request timestamp")
    state: ApprovalState = Field(ApprovalState.PENDING, description="Current approval state")


class ApprovalDecision(BaseModel):
    """Decision payload submitted by an authorized approver."""

    request_id: UUID = Field(..., description="ID of approval request")
    approver_id: str = Field(..., description="ID or username of approver")
    decision: ApprovalState = Field(..., description="APPROVED or REJECTED decision")
    reason: Optional[str] = Field(None, description="Optional decision notes or reason")
    decision_data: Dict[str, Any] = Field(default_factory=dict, description="Additional context data")
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of decision")


class ApprovalEvent(BaseModel):
    """Event payload published during approval lifecycle transitions."""

    event_type: str = Field(..., description="Type of approval event (requested, decision, expired)")
    request_id: UUID = Field(..., description="ID of approval request")
    instance_id: UUID = Field(..., description="Associated workflow instance UUID")
    step_id: str = Field(..., description="Step ID")
    approver_id: Optional[str] = Field(None, description="ID of decision submitter if applicable")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Event timestamp")


class WorkflowSettings(BaseModel):
    """Configuration settings for the Workflow Engine."""

    execution_timeout_seconds: int = Field(3600, ge=1, description="Global execution timeout")
    retry_default_attempts: int = Field(3, ge=1, description="Default max retry attempts")
    retry_backoff_factor: float = Field(2.0, ge=1.0, description="Default retry backoff factor")
    approval_timeout_seconds: int = Field(86400, ge=1, description="Default approval timeout")
    worker_count: int = Field(4, ge=1, description="Worker thread pool count")
    concurrency_limit: int = Field(100, ge=1, description="Maximum concurrent running workflows")
    logging_level: str = Field("INFO", description="Logging level string")
    metrics_enabled: bool = Field(True, description="Enable metrics collection")
