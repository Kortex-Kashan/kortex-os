"""
KORTEX Workflow Engine Pydantic v2 Models.

Defines all domain models, state enums, step descriptors, context wrappers,
retry policies, compensation actions, and approval models for the Workflow Engine.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class WorkflowState(enum.StrEnum):
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


class WorkflowStatus(enum.StrEnum):
    """Operational status indicator for runtime monitoring."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowPriority(enum.StrEnum):
    """Execution priority levels for workflow scheduling."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WorkflowTrigger(enum.StrEnum):
    """Trigger sources for workflow instantiation."""

    MANUAL = "MANUAL"
    EVENT = "EVENT"
    SCHEDULED = "SCHEDULED"
    API = "API"
    RECIPE = "RECIPE"


class ApprovalState(enum.StrEnum):
    """State descriptor for approval checkpoints."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class RetryPolicy(BaseModel):
    """Policy governing automatic step retries with backoff."""

    max_attempts: int = Field(default=3, ge=1, description="Maximum execution attempts")
    backoff_factor: float = Field(default=2.0, ge=1.0, description="Exponential backoff multiplier")
    initial_delay_seconds: float = Field(default=1.0, ge=0.0, description="Initial delay before first retry")
    jitter: bool = Field(default=True, description="Add random jitter to backoff delay")


class CompensationAction(BaseModel):
    """Action descriptor for LIFO rollback/compensation execution upon step failure."""

    id: str = Field(default_factory=lambda: str(uuid4()), description="Compensation action ID")
    name: str = Field(..., description="Action name or title")
    capability_name: str | None = Field(default=None, description="Kernel capability to execute for rollback")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Rollback parameter context")


class WorkflowStep(BaseModel):
    """Single step specification within a workflow definition."""

    id: str = Field(..., description="Unique step identifier within the workflow definition")
    name: str = Field(..., description="Human-readable step title")
    capability_name: str | None = Field(default=None, description="Kernel capability name to invoke")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Step invocation parameters")
    is_approval_step: bool = Field(default=False, description="True if step requires human approval")
    required_approval_role: str | None = Field(default=None, description="Role authorized to approve this step")
    retry_policy: RetryPolicy | None = Field(default=None, description="Custom retry policy for this step")
    compensation_action: CompensationAction | None = Field(default=None, description="Rollback compensation action")
    on_failure_continue: bool = Field(default=False, description="If True, step failure does not abort workflow")


