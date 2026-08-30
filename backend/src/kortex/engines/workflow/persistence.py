"""
KORTEX Workflow Relational Persistence & State Hydration Layer.

Implements SQLite-backed persistence for workflow definitions, runtime instances,
and step run execution ledgers via the repository's established IDataStore abstraction.
Guarantees crash recovery, deterministic state hydration, optimistic concurrency locking,
and strict multi-tenant isolation.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel
from kortex.core.exceptions import ResourceNotFoundError
from kortex.core.idempotency import sanitize_for_persistence
from kortex.core.outbox import OutboxStore
from kortex.engines.storage.interfaces import IDataStore
from kortex.engines.workflow.exceptions import (
    ApprovalConflictError,
    ScheduleConflictError,
    WorkflowApprovalError,
    WorkflowPersistenceError,
    WorkflowStateConflictError,
)
from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalDelegation,
    ApprovalRequest,
    ApprovalState,
    CompensationAction,
    ExternalExecutionRecord,
    ExternalExecutionStatus,
    ScheduleStatus,
    ScheduleType,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowPriority,
    WorkflowSchedule,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
    WorkflowTrigger,
)

logger = logging.getLogger("kortex.engines.workflow.persistence")

# M5-A5: how long a schedule may sit claimed (TRIGGERING) before it is
# considered abandoned — the scheduler process that claimed it crashed
# between the claim and `record_schedule_tick` — and is reset back to
# ACTIVE for another tick to claim. Generous relative to how long starting
# one workflow instance is expected to take.
SCHEDULE_CLAIM_LEASE_SECONDS = 120



# ============================================================================
# 1. SQLAlchemy ORM Models (IDataStore / SQLite)
# ============================================================================


class WorkflowDefinitionModel(BaseModel):
    """SQLAlchemy ORM model for persisting Workflow Definitions."""

    __tablename__ = "workflow_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", "version", name="uq_workflow_definition_tenant_id_version"),
    )

    # BaseModel provides id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # We override id to String(64) to support both UUIDs and human-readable IDs
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="default")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, default="MANUAL")
    priority: Mapped[str] = mapped_column(String(32), nullable=False, default="NORMAL")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    steps_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowInstanceModel(BaseModel):
    """SQLAlchemy ORM model for persisting Workflow Instances with optimistic locking."""

    __tablename__ = "workflow_instances"
    __table_args__ = (
        Index("ix_workflow_instances_tenant_state", "tenant_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    definition_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workflow_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    definition_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="default")
    current_step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_step_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    context_json: Mapped[str] = mapped_column(Text, nullable=False)
    compensation_stack_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WorkflowStepRunModel(BaseModel):
    """SQLAlchemy ORM model for the Workflow Step Execution Ledger."""

    __tablename__ = "workflow_step_runs"
    __table_args__ = (
        Index("ix_workflow_step_runs_lookup", "instance_id", "step_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApprovalRequestModel(BaseModel):
    """SQLAlchemy ORM model for persisting Human Approval Requests."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_requests_tenant_state", "tenant_id", "state"),
        Index("ix_approval_requests_instance", "instance_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="default")
    instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    step_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    required_role: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    timeout_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    context_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    signature_required: Mapped[bool] = mapped_column(nullable=False, default=False)


class ApprovalDecisionModel(BaseModel):
    """SQLAlchemy ORM model for persisting Human Approval Decisions."""

    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_approval_decisions_request_id"),
    )

    request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("approval_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="default")
    approver_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature_hex: Mapped[str | None] = mapped_column(String(256), nullable=True)
    decided_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalDelegationModel(BaseModel):
    """SQLAlchemy ORM model for persisting Human Approver Role Delegations."""

    __tablename__ = "approval_delegations"
    __table_args__ = (
        Index("ix_approval_delegations_lookup", "tenant_id", "delegatee_id", "role", "is_active"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="default")
    delegator_id: Mapped[str] = mapped_column(String(64), nullable=False)
    delegatee_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)


class WorkflowScheduleModel(BaseModel):
    """SQLAlchemy ORM model for persisting Workflow Schedules (Milestone M5.4)."""

    __tablename__ = "workflow_schedules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_workflow_schedules_tenant_name"),
        Index("ix_workflow_schedules_due", "status", "next_run_at"),
        Index("ix_workflow_schedules_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="default")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workflow_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    schedule_type: Mapped[str] = mapped_column(String(32), nullable=False, default="INTERVAL")
    cron_expression: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE", index=True)
    initial_context_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    max_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="SYSTEM")


class ExternalExecutionModel(BaseModel):
    """SQLAlchemy ORM model for persisting Governed External Executions (Milestone M5.4)."""

    __tablename__ = "external_executions"
    __table_args__ = (
        Index("ix_external_executions_tenant_status", "tenant_id", "status"),
        Index("ix_external_executions_idempotency", "tenant_id", "idempotency_key"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="default")
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False, default="CAPABILITY")
    target: Mapped[str] = mapped_column(String(256), nullable=False)
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    execution_time_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="SYSTEM")
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ============================================================================
# 2. Serialization & Sanitization Helpers
# ============================================================================



def _sanitize_context_json(context: WorkflowContext) -> str:
    """Serialize workflow context to JSON, omitting raw session tokens and ephemeral credentials."""
    payload = context.model_dump(mode="json")
    payload["session_token"] = None
    sensitive_keys = {
        "session_token",
        "auth_token",
        "token",
        "password",
        "secret",
        "secret_key",
        "credentials",
        "bearer_token",
    }
    if "variables" in payload and isinstance(payload["variables"], dict):
        payload["variables"] = {
            k: v for k, v in payload["variables"].items() if k.lower() not in sensitive_keys
        }
    return json.dumps(payload)


def _deserialize_context(context_json: str) -> WorkflowContext:
    """Deserialize workflow context from JSON safely."""
    try:
        data = json.loads(context_json)
        return WorkflowContext(**data)
    except Exception as e:
        logger.error("Failed to deserialize workflow context JSON: %s", e)
        return WorkflowContext()


def _serialize_compensation_stack(stack: list[CompensationAction]) -> str:
    """Serialize compensation action stack to JSON."""
    return json.dumps([action.model_dump(mode="json") for action in stack])


def _deserialize_compensation_stack(stack_json: str) -> list[CompensationAction]:
    """Deserialize compensation action stack from JSON."""
    try:
        data = json.loads(stack_json)
        return [CompensationAction(**item) for item in data]
    except Exception as e:
        logger.error("Failed to deserialize compensation stack JSON: %s", e)
        return []


def _definition_to_model(definition: WorkflowDefinition, tenant_id: str = "default") -> WorkflowDefinitionModel:
    """Convert WorkflowDefinition domain model to ORM model."""
    steps_data = [step.model_dump(mode="json") for step in definition.steps]
    return WorkflowDefinitionModel(
        id=definition.id,
        tenant_id=definition.tenant_id or tenant_id,
        name=definition.name,
        version=definition.version,
        description=definition.description,
        trigger_type=(
            definition.trigger.value
            if isinstance(definition.trigger, WorkflowTrigger)
            else str(definition.trigger)
        ),
        priority=(
            definition.priority.value
            if isinstance(definition.priority, WorkflowPriority)
            else str(definition.priority)
        ),
        timeout_seconds=definition.timeout_seconds,
        steps_json=json.dumps(steps_data),
    )


def _model_to_definition(row: WorkflowDefinitionModel) -> WorkflowDefinition:
    """Convert ORM model to WorkflowDefinition domain model."""
    raw_steps = json.loads(row.steps_json)
    steps = [WorkflowStep(**step_data) for step_data in raw_steps]
    trigger = (
        WorkflowTrigger(row.trigger_type)
        if row.trigger_type in WorkflowTrigger._value2member_map_
        else WorkflowTrigger.MANUAL
    )
    priority = (
        WorkflowPriority(row.priority)
        if row.priority in WorkflowPriority._value2member_map_
        else WorkflowPriority.NORMAL
    )
    return WorkflowDefinition(
        id=row.id,
        name=row.name,
        version=row.version,
        description=row.description,
        tenant_id=row.tenant_id,
        steps=steps,
        trigger=trigger,
        priority=priority,
        timeout_seconds=row.timeout_seconds,
    )


