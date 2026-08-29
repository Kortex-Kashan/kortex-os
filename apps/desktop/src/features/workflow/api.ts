/**
 * IPC capability wrappers for the Workflow workspace (M5.6).
 *
 * All calls use the existing generic IPC path:
 *   React → invokeCapability → Tauri invoke_capability → Rust → backend CapabilityDispatcher
 *
 * No dedicated Tauri commands are introduced. Snake_case backend fields are
 * mapped to camelCase in all `to*` mapper functions — the UI layer never
 * touches raw wire shapes.
 *
 * Sensitive fields (raw step parameters, compensation contexts, credential
 * handles, shell environment variables) are deliberately excluded from all
 * mappings.
 */

import { invokeCapability } from "@/ipc/client";
import type {
  ApprovalDecisionPayload,
  ApprovalRequest,
  ApprovalStatus,
  CreateSchedulePayload,
  DelegationPayload,
  ExternalExecution,
  ExternalExecutionStatus,
  ScheduleStatus,
  StepRecord,
  StepStatus,
  WorkflowDefinition,
  WorkflowInstance,
  WorkflowPriority,
  WorkflowSchedule,
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
// ---------------------------------------------------------------------------

interface RawStep {
  step_id: string;
  step_name: string;
  capability_name?: string | null;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  attempt_number?: number;
}

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

interface RawWorkflowInstance {
  instance_id: string;
  tenant_id: string;
  workflow_id: string;
  workflow_name: string;
  workflow_version: string;
  status: string;
  trigger: string;
  priority: string;
  current_step_index: number;
  total_steps: number;
  steps?: RawStep[];
  error_message?: string | null;
  started_at: string;
  completed_at?: string | null;
  timeout_seconds: number;
  correlation_id?: string | null;
}

interface RawApprovalRequest {
  request_id: string;
  tenant_id: string;
  instance_id: string;
  step_id: string;
  workflow_name?: string;
  required_role: string;
  requester_principal_id?: string;
  status: string;
  context?: Record<string, unknown>;
  expires_at?: string | null;
  created_at: string;
  decided_at?: string | null;
  decider_principal_id?: string | null;
  decision_rationale?: string | null;
}

interface RawSchedule {
  schedule_id: string;
  tenant_id: string;
  workflow_id: string;
  workflow_name?: string;
  cron_expression: string;
  status: string;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_run_status?: string | null;
  run_count?: number;
  max_runs?: number | null;
  created_at: string;
  description?: string;
}

interface RawExternalExecution {
  execution_id: string;
  tenant_id: string;
  instance_id?: string | null;
  workflow_id?: string | null;
  executable: string;
  arguments?: string[];
  working_directory?: string | null;
  status: string;
  exit_code?: number | null;
  stdout?: string | null;
  stderr?: string | null;
  timeout_seconds?: number;
  started_at: string;
  completed_at?: string | null;
  approval_request_id?: string | null;
  circuit_breaker_open?: boolean;
}

// ---------------------------------------------------------------------------
// Mapper Functions (snake_case → camelCase)
// ---------------------------------------------------------------------------

function toStep(raw: RawStep): StepRecord {
  return {
    stepId: raw.step_id,
    stepName: raw.step_name,
    capabilityName: raw.capability_name ?? null,
    status: raw.status as StepStatus,
    startedAt: raw.started_at ?? null,
    completedAt: raw.completed_at ?? null,
    errorMessage: raw.error_message ?? null,
    attemptNumber: raw.attempt_number ?? 1,
  };
}

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
    instanceId: raw.instance_id,
    tenantId: raw.tenant_id,
    workflowId: raw.workflow_id,
    workflowName: raw.workflow_name,
    workflowVersion: raw.workflow_version,
    status: raw.status as WorkflowStatus,
    trigger: raw.trigger as WorkflowTrigger,
    priority: raw.priority as WorkflowPriority,
    currentStepIndex: raw.current_step_index,
    totalSteps: raw.total_steps,
    steps: (raw.steps ?? []).map(toStep),
    errorMessage: raw.error_message ?? null,
    startedAt: raw.started_at,
    completedAt: raw.completed_at ?? null,
    timeoutSeconds: raw.timeout_seconds,
    correlationId: raw.correlation_id ?? null,
  };
}

function toApproval(raw: RawApprovalRequest): ApprovalRequest {
  return {
    requestId: raw.request_id,
    tenantId: raw.tenant_id,
    instanceId: raw.instance_id,
    stepId: raw.step_id,
    workflowName: raw.workflow_name ?? "",
    requiredRole: raw.required_role,
    requesterPrincipalId: raw.requester_principal_id ?? "",
    status: raw.status as ApprovalStatus,
    context: raw.context ?? {},
    expiresAt: raw.expires_at ?? null,
    createdAt: raw.created_at,
    decidedAt: raw.decided_at ?? null,
    deciderPrincipalId: raw.decider_principal_id ?? null,
    decisionRationale: raw.decision_rationale ?? null,
  };
}