class WorkflowContext(BaseModel):
    """Execution state context payload passed between workflow steps."""

    variables: dict[str, Any] = Field(default_factory=dict, description="Input and runtime context variables")
    step_outputs: dict[str, Any] = Field(default_factory=dict, description="Outputs collected from executed steps")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Execution metadata and system tags")
    session_token: dict[str, Any] | None = Field(
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
    version: str = Field(default="1.0.0", description="Semantic version string")
    description: str = Field(default="", description="Detailed workflow description")
    tenant_id: str = Field(default="default", description="Tenant ID owning this definition")
    steps: list[WorkflowStep] = Field(default_factory=list, description="Ordered execution steps")
    trigger: WorkflowTrigger = Field(default=WorkflowTrigger.MANUAL, description="Default trigger source")
    priority: WorkflowPriority = Field(default=WorkflowPriority.NORMAL, description="Workflow execution priority")
    timeout_seconds: int = Field(default=3600, ge=1, description="Execution timeout in seconds")


class WorkflowInstance(BaseModel):
    """Runtime instance of an executing workflow definition."""

    id: UUID = Field(default_factory=uuid4, description="Unique workflow instance UUID")
    definition_id: str = Field(..., description="Reference definition ID")
    definition_version: str = Field(default="1.0.0", description="Definition version string")
    tenant_id: str = Field(default="default", description="Tenant ID owning this instance")
    current_step_index: int = Field(default=0, ge=0, description="Index of the currently executing step")
    current_step_id: str | None = Field(default=None, description="ID of current step")
    state: WorkflowState = Field(default=WorkflowState.CREATED, description="Current lifecycle state")
    status: WorkflowStatus = Field(default=WorkflowStatus.PENDING, description="Current status indicator")
    context: WorkflowContext = Field(default_factory=WorkflowContext, description="Execution context")
    compensation_stack: list[CompensationAction] = Field(
        default_factory=list,
        description="LIFO stack of compensation actions registered for rollback",
    )
    trace_id: str = Field(default_factory=lambda: str(uuid4()), description="Traceability ID")
    version: int = Field(default=1, ge=1, description="Optimistic locking version counter")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")


class ExecutionResult(BaseModel):
    """Result payload for an individual step execution."""

    step_id: str = Field(..., description="Executed step ID")
    success: bool = Field(..., description="True if step executed successfully")
    output: Any | None = Field(default=None, description="Step output payload")
    error: str | None = Field(default=None, description="Error message if step failed")
    execution_time_ms: float = Field(default=0.0, ge=0.0, description="Step duration in milliseconds")
    attempts: int = Field(default=1, ge=1, description="Total execution attempts made")


class WorkflowResult(BaseModel):
    """Final result payload for a completed or failed workflow instance."""

    instance_id: UUID = Field(..., description="Workflow instance UUID")
    definition_name: str = Field(..., description="Name of executed definition")
    state: WorkflowState = Field(..., description="Final workflow lifecycle state")
    status: WorkflowStatus = Field(..., description="Final status indicator")
    context: WorkflowContext = Field(..., description="Final execution context")
    step_results: list[ExecutionResult] = Field(default_factory=list, description="List of step execution results")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Total workflow duration in milliseconds")
    error: str | None = Field(default=None, description="Terminal error message if workflow failed")


class ApprovalRequest(BaseModel):
    """Ticket representing an approval checkpoint requirement."""

    id: UUID = Field(default_factory=uuid4, description="Approval request UUID")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    instance_id: UUID | None = Field(default=None, description="Associated workflow instance UUID")
    step_id: str | None = Field(default=None, description="Step ID requiring approval")
    required_role: str = Field(..., description="Role authorized to approve")
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Request timestamp")
    state: ApprovalState = Field(default=ApprovalState.PENDING, description="Current approval state")
    timeout_at: datetime | None = Field(default=None, description="Timeout expiration timestamp")
    context_snapshot: dict[str, Any] = Field(default_factory=dict, description="Execution context snapshot")
    signature_required: bool = Field(default=False, description="Whether Ed25519 signature is strictly mandatory")
    created_at: datetime | None = Field(default=None, description="Creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Update timestamp")


class ApprovalDecision(BaseModel):
    """Decision payload submitted by an authorized approver."""

    id: UUID = Field(default_factory=uuid4, description="Decision UUID")
    request_id: UUID = Field(..., description="ID of approval request")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    approver_id: str = Field(..., description="ID or username of approver")
    decision: ApprovalState = Field(..., description="APPROVED or REJECTED decision")
    reason: str | None = Field(default=None, description="Optional decision notes or reason")
    signature_hex: str | None = Field(default=None, description="Cryptographic Ed25519 signature in hex format")
    public_key_hex: str | None = Field(default=None, description="Optional public key in hex format for verification")
    decision_data: dict[str, Any] = Field(default_factory=dict, description="Additional context data")
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Timestamp of decision")


class ApprovalDelegation(BaseModel):
    """Delegation granting an approver role to a deputy user for a bounded time window."""

    id: UUID = Field(default_factory=uuid4, description="Delegation UUID")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    delegator_id: str = Field(..., description="Principal ID delegating the role")
    delegatee_id: str = Field(..., description="Principal ID receiving the delegated role")
    role: str = Field(..., description="Role name being delegated")
    valid_from: datetime = Field(..., description="UTC start time of delegation validity")
    valid_until: datetime = Field(..., description="UTC end time of delegation validity")
    is_active: bool = Field(default=True, description="Whether the delegation is active")
    created_at: datetime | None = Field(default=None, description="Creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Update timestamp")


class ApprovalEvent(BaseModel):
    """Event payload published during approval lifecycle transitions."""

    event_type: str = Field(..., description="Type of approval event (requested, decision, expired)")
    request_id: UUID = Field(..., description="ID of approval request")
    instance_id: UUID = Field(..., description="Associated workflow instance UUID")
    step_id: str = Field(..., description="Step ID")
    approver_id: str | None = Field(default=None, description="ID of decision submitter if applicable")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Event timestamp")


class WorkflowSettings(BaseModel):
    """Configuration settings for the Workflow Engine."""

    execution_timeout_seconds: int = Field(default=3600, ge=1, description="Global execution timeout")
    retry_default_attempts: int = Field(default=3, ge=1, description="Default max retry attempts")
    retry_backoff_factor: float = Field(default=2.0, ge=1.0, description="Default retry backoff factor")
    approval_timeout_seconds: int = Field(default=86400, ge=1, description="Default approval timeout")
    worker_count: int = Field(default=4, ge=1, description="Worker thread pool count")
    concurrency_limit: int = Field(default=100, ge=1, description="Maximum concurrent running workflows")
    logging_level: str = Field(default="INFO", description="Logging level string")
    metrics_enabled: bool = Field(default=True, description="Enable metrics collection")
    scheduler_enabled: bool = Field(default=False, description="Enable background scheduler daemon")
    scheduler_poll_interval_seconds: float = Field(default=1.0, ge=0.1, description="Scheduler polling interval")



# ============================================================================
# Scheduling Models (Milestone M5.4)
# ============================================================================


class ScheduleType(enum.StrEnum):
    """Type of scheduling mechanism."""

    CRON = "CRON"
    INTERVAL = "INTERVAL"
    ONCE = "ONCE"


class ScheduleStatus(enum.StrEnum):
    """Operational status of a scheduled workflow job."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"
    COMPLETED = "COMPLETED"


class WorkflowSchedule(BaseModel):
    """Declarative specification and runtime state for a scheduled workflow job."""

    id: UUID = Field(default_factory=uuid4, description="Unique schedule UUID")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    name: str = Field(..., description="Unique human-readable schedule name within tenant")
    definition_id: str = Field(..., description="Target workflow definition ID")
    schedule_type: ScheduleType = Field(default=ScheduleType.INTERVAL, description="Scheduling strategy")
    cron_expression: str | None = Field(default=None, description="5-field cron expression if type is CRON")
    interval_seconds: int | None = Field(default=None, ge=1, description="Execution interval in seconds if INTERVAL")
    run_at: datetime | None = Field(default=None, description="Target execution timestamp if type is ONCE")
    next_run_at: datetime | None = Field(default=None, description="Next calculated UTC execution timestamp")
    last_run_at: datetime | None = Field(default=None, description="Timestamp of the most recent execution trigger")
    last_instance_id: UUID | None = Field(default=None, description="UUID of the instance created on last run")
    status: ScheduleStatus = Field(default=ScheduleStatus.ACTIVE, description="Current schedule lifecycle status")
    initial_context: dict[str, Any] = Field(default_factory=dict, description="Initial context passed to workflow")
    max_runs: int | None = Field(default=None, ge=1, description="Maximum executions before auto-completing")
    run_count: int = Field(default=0, ge=0, description="Total execution triggers performed")
    timezone: str = Field(default="UTC", description="Timezone name for cron calculations")
    created_by: str = Field(default="SYSTEM", description="Principal ID that created the schedule")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")


class ScheduleEvent(BaseModel):
    """Domain event payload for schedule lifecycle transitions."""

    event_type: str = Field(..., description="Event type (created, triggered, paused, resumed, cancelled)")
    schedule_id: UUID = Field(..., description="ID of the workflow schedule")
    tenant_id: str = Field(default="default", description="Tenant ID")
    name: str = Field(..., description="Schedule name")
    definition_id: str = Field(..., description="Target workflow definition ID")
    instance_id: UUID | None = Field(default=None, description="Spawned instance ID if triggered")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Event timestamp")


# ============================================================================
# Governed External Execution Models (Milestone M5.4)
# ============================================================================


class ExternalExecutionStatus(enum.StrEnum):
    """Lifecycle status of a governed external execution."""

    PENDING = "PENDING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class ExternalExecutionRequest(BaseModel):
    """Request payload to execute a governed external operation."""

    id: UUID = Field(default_factory=uuid4, description="Unique execution request UUID")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    operation_type: str = Field(default="CAPABILITY", description="Operation category (e.g. CAPABILITY, HTTP)")
    target: str = Field(..., description="Target capability name, endpoint, or driver action")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Invocation parameter payload")
    timeout_seconds: float = Field(default=30.0, ge=0.1, description="Execution timeout in seconds")
    retry_policy: RetryPolicy | None = Field(default=None, description="Custom retry policy")
    requires_approval: bool = Field(default=False, description="Whether human approval is required prior to execution")
    required_approval_role: str | None = Field(default=None, description="Role authorized to approve if required")
    idempotency_key: str | None = Field(default=None, description="Caller-supplied idempotency key")
    correlation_id: str | None = Field(default=None, description="Trace correlation ID")
    created_by: str = Field(default="SYSTEM", description="Principal ID initiating the request")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Request timestamp")


class ExternalExecutionRecord(BaseModel):
    """Durable record of a governed external execution outcome."""

    id: UUID = Field(default_factory=uuid4, description="Execution record UUID")
    request_id: UUID = Field(..., description="Associated execution request UUID")
    tenant_id: str = Field(default="default", description="Multi-tenant organization identifier")
    operation_type: str = Field(..., description="Operation category")
    target: str = Field(..., description="Target capability name or endpoint")
    status: ExternalExecutionStatus = Field(default=ExternalExecutionStatus.PENDING, description="Execution status")
    status_code: int | None = Field(default=None, description="HTTP status code or exit code if applicable")
    output: Any | None = Field(default=None, description="Sanitized execution output payload")
    error: str | None = Field(default=None, description="Error message if execution failed")
    attempts: int = Field(default=1, ge=1, description="Number of execution attempts made")
    execution_time_ms: float = Field(default=0.0, ge=0.0, description="Duration in milliseconds")
    idempotency_key: str | None = Field(default=None, description="Idempotency key")
    correlation_id: str | None = Field(default=None, description="Correlation ID")
    approval_request_id: UUID | None = Field(default=None, description="Linked approval ticket UUID if approval gated")
    created_by: str = Field(default="SYSTEM", description="Principal ID")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")
    completed_at: datetime | None = Field(default=None, description="Completion timestamp")