def _instance_to_model(instance: WorkflowInstance, tenant_id: str = "default") -> WorkflowInstanceModel:
    """Convert WorkflowInstance domain model to ORM model."""
    return WorkflowInstanceModel(
        id=str(instance.id),
        definition_id=instance.definition_id,
        definition_version=instance.definition_version,
        tenant_id=instance.tenant_id or tenant_id,
        current_step_index=instance.current_step_index,
        current_step_id=instance.current_step_id,
        state=instance.state.value if isinstance(instance.state, WorkflowState) else str(instance.state),
        status=instance.status.value if isinstance(instance.status, WorkflowStatus) else str(instance.status),
        context_json=_sanitize_context_json(instance.context),
        compensation_stack_json=_serialize_compensation_stack(instance.compensation_stack),
        trace_id=instance.trace_id,
        version=instance.version,
    )


def _model_to_instance(row: WorkflowInstanceModel) -> WorkflowInstance:
    """Convert ORM model to WorkflowInstance domain model."""
    context = _deserialize_context(row.context_json)
    compensation_stack = _deserialize_compensation_stack(row.compensation_stack_json)
    state = WorkflowState(row.state) if row.state in WorkflowState._value2member_map_ else WorkflowState.CREATED
    status = WorkflowStatus(row.status) if row.status in WorkflowStatus._value2member_map_ else WorkflowStatus.PENDING

    return WorkflowInstance(
        id=UUID(row.id),
        definition_id=row.definition_id,
        definition_version=row.definition_version,
        tenant_id=row.tenant_id,
        current_step_index=row.current_step_index,
        current_step_id=row.current_step_id,
        state=state,
        status=status,
        context=context,
        compensation_stack=compensation_stack,
        trace_id=row.trace_id,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _request_to_model(req: ApprovalRequest, tenant_id: str = "default") -> ApprovalRequestModel:
    """Convert ApprovalRequest domain model to ApprovalRequestModel ORM row."""
    tid = req.tenant_id or tenant_id
    sanitized_ctx = sanitize_for_persistence(req.context_snapshot)
    ctx_json = json.dumps(sanitized_ctx)
    state_val = req.state.value if isinstance(req.state, ApprovalState) else str(req.state)
    return ApprovalRequestModel(
        id=str(req.id),
        tenant_id=tid,
        instance_id=str(req.instance_id) if req.instance_id else None,
        step_id=req.step_id,
        required_role=req.required_role,
        state=state_val,
        timeout_at=req.timeout_at,
        context_snapshot_json=ctx_json,
        signature_required=req.signature_required,
    )


def _model_to_request(row: ApprovalRequestModel) -> ApprovalRequest:
    """Convert ApprovalRequestModel ORM row to ApprovalRequest domain model."""
    try:
        ctx = json.loads(row.context_snapshot_json) if row.context_snapshot_json else {}
    except Exception:
        ctx = {}
    state = ApprovalState(row.state) if row.state in ApprovalState._value2member_map_ else ApprovalState.PENDING
    timeout_at = (
        row.timeout_at.replace(tzinfo=datetime.UTC)
        if row.timeout_at is not None and row.timeout_at.tzinfo is None
        else row.timeout_at
    )
    created_at = (
        row.created_at.replace(tzinfo=datetime.UTC)
        if row.created_at is not None and row.created_at.tzinfo is None
        else row.created_at
    )
    updated_at = (
        row.updated_at.replace(tzinfo=datetime.UTC)
        if row.updated_at is not None and row.updated_at.tzinfo is None
        else row.updated_at
    )
    return ApprovalRequest(
        id=UUID(row.id),
        tenant_id=row.tenant_id,
        instance_id=UUID(row.instance_id) if row.instance_id else None,
        step_id=row.step_id,
        required_role=row.required_role,
        state=state,
        timeout_at=timeout_at,
        context_snapshot=ctx,
        signature_required=row.signature_required,
        created_at=created_at,
        updated_at=updated_at,
    )


def _decision_to_model(decision: ApprovalDecision, tenant_id: str = "default") -> ApprovalDecisionModel:
    """Convert ApprovalDecision domain model to ApprovalDecisionModel ORM row."""
    tid = decision.tenant_id or tenant_id
    dec_val = decision.decision.value if isinstance(decision.decision, ApprovalState) else str(decision.decision)
    return ApprovalDecisionModel(
        id=str(decision.id),
        request_id=str(decision.request_id),
        tenant_id=tid,
        approver_id=decision.approver_id,
        decision=dec_val,
        reason=decision.reason,
        signature_hex=decision.signature_hex,
        decided_at=decision.decided_at,
    )


def _model_to_decision(row: ApprovalDecisionModel) -> ApprovalDecision:
    """Convert ApprovalDecisionModel ORM row to ApprovalDecision domain model."""
    dec = ApprovalState(row.decision) if row.decision in ApprovalState._value2member_map_ else ApprovalState.APPROVED
    decided_at = (
        row.decided_at.replace(tzinfo=datetime.UTC)
        if row.decided_at is not None and row.decided_at.tzinfo is None
        else row.decided_at
    )
    return ApprovalDecision(
        id=UUID(row.id),
        request_id=UUID(row.request_id),
        tenant_id=row.tenant_id,
        approver_id=row.approver_id,
        decision=dec,
        reason=row.reason,
        signature_hex=row.signature_hex,
        decided_at=decided_at,
    )


def _delegation_to_model(delegation: ApprovalDelegation, tenant_id: str = "default") -> ApprovalDelegationModel:
    """Convert ApprovalDelegation domain model to ApprovalDelegationModel ORM row."""
    tid = delegation.tenant_id or tenant_id
    return ApprovalDelegationModel(
        id=str(delegation.id),
        tenant_id=tid,
        delegator_id=delegation.delegator_id,
        delegatee_id=delegation.delegatee_id,
        role=delegation.role,
        valid_from=delegation.valid_from,
        valid_until=delegation.valid_until,
        is_active=delegation.is_active,
    )


def _model_to_delegation(row: ApprovalDelegationModel) -> ApprovalDelegation:
    """Convert ApprovalDelegationModel ORM row to ApprovalDelegation domain model."""
    valid_from = (
        row.valid_from.replace(tzinfo=datetime.UTC)
        if row.valid_from is not None and row.valid_from.tzinfo is None
        else row.valid_from
    )
    valid_until = (
        row.valid_until.replace(tzinfo=datetime.UTC)
        if row.valid_until is not None and row.valid_until.tzinfo is None
        else row.valid_until
    )
    return ApprovalDelegation(
        id=UUID(row.id),
        tenant_id=row.tenant_id,
        delegator_id=row.delegator_id,
        delegatee_id=row.delegatee_id,
        role=row.role,
        valid_from=valid_from,
        valid_until=valid_until,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _schedule_to_model(schedule: WorkflowSchedule, tenant_id: str = "default") -> WorkflowScheduleModel:
    """Convert WorkflowSchedule domain model to WorkflowScheduleModel ORM row."""
    tid = schedule.tenant_id or tenant_id
    type_val = (
        schedule.schedule_type.value
        if isinstance(schedule.schedule_type, ScheduleType)
        else str(schedule.schedule_type)
    )
    status_val = (
        schedule.status.value
        if isinstance(schedule.status, ScheduleStatus)
        else str(schedule.status)
    )
    ctx_json = json.dumps(sanitize_for_persistence(schedule.initial_context or {}))
    return WorkflowScheduleModel(
        id=str(schedule.id),
        tenant_id=tid,
        name=schedule.name,
        definition_id=schedule.definition_id,
        schedule_type=type_val,
        cron_expression=schedule.cron_expression,
        interval_seconds=schedule.interval_seconds,
        run_at=schedule.run_at,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        last_instance_id=str(schedule.last_instance_id) if schedule.last_instance_id else None,
        status=status_val,
        initial_context_json=ctx_json,
        max_runs=schedule.max_runs,
        run_count=schedule.run_count,
        timezone=schedule.timezone,
        created_by=schedule.created_by,
    )


def _model_to_schedule(row: WorkflowScheduleModel) -> WorkflowSchedule:
    """Convert WorkflowScheduleModel ORM row to WorkflowSchedule domain model."""
    stype = (
        ScheduleType(row.schedule_type)
        if row.schedule_type in ScheduleType._value2member_map_
        else ScheduleType.INTERVAL
    )
    status = (
        ScheduleStatus(row.status)
        if row.status in ScheduleStatus._value2member_map_
        else ScheduleStatus.ACTIVE
    )
    try:
        ctx = json.loads(row.initial_context_json) if row.initial_context_json else {}
    except Exception:
        ctx = {}

    def _utc(dt: datetime.datetime | None) -> datetime.datetime | None:
        if dt is not None and dt.tzinfo is None:
            return dt.replace(tzinfo=datetime.UTC)
        return dt

    return WorkflowSchedule(
        id=UUID(row.id),
        tenant_id=row.tenant_id,
        name=row.name,
        definition_id=row.definition_id,
        schedule_type=stype,
        cron_expression=row.cron_expression,
        interval_seconds=row.interval_seconds,
        run_at=_utc(row.run_at),
        next_run_at=_utc(row.next_run_at),
        last_run_at=_utc(row.last_run_at),
        last_instance_id=UUID(row.last_instance_id) if row.last_instance_id else None,
        status=status,
        initial_context=ctx,
        max_runs=row.max_runs,
        run_count=row.run_count,
        timezone=row.timezone,
        created_by=row.created_by,
        created_at=_utc(row.created_at) or datetime.datetime.now(datetime.UTC),
        updated_at=_utc(row.updated_at) or datetime.datetime.now(datetime.UTC),
    )


def _execution_to_model(
    record: ExternalExecutionRecord,
    parameters: dict[str, Any] | None = None,
    tenant_id: str = "default",
) -> ExternalExecutionModel:
    """Convert ExternalExecutionRecord domain model to ExternalExecutionModel ORM row."""
    tid = record.tenant_id or tenant_id
    status_val = (
        record.status.value
        if isinstance(record.status, ExternalExecutionStatus)
        else str(record.status)
    )
    params_json = json.dumps(sanitize_for_persistence(parameters or {}))
    out_json = json.dumps(sanitize_for_persistence(record.output)) if record.output is not None else None
    return ExternalExecutionModel(
        id=str(record.id),
        tenant_id=tid,
        operation_type=record.operation_type,
        target=record.target,
        parameters_json=params_json,
        status=status_val,
        status_code=record.status_code,
        output_json=out_json,
        error_message=record.error,
        attempts=record.attempts,
        execution_time_ms=record.execution_time_ms,
        idempotency_key=record.idempotency_key,
        correlation_id=record.correlation_id,
        approval_request_id=str(record.approval_request_id) if record.approval_request_id else None,
        created_by=record.created_by,
        completed_at=record.completed_at,
    )


def _model_to_execution(row: ExternalExecutionModel) -> ExternalExecutionRecord:
    """Convert ExternalExecutionModel ORM row to ExternalExecutionRecord domain model."""
    status = (
        ExternalExecutionStatus(row.status)
        if row.status in ExternalExecutionStatus._value2member_map_
        else ExternalExecutionStatus.PENDING
    )
    out = None
    if row.output_json:
        try:
            out = json.loads(row.output_json)
        except Exception:
            out = row.output_json

    def _utc(dt: datetime.datetime | None) -> datetime.datetime | None:
        if dt is not None and dt.tzinfo is None:
            return dt.replace(tzinfo=datetime.UTC)
        return dt

    return ExternalExecutionRecord(
        id=UUID(row.id),
        request_id=UUID(row.id),
        tenant_id=row.tenant_id,
        operation_type=row.operation_type,
        target=row.target,
        status=status,
        status_code=row.status_code,
        output=out,
        error=row.error_message,
        attempts=row.attempts,
        execution_time_ms=row.execution_time_ms,
        idempotency_key=row.idempotency_key,
        correlation_id=row.correlation_id,
        approval_request_id=UUID(row.approval_request_id) if row.approval_request_id else None,
        created_by=row.created_by,
        created_at=_utc(row.created_at) or datetime.datetime.now(datetime.UTC),
        updated_at=_utc(row.updated_at) or datetime.datetime.now(datetime.UTC),
        completed_at=_utc(row.completed_at),
    )


# ============================================================================
# 3. Workflow Relational Store (IDataStore Interface)
# ============================================================================



class WorkflowStore:
    """Encapsulates all relational database operations for WorkflowEngine via IDataStore."""

    def __init__(self, data_store: IDataStore) -> None:
        self._data_store = data_store
        logger.debug("WorkflowStore initialized with IDataStore.")

    # -- Definition Persistence ----------------------------------------------

    async def save_definition(self, definition: WorkflowDefinition, tenant_id: str = "default") -> None:
        """Persist or update a WorkflowDefinition in the database."""
        tid = definition.tenant_id or tenant_id

        async def _action(session: AsyncSession) -> None:
            existing = await session.scalar(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.id == definition.id,
                    WorkflowDefinitionModel.tenant_id == tid,
                )
            )
            model = _definition_to_model(definition, tenant_id=tid)
            if existing is None:
                session.add(model)
            else:
                existing.name = model.name
                existing.version = model.version
                existing.description = model.description
                existing.trigger_type = model.trigger_type
                existing.priority = model.priority
                existing.timeout_seconds = model.timeout_seconds
                existing.steps_json = model.steps_json

        try:
            await self._data_store.execute_in_transaction(_action)
            logger.info("Saved workflow definition '%s' (v%s) for tenant '%s'", definition.id, definition.version, tid)
        except IntegrityError:
            logger.debug("Workflow definition '%s' already persisted; skipping duplicate insert.", definition.id)
        except Exception as e:
            logger.error("Failed to save workflow definition '%s': %s", definition.id, e)
            raise WorkflowPersistenceError(f"Failed to save workflow definition '{definition.id}': {e}") from e

    async def get_definition(self, definition_id: str, tenant_id: str | None = None) -> WorkflowDefinition | None:
        """Retrieve a WorkflowDefinition by ID, optionally filtered by tenant."""
        async def _action(session: AsyncSession) -> WorkflowDefinitionModel | None:
            stmt = select(WorkflowDefinitionModel).where(WorkflowDefinitionModel.id == definition_id)
            if tenant_id:
                stmt = stmt.where(WorkflowDefinitionModel.tenant_id == tenant_id)
            res: WorkflowDefinitionModel | None = await session.scalar(stmt)
            return res

        try:
            row = await self._data_store.execute_in_transaction(_action)
            if row is None:
                return None
            return _model_to_definition(row)
        except Exception as e:
            logger.error("Failed to retrieve workflow definition '%s': %s", definition_id, e)
            raise WorkflowPersistenceError(f"Failed to retrieve workflow definition '{definition_id}': {e}") from e

    async def list_definitions(self, tenant_id: str | None = None) -> list[WorkflowDefinition]:
        """List all WorkflowDefinitions, optionally filtered by tenant."""
        async def _action(session: AsyncSession) -> list[WorkflowDefinitionModel]:
            stmt = select(WorkflowDefinitionModel)
            if tenant_id:
                stmt = stmt.where(WorkflowDefinitionModel.tenant_id == tenant_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

        try:
            rows = await self._data_store.execute_in_transaction(_action)
            return [_model_to_definition(row) for row in rows]
        except Exception as e:
            logger.error("Failed to list workflow definitions: %s", e)
            raise WorkflowPersistenceError(f"Failed to list workflow definitions: {e}") from e

    # -- Instance Persistence ------------------------------------------------

    async def save_instance(
        self,
        instance: WorkflowInstance,
        definition: WorkflowDefinition | None = None,
        tenant_id: str = "default",
    ) -> None:
        """Persist a newly created WorkflowInstance to the database."""
        tid = instance.tenant_id or tenant_id

        async def _action(session: AsyncSession) -> None:
            # Ensure referenced definition exists in database before inserting instance
            if definition is not None:
                def_row = await session.scalar(
                    select(WorkflowDefinitionModel).where(
                        WorkflowDefinitionModel.id == definition.id,
                        WorkflowDefinitionModel.tenant_id == (definition.tenant_id or tid),
                    )
                )
                if def_row is None:
                    session.add(_definition_to_model(definition, tenant_id=tid))
                    await session.flush()

            model = _instance_to_model(instance, tenant_id=tid)
            session.add(model)

        try:
            await self._data_store.execute_in_transaction(_action)
            logger.info("Persisted new workflow instance '%s' for tenant '%s'", instance.id, tid)
        except IntegrityError as e:
            logger.error("Integrity error saving workflow instance '%s': %s", instance.id, e)
            raise WorkflowPersistenceError(f"Integrity violation saving workflow instance '{instance.id}': {e}") from e
        except Exception as e:
            logger.error("Failed to save workflow instance '%s': %s", instance.id, e)
            raise WorkflowPersistenceError(f"Failed to save workflow instance '{instance.id}': {e}") from e

    async def update_instance(self, instance: WorkflowInstance, tenant_id: str | None = None) -> None:
        """Update an existing WorkflowInstance in the database using optimistic concurrency locking."""
        current_version = instance.version
        next_version = current_version + 1

        state_val = instance.state.value if isinstance(instance.state, WorkflowState) else str(instance.state)
        status_val = instance.status.value if isinstance(instance.status, WorkflowStatus) else str(instance.status)

        async def _action(session: AsyncSession) -> int:
            stmt = (
                update(WorkflowInstanceModel)
                .where(
                    WorkflowInstanceModel.id == str(instance.id),
                    WorkflowInstanceModel.version == current_version,
                )
                .values(
                    current_step_index=instance.current_step_index,
                    current_step_id=instance.current_step_id,
                    state=state_val,
                    status=status_val,
                    context_json=_sanitize_context_json(instance.context),
                    compensation_stack_json=_serialize_compensation_stack(instance.compensation_stack),
                    version=next_version,
                    updated_at=datetime.datetime.now(datetime.UTC),
                )
            )
            if tenant_id:
                stmt = stmt.where(WorkflowInstanceModel.tenant_id == tenant_id)
            result = await session.execute(stmt)
            cursor_res = cast(CursorResult[Any], result)
            return cursor_res.rowcount

        try:
            rows_updated = await self._data_store.execute_in_transaction(_action)
            if rows_updated == 0:
                # Check if record exists with different version
                existing = await self.get_instance(instance.id)
                if existing is None:
                    raise ResourceNotFoundError(f"Workflow instance '{instance.id}' not found.")
                raise WorkflowStateConflictError(
                    f"Optimistic concurrency conflict on workflow instance '{instance.id}': "
                    f"expected version {current_version}, but persistent version is {existing.version}."
                )
            instance.version = next_version
            logger.debug(
                "Updated workflow instance '%s' to state '%s' (v%d -> v%d)",
                instance.id,
                instance.state.value,
                current_version,
                next_version,
            )
        except (WorkflowStateConflictError, ResourceNotFoundError):
            raise
        except Exception as e:
            logger.error("Failed to update workflow instance '%s': %s", instance.id, e)
            raise WorkflowPersistenceError(f"Failed to update workflow instance '{instance.id}': {e}") from e

    async def get_instance(self, instance_id: UUID, tenant_id: str | None = None) -> WorkflowInstance | None:
        """Retrieve a WorkflowInstance by UUID, strictly enforcing tenant boundary if provided."""
        async def _action(session: AsyncSession) -> WorkflowInstanceModel | None:
            stmt = select(WorkflowInstanceModel).where(WorkflowInstanceModel.id == str(instance_id))
            if tenant_id:
                stmt = stmt.where(WorkflowInstanceModel.tenant_id == tenant_id)
            res: WorkflowInstanceModel | None = await session.scalar(stmt)
            return res

        try:
            row = await self._data_store.execute_in_transaction(_action)
            if row is None:
                return None
            return _model_to_instance(row)
        except Exception as e:
            logger.error("Failed to retrieve workflow instance '%s': %s", instance_id, e)
            raise WorkflowPersistenceError(f"Failed to retrieve workflow instance '{instance_id}': {e}") from e

    async def list_instances(
        self,
        tenant_id: str | None = None,
        state_filter: WorkflowState | None = None,
    ) -> list[WorkflowInstance]:
        """List WorkflowInstances matching optional tenant and state filters."""
        async def _action(session: AsyncSession) -> list[WorkflowInstanceModel]:
            stmt = select(WorkflowInstanceModel)
            if tenant_id:
                stmt = stmt.where(WorkflowInstanceModel.tenant_id == tenant_id)
            if state_filter:
                state_val = state_filter.value if isinstance(state_filter, WorkflowState) else str(state_filter)
                stmt = stmt.where(WorkflowInstanceModel.state == state_val)
            result = await session.execute(stmt)
            return list(result.scalars().all())

        try:
            rows = await self._data_store.execute_in_transaction(_action)
            return [_model_to_instance(row) for row in rows]
        except Exception as e:
            logger.error("Failed to list workflow instances: %s", e)
            raise WorkflowPersistenceError(f"Failed to list workflow instances: {e}") from e

    async def get_unfinalized_instances(self, tenant_id: str | None = None) -> list[WorkflowInstance]:
        """Retrieve all non-terminal workflow instances for recovery hydration."""
        terminal_states = [WorkflowState.COMPLETED.value, WorkflowState.FAILED.value, WorkflowState.CANCELLED.value]

        async def _action(session: AsyncSession) -> list[WorkflowInstanceModel]:
            stmt = select(WorkflowInstanceModel).where(
                WorkflowInstanceModel.state.notin_(terminal_states)
            )
            if tenant_id:
                stmt = stmt.where(WorkflowInstanceModel.tenant_id == tenant_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

        try:
            rows = await self._data_store.execute_in_transaction(_action)
            return [_model_to_instance(row) for row in rows]
        except Exception as e:
            logger.error("Failed to query unfinalized workflow instances: %s", e)
            raise WorkflowPersistenceError(f"Failed to query unfinalized workflow instances: {e}") from e

    # -- Step Run Execution Ledger -------------------------------------------

    async def record_step_run_start(self, instance_id: UUID, step_id: str, attempt: int = 1) -> str:
        """Record the start of a step execution attempt in the durable ledger."""
        run_id = str(uuid.uuid4())

        async def _action(session: AsyncSession) -> None:
            model = WorkflowStepRunModel(
                id=run_id,
                instance_id=str(instance_id),
                step_id=step_id,
                attempt=attempt,
                status="RUNNING",
                started_at=datetime.datetime.now(datetime.UTC),
            )
            session.add(model)

        try:
            await self._data_store.execute_in_transaction(_action)
            return run_id
        except Exception as e:
            logger.warning("Failed to record step run start for instance '%s' step '%s': %s", instance_id, step_id, e)
            return run_id

    async def record_step_run_complete(
        self,
        run_id: str,
        status: str,
        output: object = None,
        error: str | None = None,
    ) -> None:
        """Record the completion or failure of a step execution attempt."""
        output_str = None
        if output is not None:
            try:
                output_str = json.dumps(output) if not isinstance(output, str) else output
            except Exception:
                output_str = str(output)

        async def _action(session: AsyncSession) -> None:
            stmt = (
                update(WorkflowStepRunModel)
                .where(WorkflowStepRunModel.id == run_id)
                .values(
                    status=status,
                    completed_at=datetime.datetime.now(datetime.UTC),
                    output_json=output_str,
                    error_message=error,
                    updated_at=datetime.datetime.now(datetime.UTC),
                )
            )
            await session.execute(stmt)

        try:
            await self._data_store.execute_in_transaction(_action)
        except Exception as e:
            logger.warning("Failed to record step run complete for run '%s': %s", run_id, e)

    async def record_step_complete_and_advance_instance(
        self,
        run_id: str | None,
        instance: WorkflowInstance,
        output: object = None,
        error: str | None = None,
    ) -> None:
        """Atomically record step run completion, advance step index, and update instance version."""
        output_str = None
        if output is not None:
            try:
                output_str = json.dumps(output) if not isinstance(output, str) else output
            except Exception:
                output_str = str(output)

        current_version = instance.version
        next_version = current_version + 1
        instance.version = next_version

        state_val = instance.state.value if isinstance(instance.state, WorkflowState) else str(instance.state)
        status_val = instance.status.value if isinstance(instance.status, WorkflowStatus) else str(instance.status)

        async def _action(session: AsyncSession) -> None:
            if run_id:
                await session.execute(
                    update(WorkflowStepRunModel)
                    .where(WorkflowStepRunModel.id == run_id)
                    .values(
                        status="COMPLETED" if error is None else "FAILED",
                        completed_at=datetime.datetime.now(datetime.UTC),
                        output_json=output_str,
                        error_message=error,
                        updated_at=datetime.datetime.now(datetime.UTC),
                    )
                )

            stmt = (
                update(WorkflowInstanceModel)
                .where(
                    WorkflowInstanceModel.id == str(instance.id),
                    WorkflowInstanceModel.version == current_version,
                )
                .values(
                    current_step_index=instance.current_step_index,
                    current_step_id=instance.current_step_id,
                    state=state_val,
                    status=status_val,
                    context_json=_sanitize_context_json(instance.context),
                    compensation_stack_json=_serialize_compensation_stack(instance.compensation_stack),
                    version=next_version,
                    updated_at=datetime.datetime.now(datetime.UTC),
                )
            )
            result = await session.execute(stmt)
            cursor_res = cast(CursorResult[Any], result)
            if cursor_res.rowcount == 0:
                raise WorkflowStateConflictError(
                    f"Optimistic lock conflict on instance '{instance.id}': expected version {current_version}"
                )

        try:
            await self._data_store.execute_in_transaction(_action)
        except WorkflowStateConflictError:
            raise
        except Exception as e:
            logger.error("Failed atomic step completion and instance update for '%s': %s", instance.id, e)
            raise WorkflowPersistenceError(f"Failed to record step completion for '{instance.id}': {e}") from e

    async def record_step_complete_and_update_state(
        self,
        run_id: str | None,
        instance: WorkflowInstance,
        output: object = None,
        error: str | None = None,
    ) -> None:
        """Atomically record step run completion and update instance state/version (e.g. WAITING or FAILED)."""
        output_str = None
        if output is not None:
            try:
                output_str = json.dumps(output) if not isinstance(output, str) else output
            except Exception:
                output_str = str(output)

        current_version = instance.version
        next_version = current_version + 1
        instance.version = next_version

        state_val = instance.state.value if isinstance(instance.state, WorkflowState) else str(instance.state)
        status_val = instance.status.value if isinstance(instance.status, WorkflowStatus) else str(instance.status)

        async def _action(session: AsyncSession) -> None:
            if run_id:
                await session.execute(
                    update(WorkflowStepRunModel)
                    .where(WorkflowStepRunModel.id == run_id)
                    .values(
                        status="COMPLETED" if error is None else "FAILED",
                        completed_at=datetime.datetime.now(datetime.UTC),
                        output_json=output_str,
                        error_message=error,
                        updated_at=datetime.datetime.now(datetime.UTC),
                    )
                )

            stmt = (
                update(WorkflowInstanceModel)
                .where(
                    WorkflowInstanceModel.id == str(instance.id),
                    WorkflowInstanceModel.version == current_version,
                )
                .values(
                    current_step_index=instance.current_step_index,
                    current_step_id=instance.current_step_id,
                    state=state_val,
                    status=status_val,
                    context_json=_sanitize_context_json(instance.context),
                    compensation_stack_json=_serialize_compensation_stack(instance.compensation_stack),
                    version=next_version,
                    updated_at=datetime.datetime.now(datetime.UTC),
                )
            )
            result = await session.execute(stmt)
            cursor_res = cast(CursorResult[Any], result)
            if cursor_res.rowcount == 0:
                raise WorkflowStateConflictError(
                    f"Optimistic lock conflict on instance '{instance.id}': expected version {current_version}"
                )

        try:
            await self._data_store.execute_in_transaction(_action)
        except WorkflowStateConflictError:
            raise
        except Exception as e:
            logger.error("Failed atomic step completion and state update for '%s': %s", instance.id, e)
            raise WorkflowPersistenceError(f"Failed to update workflow state for '{instance.id}': {e}") from e

    async def list_step_runs(self, instance_id: UUID) -> list[dict[str, Any]]:
        """Retrieve the execution ledger of all step runs for an instance."""
        async def _action(session: AsyncSession) -> list[WorkflowStepRunModel]:
            stmt = (
                select(WorkflowStepRunModel)
                .where(WorkflowStepRunModel.instance_id == str(instance_id))
                .order_by(WorkflowStepRunModel.started_at)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

        try:
            rows = await self._data_store.execute_in_transaction(_action)
            return [
                {
                    "run_id": row.id,
                    "instance_id": row.instance_id,
                    "step_id": row.step_id,
                    "attempt": row.attempt,
                    "status": row.status,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    "output": row.output_json,
                    "error": row.error_message,
                }
                for row in rows
            ]
        except Exception as e:
            logger.error("Failed to list step runs for instance '%s': %s", instance_id, e)
            return []


# ============================================================================
# 4. Approval Relational Store (IDataStore Interface)
# ============================================================================


class ApprovalStore:
    """Encapsulates all relational database operations for human approvals via IDataStore."""

    def __init__(self, data_store: IDataStore) -> None:
        self._data_store = data_store
        logger.debug("ApprovalStore initialized with IDataStore.")

    async def save_request(
        self,
        request: ApprovalRequest,
        tenant_id: str = "default",
        outbox_store: OutboxStore | None = None,
    ) -> None:
        """Persist or update an ApprovalRequest ticket in the database with optional atomic outbox staging."""
        tid = request.tenant_id or tenant_id

        async def _action(session: AsyncSession) -> None:
            existing = await session.scalar(
                select(ApprovalRequestModel).where(
                    ApprovalRequestModel.id == str(request.id),
                    ApprovalRequestModel.tenant_id == tid,
                )
            )
            model = _request_to_model(request, tenant_id=tid)
            if existing is None:
                session.add(model)
                if outbox_store is not None:
                    outbox_store.stage_event_in_session(
                        session=session,
                        tenant_id=tid,
                        topic="workflow.approval.created",
                        payload={
                            "request_id": str(request.id),
                            "instance_id": str(request.instance_id) if request.instance_id else None,
                            "step_id": request.step_id,
                            "required_role": request.required_role,
                            "tenant_id": tid,
                        },
                    )
            else:
                existing.state = model.state
                existing.timeout_at = model.timeout_at
                existing.context_snapshot_json = model.context_snapshot_json
                existing.signature_required = model.signature_required
                existing.updated_at = datetime.datetime.now(datetime.UTC)

        await self._data_store.execute_in_transaction(_action)

    async def get_request(self, request_id: UUID | str, tenant_id: str | None = None) -> ApprovalRequest | None:
        """Retrieve an approval request ticket by ID with optional tenant isolation."""
        r_id = str(request_id)

        async def _action(session: AsyncSession) -> ApprovalRequestModel | None:
            stmt = select(ApprovalRequestModel).where(ApprovalRequestModel.id == r_id)
            if tenant_id is not None:
                stmt = stmt.where(ApprovalRequestModel.tenant_id == tenant_id)
            return cast(ApprovalRequestModel | None, await session.scalar(stmt))

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_request(row) if row else None

    async def get_request_by_step(
        self, instance_id: UUID | str, step_id: str, tenant_id: str | None = None
    ) -> ApprovalRequest | None:
        """Retrieve the pending approval request for a workflow instance and step."""
        inst_id = str(instance_id)

        async def _action(session: AsyncSession) -> ApprovalRequestModel | None:
            stmt = (
                select(ApprovalRequestModel)
                .where(
                    ApprovalRequestModel.instance_id == inst_id,
                    ApprovalRequestModel.step_id == step_id,
                )
                .order_by(ApprovalRequestModel.created_at.desc())
            )
            if tenant_id is not None:
                stmt = stmt.where(ApprovalRequestModel.tenant_id == tenant_id)
            return cast(ApprovalRequestModel | None, await session.scalar(stmt))

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_request(row) if row else None

    async def get_request_by_instance(
        self, instance_id: UUID | str, tenant_id: str | None = None
    ) -> ApprovalRequest | None:
        """Retrieve the latest approval request for a workflow instance."""
        inst_id = str(instance_id)

        async def _action(session: AsyncSession) -> ApprovalRequestModel | None:
            stmt = (
                select(ApprovalRequestModel)
                .where(
                    ApprovalRequestModel.instance_id == inst_id,
                )
                .order_by(ApprovalRequestModel.created_at.desc())
            )
            if tenant_id is not None:
                stmt = stmt.where(ApprovalRequestModel.tenant_id == tenant_id)
            return cast(ApprovalRequestModel | None, await session.scalar(stmt))

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_request(row) if row else None

    async def list_requests(
        self,
        tenant_id: str = "default",
        role_filter: str | None = None,
        state_filter: str | None = None,
    ) -> list[ApprovalRequest]:
        """List approval requests matching criteria within a tenant boundary."""
        async def _action(session: AsyncSession) -> list[ApprovalRequestModel]:
            stmt = select(ApprovalRequestModel).where(ApprovalRequestModel.tenant_id == tenant_id)
            if role_filter is not None:
                stmt = stmt.where(ApprovalRequestModel.required_role == role_filter)
            if state_filter is not None:
                stmt = stmt.where(ApprovalRequestModel.state == state_filter)
            stmt = stmt.order_by(ApprovalRequestModel.created_at.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        return [_model_to_request(row) for row in rows]

    async def save_decision(self, decision: ApprovalDecision, tenant_id: str = "default") -> None:
        """Persist an ApprovalDecision record."""
        tid = decision.tenant_id or tenant_id
        model = _decision_to_model(decision, tenant_id=tid)

        async def _action(session: AsyncSession) -> None:
            session.add(model)

        await self._data_store.execute_in_transaction(_action)

    async def get_decision(self, request_id: UUID | str, tenant_id: str | None = None) -> ApprovalDecision | None:
        """Retrieve an approval decision by request ID."""
        r_id = str(request_id)

        async def _action(session: AsyncSession) -> ApprovalDecisionModel | None:
            stmt = select(ApprovalDecisionModel).where(ApprovalDecisionModel.request_id == r_id)
            if tenant_id is not None:
                stmt = stmt.where(ApprovalDecisionModel.tenant_id == tenant_id)
            return cast(ApprovalDecisionModel | None, await session.scalar(stmt))

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_decision(row) if row else None

    async def save_delegation(self, delegation: ApprovalDelegation, tenant_id: str = "default") -> None:
        """Persist a new role delegation."""
        tid = delegation.tenant_id or tenant_id
        model = _delegation_to_model(delegation, tenant_id=tid)

        async def _action(session: AsyncSession) -> None:
            session.add(model)

        await self._data_store.execute_in_transaction(_action)

    async def get_active_delegation(
        self,
        tenant_id: str,
        delegatee_id: str,
        role: str,
        at_time: datetime.datetime | None = None,
    ) -> ApprovalDelegation | None:
        """Query for an active role delegation valid at the given timestamp."""
        now_dt = at_time or datetime.datetime.now(datetime.UTC)

        async def _action(session: AsyncSession) -> ApprovalDelegationModel | None:
            stmt = (
                select(ApprovalDelegationModel)
                .where(
                    ApprovalDelegationModel.tenant_id == tenant_id,
                    ApprovalDelegationModel.delegatee_id == delegatee_id,
                    ApprovalDelegationModel.role == role,
                    ApprovalDelegationModel.is_active.is_(True),
                    ApprovalDelegationModel.valid_from <= now_dt,
                    ApprovalDelegationModel.valid_until >= now_dt,
                )
                .order_by(ApprovalDelegationModel.created_at.desc())
                .limit(1)
            )
            return cast(ApprovalDelegationModel | None, await session.scalar(stmt))

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_delegation(row) if row else None

    async def list_delegations(
        self,
        tenant_id: str = "default",
        delegator_id: str | None = None,
        delegatee_id: str | None = None,
        is_active: bool | None = None,
    ) -> list[ApprovalDelegation]:
        """List delegations matching criteria within a tenant boundary."""
        async def _action(session: AsyncSession) -> list[ApprovalDelegationModel]:
            stmt = select(ApprovalDelegationModel).where(ApprovalDelegationModel.tenant_id == tenant_id)
            if delegator_id is not None:
                stmt = stmt.where(ApprovalDelegationModel.delegator_id == delegator_id)
            if delegatee_id is not None:
                stmt = stmt.where(ApprovalDelegationModel.delegatee_id == delegatee_id)
            if is_active is not None:
                stmt = stmt.where(ApprovalDelegationModel.is_active == is_active)
            stmt = stmt.order_by(ApprovalDelegationModel.created_at.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        return [_model_to_delegation(row) for row in rows]

    async def get_expired_pending_requests(
        self,
        before_time: datetime.datetime | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[ApprovalRequest]:
        """Query pending approval requests whose timeout_at has passed."""
        now_dt = before_time or datetime.datetime.now(datetime.UTC)

        async def _action(session: AsyncSession) -> list[ApprovalRequestModel]:
            stmt = select(ApprovalRequestModel).where(
                ApprovalRequestModel.state == "PENDING",
                ApprovalRequestModel.timeout_at.is_not(None),
                ApprovalRequestModel.timeout_at < now_dt,
            )
            if tenant_id is not None:
                stmt = stmt.where(ApprovalRequestModel.tenant_id == tenant_id)
            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        return [_model_to_request(row) for row in rows]

    async def atomic_submit_decision(
        self,
        decision: ApprovalDecision,
        tenant_id: str,
        outbox_store: OutboxStore | None = None,
    ) -> ApprovalRequest:
        """Atomically transition ticket state, insert decision record, and stage outbox event."""
        r_id = str(decision.request_id)
        dec_val = decision.decision.value if isinstance(decision.decision, ApprovalState) else str(decision.decision)

        async def _action(session: AsyncSession) -> ApprovalRequestModel:
            # 1. Fetch ticket under tenant
            ticket = await session.scalar(
                select(ApprovalRequestModel).where(
                    ApprovalRequestModel.id == r_id,
                    ApprovalRequestModel.tenant_id == tenant_id,
                )
            )
            if ticket is None:
                raise WorkflowApprovalError(f"Approval request ticket '{r_id}' not found.")

            if ticket.state != "PENDING":
                raise ApprovalConflictError(
                    f"Approval request ticket '{r_id}' is already in state '{ticket.state}'."
                )

            # 2. Update state to decision
            ticket.state = dec_val
            ticket.updated_at = datetime.datetime.now(datetime.UTC)

            # 3. Insert decision record
            dec_model = _decision_to_model(decision, tenant_id=tenant_id)
            session.add(dec_model)

            # 4. Stage event in outbox if store provided
            if outbox_store is not None:
                outbox_store.stage_event_in_session(
                    session=session,
                    tenant_id=tenant_id,
                    topic="workflow.approval.decided",
                    payload={
                        "request_id": r_id,
                        "instance_id": ticket.instance_id,
                        "step_id": ticket.step_id,
                        "decision": dec_val,
                        "approver_id": decision.approver_id,
                        "tenant_id": tenant_id,
                    },
                )
            await session.flush()
            return ticket

        try:
            updated_row = await self._data_store.execute_in_transaction(_action)
            return _model_to_request(updated_row)
        except IntegrityError as e:
            raise ApprovalConflictError(
                f"Concurrent or duplicate decision submitted for approval request '{r_id}'."
            ) from e

    async def atomic_expire_request(
        self,
        request_id: UUID | str,
        tenant_id: str,
        outbox_store: OutboxStore | None = None,
    ) -> ApprovalRequest | None:
        """Atomically transition a pending ticket to EXPIRED and stage outbox event."""
        r_id = str(request_id)

        async def _action(session: AsyncSession) -> ApprovalRequestModel | None:
            ticket = await session.scalar(
                select(ApprovalRequestModel).where(
                    ApprovalRequestModel.id == r_id,
                    ApprovalRequestModel.tenant_id == tenant_id,
                    ApprovalRequestModel.state == "PENDING",
                )
            )
            if ticket is None:
                return None
            ticket.state = "EXPIRED"
            ticket.updated_at = datetime.datetime.now(datetime.UTC)

            if outbox_store is not None:
                outbox_store.stage_event_in_session(
                    session=session,
                    tenant_id=tenant_id,
                    topic="workflow.approval.expired",
                    payload={
                        "request_id": r_id,
                        "instance_id": ticket.instance_id,
                        "step_id": ticket.step_id,
                        "tenant_id": tenant_id,
                    },
                )
            await session.flush()
            return ticket

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_request(row) if row else None


# ============================================================================
# 5. Workflow Scheduler Relational Store (IDataStore Interface)
# ============================================================================


class SchedulerStore:
    """Encapsulates relational persistence for Workflow Schedules (Milestone M5.4)."""

    def __init__(self, data_store: IDataStore) -> None:
        self._data_store = data_store

    async def save_schedule(
        self,
        schedule: WorkflowSchedule,
        tenant_id: str = "default",
        outbox_store: OutboxStore | None = None,
    ) -> None:
        """Persist or update a WorkflowSchedule definition."""
        tid = schedule.tenant_id or tenant_id
        sch_model = _schedule_to_model(schedule, tenant_id=tid)

        async def _action(session: AsyncSession) -> None:
            existing = await session.scalar(
                select(WorkflowScheduleModel).where(
                    WorkflowScheduleModel.id == str(schedule.id),
                    WorkflowScheduleModel.tenant_id == tid,
                )
            )
            if existing is None:
                session.add(sch_model)
                if outbox_store is not None:
                    outbox_store.stage_event_in_session(
                        session=session,
                        tenant_id=tid,
                        topic="workflow.schedule.created",
                        payload={
                            "schedule_id": str(schedule.id),
                            "name": schedule.name,
                            "definition_id": schedule.definition_id,
                            "tenant_id": tid,
                        },
                    )
            else:
                existing.name = sch_model.name
                existing.definition_id = sch_model.definition_id
                existing.schedule_type = sch_model.schedule_type
                existing.cron_expression = sch_model.cron_expression
                existing.interval_seconds = sch_model.interval_seconds
                existing.run_at = sch_model.run_at
                existing.next_run_at = sch_model.next_run_at
                existing.last_run_at = sch_model.last_run_at
                existing.last_instance_id = sch_model.last_instance_id
                existing.status = sch_model.status
                existing.initial_context_json = sch_model.initial_context_json
                existing.max_runs = sch_model.max_runs
                existing.run_count = sch_model.run_count
                existing.timezone = sch_model.timezone
                existing.updated_at = datetime.datetime.now(datetime.UTC)
            await session.flush()

        try:
            await self._data_store.execute_in_transaction(_action)
        except IntegrityError as exc:
            raise ScheduleConflictError(
                f"Schedule with name '{schedule.name}' already exists in tenant '{tid}'."
            ) from exc

    async def get_schedule(
        self, schedule_id: UUID | str, tenant_id: str | None = None
    ) -> WorkflowSchedule | None:
        """Retrieve a schedule by UUID with optional tenant boundary."""
        s_id = str(schedule_id)

        async def _action(session: AsyncSession) -> WorkflowScheduleModel | None:
            stmt = select(WorkflowScheduleModel).where(WorkflowScheduleModel.id == s_id)
            if tenant_id is not None:
                stmt = stmt.where(WorkflowScheduleModel.tenant_id == tenant_id)
            return await session.scalar(stmt)

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_schedule(row) if row else None

    async def get_schedule_by_name(
        self, name: str, tenant_id: str = "default"
    ) -> WorkflowSchedule | None:
        """Retrieve a schedule by unique name within a tenant."""
        async def _action(session: AsyncSession) -> WorkflowScheduleModel | None:
            stmt = select(WorkflowScheduleModel).where(
                WorkflowScheduleModel.name == name,
                WorkflowScheduleModel.tenant_id == tenant_id,
            )
            return await session.scalar(stmt)

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_schedule(row) if row else None

    async def list_schedules(
        self,
        tenant_id: str = "default",
        status_filter: str | None = None,
    ) -> list[WorkflowSchedule]:
        """List schedules matching criteria in a tenant boundary."""
        async def _action(session: AsyncSession) -> list[WorkflowScheduleModel]:
            stmt = select(WorkflowScheduleModel).where(
                WorkflowScheduleModel.tenant_id == tenant_id
            )
            if status_filter:
                stmt = stmt.where(WorkflowScheduleModel.status == status_filter)
            stmt = stmt.order_by(WorkflowScheduleModel.created_at.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        return [_model_to_schedule(row) for row in rows]

    async def claim_due_schedules(
        self,
        before_time: datetime.datetime,
        tenant_id: str | None = None,
        limit: int = 50,
    ) -> list[WorkflowSchedule]:
        """Claim due schedules atomically to prevent concurrent runner collisions.

        M5-A5: the previous implementation was a bare SELECT with no
        claiming step at all — two concurrent callers (two scheduler
        processes, or a manual `trigger_schedule` racing the background poll
        loop) could both select and both act on the same due row, starting
        two workflow instances for one fire. Claiming is now a
        compare-and-swap per candidate row (`ACTIVE -> TRIGGERING`, `WHERE
        status='ACTIVE'`, checking `rowcount`) — the same atomic-UPDATE
        pattern already used elsewhere in this codebase for exclusive
        claims (e.g. `IdempotencyStore`'s `FAILED -> PROCESSING` transition
        and its stale-`PROCESSING` reclaim). Only rows this call actually
        won the CAS on are returned; a candidate another concurrent caller
        claims first is silently dropped, not retried, exactly like those
        other CAS sites.

        Before claiming new work, any row abandoned in `TRIGGERING` past
        `SCHEDULE_CLAIM_LEASE_SECONDS` (a scheduler process died between
        claiming it and calling `record_schedule_tick`) is reset back to
        `ACTIVE` first, so a crash can never permanently strand a schedule.
        This reset step is a plain bulk UPDATE, not a CAS: two callers both
        resetting the same stale row to `ACTIVE` is harmless (both write
        the same value), and the actual mutual-exclusion for claiming it
        again happens in the CAS step immediately after.
        """
        dt = before_time.replace(tzinfo=datetime.UTC) if before_time.tzinfo is None else before_time
        stale_cutoff = dt - datetime.timedelta(seconds=SCHEDULE_CLAIM_LEASE_SECONDS)

        async def _reclaim_abandoned(session: AsyncSession) -> None:
            stmt = (
                update(WorkflowScheduleModel)
                .where(
                    WorkflowScheduleModel.status == ScheduleStatus.TRIGGERING.value,
                    WorkflowScheduleModel.updated_at < stale_cutoff,
                )
                .values(status=ScheduleStatus.ACTIVE.value)
            )
            if tenant_id is not None:
                stmt = stmt.where(WorkflowScheduleModel.tenant_id == tenant_id)
            await session.execute(stmt)

        await self._data_store.execute_in_transaction(_reclaim_abandoned)

        async def _select_candidates(session: AsyncSession) -> list[str]:
            stmt = select(WorkflowScheduleModel.id).where(
                WorkflowScheduleModel.status == ScheduleStatus.ACTIVE.value,
                WorkflowScheduleModel.next_run_at <= dt,
            )
            if tenant_id is not None:
                stmt = stmt.where(WorkflowScheduleModel.tenant_id == tenant_id)
            stmt = stmt.order_by(WorkflowScheduleModel.next_run_at.asc()).limit(limit)
            result = await session.execute(stmt)
            return [str(row) for row in result.scalars().all()]

        candidate_ids = await self._data_store.execute_in_transaction(_select_candidates)

        claimed_rows: list[WorkflowScheduleModel] = []
        for sched_id in candidate_ids:

            async def _claim_one(session: AsyncSession, sid: str = sched_id) -> WorkflowScheduleModel | None:
                claim_stmt = (
                    update(WorkflowScheduleModel)
                    .where(
                        WorkflowScheduleModel.id == sid,
                        WorkflowScheduleModel.status == ScheduleStatus.ACTIVE.value,
                        WorkflowScheduleModel.next_run_at <= dt,
                    )
                    .values(status=ScheduleStatus.TRIGGERING.value, updated_at=datetime.datetime.now(datetime.UTC))
                )
                result = cast(CursorResult[Any], await session.execute(claim_stmt))
                if result.rowcount != 1:
                    # Another concurrent caller claimed (or otherwise
                    # changed) this row first — not our win, skip it.
                    return None
                return await session.scalar(select(WorkflowScheduleModel).where(WorkflowScheduleModel.id == sid))

            row = await self._data_store.execute_in_transaction(_claim_one)
            if row is not None:
                claimed_rows.append(row)

        return [_model_to_schedule(row) for row in claimed_rows]

    async def record_schedule_tick(
        self,
        schedule_id: UUID | str,
        last_run_at: datetime.datetime,
        next_run_at: datetime.datetime | None,
        last_instance_id: UUID | str | None,
        run_count: int,
        new_status: str,
        tenant_id: str,
        outbox_store: OutboxStore | None = None,
    ) -> WorkflowSchedule | None:
        """Atomically record schedule trigger, increment run count, update next run, and stage event."""
        s_id = str(schedule_id)
        inst_id_str = str(last_instance_id) if last_instance_id else None

        async def _action(session: AsyncSession) -> WorkflowScheduleModel | None:
            schedule = await session.scalar(
                select(WorkflowScheduleModel).where(
                    WorkflowScheduleModel.id == s_id,
                    WorkflowScheduleModel.tenant_id == tenant_id,
                )
            )
            if schedule is None:
                return None

            schedule.last_run_at = last_run_at
            schedule.next_run_at = next_run_at
            schedule.last_instance_id = inst_id_str
            schedule.run_count = run_count
            schedule.status = new_status
            schedule.updated_at = datetime.datetime.now(datetime.UTC)

            if outbox_store is not None:
                outbox_store.stage_event_in_session(
                    session=session,
                    tenant_id=tenant_id,
                    topic="workflow.schedule.triggered",
                    payload={
                        "schedule_id": s_id,
                        "name": schedule.name,
                        "definition_id": schedule.definition_id,
                        "instance_id": inst_id_str,
                        "run_count": run_count,
                        "tenant_id": tenant_id,
                    },
                )
            await session.flush()
            return schedule

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_schedule(row) if row else None

    async def update_schedule_status(
        self,
        schedule_id: UUID | str,
        status: str,
        tenant_id: str,
        outbox_store: OutboxStore | None = None,
    ) -> WorkflowSchedule | None:
        """Update schedule status (ACTIVE, PAUSED, DISABLED, COMPLETED) with outbox event."""
        s_id = str(schedule_id)

        async def _action(session: AsyncSession) -> WorkflowScheduleModel | None:
            schedule = await session.scalar(
                select(WorkflowScheduleModel).where(
                    WorkflowScheduleModel.id == s_id,
                    WorkflowScheduleModel.tenant_id == tenant_id,
                )
            )
            if schedule is None:
                return None

            schedule.status = status
            schedule.updated_at = datetime.datetime.now(datetime.UTC)

            if outbox_store is not None:
                outbox_store.stage_event_in_session(
                    session=session,
                    tenant_id=tenant_id,
                    topic=f"workflow.schedule.{status.lower()}",
                    payload={
                        "schedule_id": s_id,
                        "name": schedule.name,
                        "status": status,
                        "tenant_id": tenant_id,
                    },
                )
            await session.flush()
            return schedule

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_schedule(row) if row else None


# ============================================================================
# 6. Governed External Execution Relational Store (IDataStore Interface)
# ============================================================================


class ExternalExecutionStore:
    """Encapsulates relational persistence for Governed External Executions (Milestone M5.4)."""

    def __init__(self, data_store: IDataStore) -> None:
        self._data_store = data_store

    async def save_execution(
        self,
        record: ExternalExecutionRecord,
        parameters: dict[str, Any] | None = None,
        tenant_id: str = "default",
        outbox_store: OutboxStore | None = None,
    ) -> None:
        """Persist a new ExternalExecutionRecord and optionally stage event in transactional outbox."""
        tid = record.tenant_id or tenant_id
        model = _execution_to_model(record, parameters=parameters, tenant_id=tid)

        async def _action(session: AsyncSession) -> None:
            existing = await session.scalar(
                select(ExternalExecutionModel).where(
                    ExternalExecutionModel.id == str(record.id),
                    ExternalExecutionModel.tenant_id == tid,
                )
            )
            if existing is None:
                session.add(model)
                if outbox_store is not None:
                    outbox_store.stage_event_in_session(
                        session=session,
                        tenant_id=tid,
                        topic="workflow.external.started",
                        payload={
                            "execution_id": str(record.id),
                            "target": record.target,
                            "operation_type": record.operation_type,
                            "tenant_id": tid,
                        },
                    )
            else:
                existing.status = model.status
                existing.status_code = model.status_code
                existing.output_json = model.output_json
                existing.error_message = model.error_message
                existing.attempts = model.attempts
                existing.execution_time_ms = model.execution_time_ms
                existing.approval_request_id = model.approval_request_id
                existing.completed_at = model.completed_at
                existing.updated_at = datetime.datetime.now(datetime.UTC)
            await session.flush()

        await self._data_store.execute_in_transaction(_action)

    async def get_execution(
        self, execution_id: UUID | str, tenant_id: str | None = None
    ) -> ExternalExecutionRecord | None:
        """Retrieve an external execution record by ID with optional tenant isolation."""
        e_id = str(execution_id)

        async def _action(session: AsyncSession) -> ExternalExecutionModel | None:
            stmt = select(ExternalExecutionModel).where(ExternalExecutionModel.id == e_id)
            if tenant_id is not None:
                stmt = stmt.where(ExternalExecutionModel.tenant_id == tenant_id)
            return await session.scalar(stmt)

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_execution(row) if row else None

    async def list_executions(
        self,
        tenant_id: str = "default",
        status_filter: str | None = None,
        limit: int = 100,
    ) -> list[ExternalExecutionRecord]:
        """List external execution records within a tenant boundary."""
        async def _action(session: AsyncSession) -> list[ExternalExecutionModel]:
            stmt = select(ExternalExecutionModel).where(
                ExternalExecutionModel.tenant_id == tenant_id
            )
            if status_filter:
                stmt = stmt.where(ExternalExecutionModel.status == status_filter)
            stmt = stmt.order_by(ExternalExecutionModel.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        return [_model_to_execution(row) for row in rows]

    async def update_execution_status(
        self,
        execution_id: UUID | str,
        status: str,
        tenant_id: str,
        output: Any | None = None,  # noqa: ANN401
        error: str | None = None,

        status_code: int | None = None,
        attempts: int = 1,
        execution_time_ms: float = 0.0,
        completed_at: datetime.datetime | None = None,
        outbox_store: OutboxStore | None = None,
    ) -> ExternalExecutionRecord | None:
        """Atomically update execution status, outputs, timing, and stage completion/failure outbox event."""
        e_id = str(execution_id)
        out_json = json.dumps(sanitize_for_persistence(output)) if output is not None else None

        async def _action(session: AsyncSession) -> ExternalExecutionModel | None:
            record = await session.scalar(
                select(ExternalExecutionModel).where(
                    ExternalExecutionModel.id == e_id,
                    ExternalExecutionModel.tenant_id == tenant_id,
                )
            )
            if record is None:
                return None

            record.status = status
            record.status_code = status_code
            record.output_json = out_json
            record.error_message = error
            record.attempts = attempts
            record.execution_time_ms = execution_time_ms
            record.completed_at = completed_at
            record.updated_at = datetime.datetime.now(datetime.UTC)

            if outbox_store is not None:
                topic = (
                    "workflow.external.completed"
                    if status == "COMPLETED"
                    else f"workflow.external.{status.lower()}"
                )
                outbox_store.stage_event_in_session(
                    session=session,
                    tenant_id=tenant_id,
                    topic=topic,
                    payload={
                        "execution_id": e_id,
                        "status": status,
                        "status_code": status_code,
                        "attempts": attempts,
                        "execution_time_ms": execution_time_ms,
                        "tenant_id": tenant_id,
                    },
                )
            await session.flush()
            return record

        row = await self._data_store.execute_in_transaction(_action)
        return _model_to_execution(row) if row else None
