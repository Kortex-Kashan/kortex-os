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
  ProjectedCapability,
  WorkflowDraftDetail,
  WorkflowGraph,
  WorkflowValidationReport,
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
 * `context_snapshot` is present only from `get_approval_request`.
 * `requester_principal_id`/`requester_principal_type`/`correlation_id`
 * (M6.2-3) are present on `create`/`list`/`get` alike. */
interface RawApprovalRequest {
  id: string;
  tenant_id: string;
  instance_id?: string | null;
  step_id?: string | null;
  required_role: string;
  state: string;
  timeout_at?: string | null;
  signature_required: boolean;
  requester_principal_id?: string | null;
  requester_principal_type?: string | null;
  correlation_id?: string | null;
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
    requesterPrincipalId: raw.requester_principal_id ?? null,
    requesterPrincipalType: raw.requester_principal_type ?? null,
    correlationId: raw.correlation_id ?? null,
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

export async function cancelExternalExecution(executionId: string): Promise<void> {
  await invoke("kortex.workflow.external.cancel", { execution_id: executionId });
}

// ---------------------------------------------------------------------------
// Lifecycle & Draft API (Milestone F4)
// ---------------------------------------------------------------------------

function toGraphFromWire(raw: Record<string, unknown> | null | undefined): WorkflowGraph | null {
  if (!raw) return null;
  const rawNodes = Array.isArray(raw.nodes) ? (raw.nodes as Array<Record<string, unknown>>) : [];
  const rawEdges = Array.isArray(raw.edges) ? (raw.edges as Array<Record<string, unknown>>) : [];
  return {
    schemaVersion: (raw.schema_version as string) ?? "1.0.0",
    entryNodeId: String(raw.entry_node_id ?? ""),
    nodes: rawNodes.map((node) => ({
      nodeId: String(node.node_id ?? ""),
      nodeType: String(node.node_type ?? "capability"),
      capabilityName: (node.capability_name as string | null) ?? null,
      config: (node.config as Record<string, unknown>) ?? {},
      inputPorts: Array.isArray(node.input_ports) ? (node.input_ports as string[]) : [],
      outputPorts: Array.isArray(node.output_ports) ? (node.output_ports as string[]) : [],
      metadata: (node.metadata as Record<string, unknown>) ?? {},
    })),
    edges: rawEdges.map((edge) => ({
      edgeId: String(edge.edge_id ?? ""),
      sourceNodeId: String(edge.source_node_id ?? ""),
      targetNodeId: String(edge.target_node_id ?? ""),
      sourcePort: (edge.source_port as string | null) ?? null,
      targetPort: (edge.target_port as string | null) ?? null,
      metadata: (edge.metadata as Record<string, unknown>) ?? {},
    })),
    metadata: (raw.metadata as Record<string, unknown>) ?? {},
  };
}

export function toWireGraph(graph: WorkflowGraph | null | undefined): Record<string, unknown> | null {
  if (!graph) return null;
  return {
    schema_version: graph.schemaVersion ?? "1.0.0",
    entry_node_id: graph.entryNodeId,
    nodes: graph.nodes.map((node) => ({
      node_id: node.nodeId,
      node_type: node.nodeType,
      capability_name: node.capabilityName,
      config: node.config,
      input_ports: node.inputPorts ?? [],
      output_ports: node.outputPorts ?? [],
      metadata: node.metadata ?? {},
    })),
    edges: graph.edges.map((edge) => ({
      edge_id: edge.edgeId,
      source_node_id: edge.sourceNodeId,
      target_node_id: edge.targetNodeId,
      source_port: edge.sourcePort ?? null,
      target_port: edge.targetPort ?? null,
      metadata: edge.metadata ?? {},
    })),
    metadata: graph.metadata ?? {},
  };
}

function toDraftDetail(raw: Record<string, unknown>): WorkflowDraftDetail {
  return {
    id: String(raw.id ?? ""),
    definitionId: String(raw.definition_id ?? ""),
    tenantId: String(raw.tenant_id ?? ""),
    version: String(raw.version ?? "0.1.0"),
    status: String(raw.status ?? "DRAFT"),
    name: String(raw.name ?? ""),
    description: String(raw.description ?? ""),
    trigger: (raw.trigger as WorkflowTrigger) ?? "MANUAL",
    priority: (raw.priority as WorkflowPriority) ?? "NORMAL",
    timeoutSeconds: Number(raw.timeout_seconds ?? 3600),
    steps: Array.isArray(raw.steps) ? (raw.steps as RawWorkflowStep[]).map(toDefinitionStep) : [],
    graph: toGraphFromWire(raw.graph as Record<string, unknown> | null | undefined),
    lockVersion: Number(raw.lock_version ?? 1),
    createdAt: (raw.created_at as string | null) ?? null,
    updatedAt: (raw.updated_at as string | null) ?? null,
  };
}

export async function getWorkflowDefinitionDraft(definitionId: string): Promise<WorkflowDraftDetail | null> {
  const raw = (await invoke("kortex.workflow.definition.get", {
    definition_id: definitionId,
  })) as Record<string, unknown>;
  if (!raw) return null;
  if (raw.draft) {
    return toDraftDetail(raw.draft as Record<string, unknown>);
  }
  return toDraftDetail(raw);
}

export async function createWorkflowDraft(payload: {
  name: string;
  description?: string;
  trigger?: WorkflowTrigger;
  priority?: WorkflowPriority;
  timeoutSeconds?: number;
  graph?: WorkflowGraph;
}): Promise<WorkflowDraftDetail> {
  const raw = (await invoke("kortex.workflow.definition.create", {
    name: payload.name,
    description: payload.description ?? "",
    trigger: payload.trigger ?? "MANUAL",
    priority: payload.priority ?? "NORMAL",
    timeout_seconds: payload.timeoutSeconds ?? 3600,
    graph: toWireGraph(payload.graph),
  })) as Record<string, unknown>;
  return toDraftDetail(raw);
}

export async function updateWorkflowDraft(payload: {
  definitionId: string;
  expectedLockVersion: number;
  name?: string;
  description?: string;
  trigger?: WorkflowTrigger;
  priority?: WorkflowPriority;
  timeoutSeconds?: number;
  graph?: WorkflowGraph;
}): Promise<WorkflowDraftDetail> {
  const wireParams: Record<string, unknown> = {
    definition_id: payload.definitionId,
    expected_lock_version: payload.expectedLockVersion,
  };
  if (payload.name !== undefined) wireParams.name = payload.name;
  if (payload.description !== undefined) wireParams.description = payload.description;
  if (payload.trigger !== undefined) wireParams.trigger = payload.trigger;
  if (payload.priority !== undefined) wireParams.priority = payload.priority;
  if (payload.timeoutSeconds !== undefined) wireParams.timeout_seconds = payload.timeoutSeconds;
  if (payload.graph !== undefined) wireParams.graph = toWireGraph(payload.graph);

  const raw = (await invoke("kortex.workflow.definition.update", wireParams)) as Record<string, unknown>;
  return toDraftDetail(raw);
}

export async function validateWorkflowDraft(definitionId: string): Promise<WorkflowValidationReport> {
  const raw = (await invoke("kortex.workflow.definition.validate", {
    definition_id: definitionId,
  })) as Record<string, unknown>;
  return {
    isValid: Boolean(raw.is_valid),
    errors: Array.isArray(raw.errors) ? (raw.errors as string[]) : [],
    warnings: Array.isArray(raw.warnings) ? (raw.warnings as string[]) : [],
  };
}

export async function publishWorkflowDraft(
  definitionId: string,
  expectedLockVersion: number,
  version?: string,
): Promise<WorkflowDraftDetail> {
  const raw = (await invoke("kortex.workflow.definition.publish", {
    definition_id: definitionId,
    expected_lock_version: expectedLockVersion,
    version: version ?? null,
  })) as Record<string, unknown>;
  return toDraftDetail(raw);
}

// ---------------------------------------------------------------------------
// Capability Projection API (Milestone F6)
// ---------------------------------------------------------------------------

function toProjectedCapability(raw: Record<string, unknown>): ProjectedCapability {
  return {
    name: String(raw.name ?? ""),
    description: String(raw.description ?? ""),
    provider: String(raw.provider ?? ""),
    parametersSchema: (raw.parameters_schema as Record<string, unknown>) ?? { type: "object", properties: {} },
    returnsSchema: raw.returns_schema as Record<string, unknown> | undefined,
    requiredPermissions: (raw.required_permissions as string[] | null) ?? null,
    requiresAuthentication: Boolean(raw.requires_authentication),
    securityClassification: (raw.security_classification as string) ?? "RESTRICTED",
    isReadOnly: Boolean(raw.is_read_only),
    isIdempotent: Boolean(raw.is_idempotent),
    ownerDomain: (raw.owner_domain as string) ?? undefined,
    resourceType: (raw.resource_type as string) ?? undefined,
    action: (raw.action as string) ?? undefined,
  };
}

export async function projectCapabilities(filters?: {
  keyword?: string;
  ownerDomain?: string;
  resourceType?: string;
  action?: string;
  isReadOnly?: boolean;
  isIdempotent?: boolean;
}): Promise<ProjectedCapability[]> {
  const wireParams: Record<string, unknown> = {};
  if (filters?.keyword !== undefined) wireParams.keyword = filters.keyword;
  if (filters?.ownerDomain !== undefined) wireParams.owner_domain = filters.ownerDomain;
  if (filters?.resourceType !== undefined) wireParams.resource_type = filters.resourceType;
  if (filters?.action !== undefined) wireParams.action = filters.action;
  if (filters?.isReadOnly !== undefined) wireParams.is_read_only = filters.isReadOnly;
  if (filters?.isIdempotent !== undefined) wireParams.is_idempotent = filters.isIdempotent;

  const raw = await invoke("kortex.system.capability.project", wireParams);
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as Array<Record<string, unknown>>).map(toProjectedCapability);
}

export async function getProjectedCapability(capabilityName: string): Promise<ProjectedCapability | null> {
  const raw = (await invoke("kortex.system.capability.get", {
    capability_name: capabilityName,
  })) as Record<string, unknown> | null;
  if (!raw) return null;
  return toProjectedCapability(raw);
}

