/**
 * IPC capability wrappers for the Workflow workspace (M5.6, hardened M5-A6).
 *
 * All calls use the existing generic IPC path:
 *   React → invokeCapability → Tauri invoke_capability → Rust → backend CapabilityDispatcher
 *
 * No dedicated Tauri commands are introduced. Snake_case backend fields are
 * mapped to camelCase in all `to*` mapper functions — the UI layer never
 * touches raw wire shapes.
 *
 * Every `Raw*` interface and every capability's request-parameter names below
 * were verified directly against the backend handler source
 * (`backend/src/kortex/engines/workflow/engine.py`) and the domain models it
 * returns or hand-builds — not assumed. See `./types.ts`'s module docstring
 * for the full account of what the pre-M5-A6 version of this file got wrong.
 *
 * Sensitive fields (raw step parameters, compensation contexts, credential
 * handles, shell environment variables) are deliberately excluded from all
 * mappings.
 */

import { invokeCapability } from "@/ipc/client";
import type {
  ApprovalDecisionPayload,
  ApprovalRequest,
  ApprovalState,
  CreateSchedulePayload,
  DelegationPayload,
  ExternalExecution,
  ExternalExecutionStatus,
  ScheduleStatus,
  ScheduleType,
  WorkflowDefinition,
  WorkflowInstance,
  WorkflowPriority,
  WorkflowSchedule,
  WorkflowState,
  WorkflowStatus,
  WorkflowTrigger,
} from "./types";
import type { IpcResultEnvelope } from "@/ipc/client";

// ---------------------------------------------------------------------------
// Error Classes
// ---------------------------------------------------------------------------

/** Permission denied (PERMISSION_DENIED category — encompasses 401 + 403). */
export class WorkflowAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowAccessDeniedError";
  }
}

/** Any other capability failure — generic, recoverable. */
export class WorkflowRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowRequestError";
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function extractResult(envelope: IpcResultEnvelope, capabilityName: string): unknown {
  if (envelope.status === "SUCCESS") {
    return envelope.payload?.result ?? null;
  }
  const failure = envelope.errors[0];
  const message = failure?.message ?? `Capability ${capabilityName} failed.`;
  if (failure?.category === "PERMISSION_DENIED") {
    throw new WorkflowAccessDeniedError(message);
  }
  throw new WorkflowRequestError(message);
}

async function invoke(
  capabilityName: string,
  parameters: Record<string, unknown> = {},
): Promise<unknown> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName,
    parameters,
  });
  return extractResult(envelope, capabilityName);
}

// ---------------------------------------------------------------------------
// Raw Wire Shapes
//
// Definitions unchanged from the original M5.6 slice — `list_definitions`
// returns real `WorkflowDefinition` models and this shape already matched.
// ---------------------------------------------------------------------------

interface RawWorkflowStep {
  id: string;
  name: string;
  capability_name?: string | null;
  is_approval_step?: boolean;
}

interface RawWorkflowDefinition {
  id: string;
  name: string;
  version: string;
  description: string;
  trigger: string;
  priority: string;
  timeout_seconds: number;
  steps?: RawWorkflowStep[];
}

/** The raw `WorkflowInstance` Pydantic model, verbatim — see types.ts. */
interface RawWorkflowInstance {
  id: string;
  definition_id: string;
  definition_version: string;
  tenant_id: string;
  current_step_index: number;
  current_step_id?: string | null;
  state: string;
  status: string;
  trace_id: string;
  version: number;
  created_at: string;
  updated_at: string;
}

/** The hand-built dict every `kortex.workflow.approval.*` handler returns.
 * `context_snapshot` is present only from `get_approval_request`. */
interface RawApprovalRequest {
  id: string;
  tenant_id: string;
  instance_id?: string | null;
  step_id?: string | null;
  required_role: string;
  state: string;
  timeout_at?: string | null;
  signature_required: boolean;
  context_snapshot?: Record<string, unknown>;
}

/** The hand-built dict every `kortex.workflow.schedule.*` handler returns.
 * `last_run_at` is present only from `get_schedule`, `run_count` only from
 * `list_schedules`/`get_schedule` (not `create_schedule`/pause/resume/cancel). */
interface RawWorkflowSchedule {
  id: string;
  name: string;
  definition_id: string;
  schedule_type: string;
  cron_expression?: string | null;
  interval_seconds?: number | null;
  next_run_at?: string | null;
  last_run_at?: string | null;
  status: string;
  run_count?: number;
  tenant_id: string;
}

/** The hand-built dict every `kortex.workflow.external.*` handler returns. */
interface RawExternalExecution {
  id: string;
  status: string;
  target: string;
  output: unknown;
  error?: string | null;
  attempts: number;
  execution_time_ms: number;
  approval_request_id?: string | null;
  tenant_id: string;
}