function toSchedule(raw: RawSchedule): WorkflowSchedule {
  return {
    scheduleId: raw.schedule_id,
    tenantId: raw.tenant_id,
    workflowId: raw.workflow_id,
    workflowName: raw.workflow_name ?? "",
    cronExpression: raw.cron_expression,
    status: raw.status as ScheduleStatus,
    nextRunAt: raw.next_run_at ?? null,
    lastRunAt: raw.last_run_at ?? null,
    lastRunStatus: raw.last_run_status ?? null,
    runCount: raw.run_count ?? 0,
    maxRuns: raw.max_runs ?? null,
    createdAt: raw.created_at,
    description: raw.description ?? "",
  };
}

function toExternalExecution(raw: RawExternalExecution): ExternalExecution {
  return {
    executionId: raw.execution_id,
    tenantId: raw.tenant_id,
    instanceId: raw.instance_id ?? null,
    workflowId: raw.workflow_id ?? null,
    executable: raw.executable,
    arguments: raw.arguments ?? [],
    workingDirectory: raw.working_directory ?? null,
    status: raw.status as ExternalExecutionStatus,
    exitCode: raw.exit_code ?? null,
    stdout: raw.stdout ?? null,
    stderr: raw.stderr ?? null,
    timeoutSeconds: raw.timeout_seconds ?? 30,
    startedAt: raw.started_at,
    completedAt: raw.completed_at ?? null,
    approvalRequestId: raw.approval_request_id ?? null,
    circuitBreakerOpen: raw.circuit_breaker_open ?? false,
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
  workflowId: string,
  context: Record<string, unknown> = {},
): Promise<WorkflowInstance> {
  const raw = await invoke("kortex.workflow.instance.start", { workflow_id: workflowId, context });
  return toInstance(raw as RawWorkflowInstance);
}

// ---------------------------------------------------------------------------
// Instance API (M5.1)
// ---------------------------------------------------------------------------

export async function listWorkflowInstances(filters?: {
  workflowId?: string;
  status?: string;
  limit?: number;
}): Promise<WorkflowInstance[]> {
  const raw = await invoke("kortex.workflow.instance.list", {
    workflow_id: filters?.workflowId ?? null,
    status: filters?.status ?? null,
    limit: filters?.limit ?? 50,
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
  const raw = await invoke("kortex.workflow.approval.list", { status: "PENDING" });
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as RawApprovalRequest[]).map(toApproval);
}

export async function getApprovalRequest(requestId: string): Promise<ApprovalRequest> {
  const raw = await invoke("kortex.workflow.approval.get", { request_id: requestId });
  return toApproval(raw as RawApprovalRequest);
}

export async function submitApprovalDecision(payload: ApprovalDecisionPayload): Promise<void> {
  await invoke("kortex.workflow.approval.decide", {
    request_id: payload.requestId,
    decision: payload.decision,
    rationale: payload.rationale,
  });
}

export async function delegateApproval(payload: DelegationPayload): Promise<void> {
  await invoke("kortex.workflow.approval.delegate", {
    request_id: payload.requestId,
    delegate_to_principal_id: payload.delegateToPrincipalId,
    reason: payload.reason,
  });
}

// ---------------------------------------------------------------------------
// Schedule API (M5.4)
// ---------------------------------------------------------------------------

export async function listSchedules(): Promise<WorkflowSchedule[]> {
  const raw = await invoke("kortex.workflow.schedule.list");
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as RawSchedule[]).map(toSchedule);
}

export async function getSchedule(scheduleId: string): Promise<WorkflowSchedule> {
  const raw = await invoke("kortex.workflow.schedule.get", { schedule_id: scheduleId });
  return toSchedule(raw as RawSchedule);
}

export async function createSchedule(payload: CreateSchedulePayload): Promise<WorkflowSchedule> {
  const raw = await invoke("kortex.workflow.schedule.create", {
    workflow_id: payload.workflowId,
    cron_expression: payload.cronExpression,
    description: payload.description,
    max_runs: payload.maxRuns,
  });
  return toSchedule(raw as RawSchedule);
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
  instanceId?: string;
  status?: string;
  limit?: number;
}): Promise<ExternalExecution[]> {
  const raw = await invoke("kortex.workflow.external.list", {
    instance_id: filters?.instanceId ?? null,
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