// ---------------------------------------------------------------------------
// Mapper Functions (snake_case → camelCase)
// ---------------------------------------------------------------------------

function toDefinitionStep(raw: RawWorkflowStep) {
  return {
    id: raw.id,
    name: raw.name,
    capabilityName: raw.capability_name ?? null,
    isApprovalStep: raw.is_approval_step ?? false,
  };
}

function toDefinition(raw: RawWorkflowDefinition): WorkflowDefinition {
  return {
    id: raw.id,
    name: raw.name,
    version: raw.version,
    description: raw.description,
    trigger: raw.trigger as WorkflowTrigger,
    priority: raw.priority as WorkflowPriority,
    timeoutSeconds: raw.timeout_seconds,
    steps: (raw.steps ?? []).map(toDefinitionStep),
  };
}

function toInstance(raw: RawWorkflowInstance): WorkflowInstance {
  return {
    id: raw.id,
    definitionId: raw.definition_id,
    definitionVersion: raw.definition_version,
    tenantId: raw.tenant_id,
    currentStepIndex: raw.current_step_index,
    currentStepId: raw.current_step_id ?? null,
    state: raw.state as WorkflowState,
    status: raw.status as WorkflowStatus,
    traceId: raw.trace_id,
    version: raw.version,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  };
}

function toApproval(raw: RawApprovalRequest): ApprovalRequest {
  return {
    id: raw.id,
    tenantId: raw.tenant_id,
    instanceId: raw.instance_id ?? null,
    stepId: raw.step_id ?? null,
    requiredRole: raw.required_role,
    state: raw.state as ApprovalState,
    timeoutAt: raw.timeout_at ?? null,
    signatureRequired: raw.signature_required,
    contextSnapshot: raw.context_snapshot,
  };
}

function toSchedule(raw: RawWorkflowSchedule): WorkflowSchedule {
  return {
    id: raw.id,
    name: raw.name,
    definitionId: raw.definition_id,
    scheduleType: raw.schedule_type as ScheduleType,
    cronExpression: raw.cron_expression ?? null,
    intervalSeconds: raw.interval_seconds ?? null,
    nextRunAt: raw.next_run_at ?? null,
    lastRunAt: raw.last_run_at,
    status: raw.status as ScheduleStatus,
    runCount: raw.run_count ?? 0,
    tenantId: raw.tenant_id,
  };
}

function toExternalExecution(raw: RawExternalExecution): ExternalExecution {
  return {
    id: raw.id,
    status: raw.status as ExternalExecutionStatus,
    target: raw.target,
    output: raw.output ?? null,
    error: raw.error ?? null,
    attempts: raw.attempts,
    executionTimeMs: raw.execution_time_ms,
    approvalRequestId: raw.approval_request_id ?? null,
    tenantId: raw.tenant_id,
  };
}

// ---------------------------------------------------------------------------
// Definition API (M5.1)
// ---------------------------------------------------------------------------

export async function listWorkflowDefinitions(): Promise<WorkflowDefinition[]> {
  const raw = await invoke("kortex.workflow.definition.list");
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as RawWorkflowDefinition[]).map(toDefinition);
}

export async function startWorkflowInstance(
  definitionId: string,
  initialContext: Record<string, unknown> = {},
): Promise<WorkflowInstance> {
  const raw = await invoke("kortex.workflow.instance.start", {
    definition_id: definitionId,
    initial_context: initialContext,
  });
  return toInstance(raw as RawWorkflowInstance);
}

// ---------------------------------------------------------------------------
// Instance API (M5.1)
//
// `list_instances_durable(tenant_id, state)` has no `limit` and no
// `workflow_id`/`status` filter parameters — those were invented. The only
// supported filter is `state` (a `WorkflowState`, not `WorkflowStatus`).
// ---------------------------------------------------------------------------

export async function listWorkflowInstances(filters?: {
  state?: WorkflowState;
}): Promise<WorkflowInstance[]> {
  const raw = await invoke("kortex.workflow.instance.list", {
    state: filters?.state ?? null,
  });
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as RawWorkflowInstance[]).map(toInstance);
}

export async function getWorkflowInstance(instanceId: string): Promise<WorkflowInstance> {
  const raw = await invoke("kortex.workflow.instance.get", { instance_id: instanceId });
  return toInstance(raw as RawWorkflowInstance);
}

export async function cancelWorkflowInstance(
  instanceId: string,
  reason: string,
): Promise<void> {
  await invoke("kortex.workflow.instance.cancel", { instance_id: instanceId, reason });
}

export async function resumeWorkflowInstance(instanceId: string): Promise<void> {
  await invoke("kortex.workflow.instance.resume", { instance_id: instanceId });
}

// ---------------------------------------------------------------------------
// Approval API (M5.3)
// ---------------------------------------------------------------------------

export async function listPendingApprovals(): Promise<ApprovalRequest[]> {
  const raw = await invoke("kortex.workflow.approval.list", { state_filter: "PENDING" });
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as RawApprovalRequest[]).map(toApproval);
}

export async function getApprovalRequest(requestId: string): Promise<ApprovalRequest> {
  const raw = await invoke("kortex.workflow.approval.get", { request_id: requestId });
  return toApproval(raw as RawApprovalRequest);
}

/**
 * `decide_approval_request` requires `approver_id` (the deciding operator's
 * own principal ID — the backend rejects a mismatch against the
 * dispatcher-verified caller, M5-A2) and reads `reason`, not `rationale`.
 */
export async function submitApprovalDecision(payload: ApprovalDecisionPayload): Promise<void> {
  await invoke("kortex.workflow.approval.decide", {
    request_id: payload.requestId,
    decision: payload.decision,
    approver_id: payload.approverId,
    reason: payload.reason,
  });
}

/**
 * `delegate_approval_role(delegator_id, delegatee_id, role, valid_from,
 * valid_until, ...)` — a time-bounded role grant, not a per-ticket action.
 */
export async function delegateApproval(payload: DelegationPayload): Promise<void> {
  await invoke("kortex.workflow.approval.delegate", {
    delegator_id: payload.delegatorId,
    delegatee_id: payload.delegateeId,
    role: payload.role,
    valid_from: payload.validFrom,
    valid_until: payload.validUntil,
  });
}

// ---------------------------------------------------------------------------
// Schedule API (M5.4)
// ---------------------------------------------------------------------------

export async function listSchedules(): Promise<WorkflowSchedule[]> {
  const raw = await invoke("kortex.workflow.schedule.list");
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as RawWorkflowSchedule[]).map(toSchedule);
}

export async function getSchedule(scheduleId: string): Promise<WorkflowSchedule> {
  const raw = await invoke("kortex.workflow.schedule.get", { schedule_id: scheduleId });
  return toSchedule(raw as RawWorkflowSchedule);
}

/**
 * `create_schedule(name, definition_id, schedule_type="INTERVAL", ...)` —
 * `name` and `definitionId` are required with no backend default.
 */
export async function createSchedule(payload: CreateSchedulePayload): Promise<WorkflowSchedule> {
  const raw = await invoke("kortex.workflow.schedule.create", {
    name: payload.name,
    definition_id: payload.definitionId,
    schedule_type: payload.scheduleType,
    cron_expression: payload.cronExpression ?? null,
    interval_seconds: payload.intervalSeconds ?? null,
    run_at: payload.runAt ?? null,
    max_runs: payload.maxRuns ?? null,
    timezone: payload.timezone ?? "UTC",
  });
  return toSchedule(raw as RawWorkflowSchedule);
}

export async function pauseSchedule(scheduleId: string): Promise<void> {
  await invoke("kortex.workflow.schedule.pause", { schedule_id: scheduleId });
}

export async function resumeSchedule(scheduleId: string): Promise<void> {
  await invoke("kortex.workflow.schedule.resume", { schedule_id: scheduleId });
}

export async function cancelSchedule(scheduleId: string): Promise<void> {
  await invoke("kortex.workflow.schedule.cancel", { schedule_id: scheduleId });
}

export async function triggerScheduleNow(scheduleId: string): Promise<void> {
  await invoke("kortex.workflow.schedule.trigger", { schedule_id: scheduleId });
}

// ---------------------------------------------------------------------------
// External Execution API (M5.4)
// ---------------------------------------------------------------------------

export async function listExternalExecutions(filters?: {
  status?: ExternalExecutionStatus;
  limit?: number;
}): Promise<ExternalExecution[]> {
  const raw = await invoke("kortex.workflow.external.list", {
    status: filters?.status ?? null,
    limit: filters?.limit ?? 50,
  });
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as RawExternalExecution[]).map(toExternalExecution);
}

export async function getExternalExecution(executionId: string): Promise<ExternalExecution> {
  const raw = await invoke("kortex.workflow.external.get", { execution_id: executionId });
  return toExternalExecution(raw as RawExternalExecution);
}

/** `kortex.workflow.external.cancel` — fully implemented server-side
 * (`cancel_external_execution`) but the M5.6 UI never exposed a control for
 * it at all (M5-A7). */
export async function cancelExternalExecution(executionId: string): Promise<void> {
  await invoke("kortex.workflow.external.cancel", { execution_id: executionId });
}
