/**
 * TypeScript domain models for the Workflow workspace (M5.6, hardened M5-A6).
 *
 * Every interface and enum below is derived directly from the ACTUAL backend
 * response shapes — the real Pydantic models and, where a capability handler
 * returns a hand-built dict rather than the raw model (approvals, schedules,
 * external executions all do), that exact dict's keys — not from what would
 * be convenient for the UI. The M5.6 audit found the previous version of
 * this file invented field names (`instance_id`, `workflow_name`,
 * `schedule_id`, `expires_at`, `total_steps`, a `steps[]` array on
 * WorkflowInstance, ...) that do not exist anywhere on the backend, which is
 * why the Approval Queue crashed on real data and every schedule action sent
 * `schedule_id: undefined`. The backend contract is authoritative; where the
 * backend genuinely doesn't expose something the old UI assumed (per-step
 * execution history, an approval's requester/decider identity, a schedule's
 * description or max-run count in its list view), that is reflected here as
 * an honest absence, not papered over.
 *
 * Sensitive fields (principal credentials, raw execution context, system
 * paths) are deliberately absent — never merely hidden in the UI.
 */

// ---------------------------------------------------------------------------
// Shared Enumerations — copied verbatim from backend enum.StrEnum definitions
// ---------------------------------------------------------------------------

export type WorkflowTrigger = "MANUAL" | "EVENT" | "SCHEDULED" | "API" | "RECIPE";

export type WorkflowPriority = "LOW" | "NORMAL" | "HIGH" | "CRITICAL";

/** `WorkflowState` — the deterministic lifecycle state machine value. */
export type WorkflowState =
  | "CREATED"
  | "VALIDATED"
  | "READY"
  | "RUNNING"
  | "WAITING"
  | "APPROVED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

/** `WorkflowStatus` — the separate operational status indicator field. */
export type WorkflowStatus =
  | "PENDING"
  | "RUNNING"
  | "PAUSED"
  | "WAITING_APPROVAL"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type ApprovalState = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" | "CANCELLED";

export type ApprovalDecision = "APPROVED" | "REJECTED";

export type ScheduleType = "CRON" | "INTERVAL" | "ONCE";

/** Includes the M5-A5 transient `TRIGGERING` claim state — a schedule can be
 * observed in it briefly between a scheduler tick claiming it and recording
 * the resulting run; the UI treats it as "about to run", not an error. */
export type ScheduleStatus = "ACTIVE" | "PAUSED" | "DISABLED" | "COMPLETED" | "TRIGGERING";

export type ExternalExecutionStatus =
  | "PENDING"
  | "WAITING_APPROVAL"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "TIMED_OUT";

// ---------------------------------------------------------------------------
// Definition Models (M5.1 — read-only catalog)
// ---------------------------------------------------------------------------

export interface WorkflowStepSummary {
  id: string;
  name: string;
  capabilityName: string | null;
  isApprovalStep: boolean;
}

export interface WorkflowDefinition {
  id: string;
  name: string;
  version: string;
  description: string;
  trigger: WorkflowTrigger;
  priority: WorkflowPriority;
  timeoutSeconds: number;
  steps: WorkflowStepSummary[];
}

// ---------------------------------------------------------------------------
// Instance / Execution Models (M5.1)
// ---------------------------------------------------------------------------

/**
 * `kortex.workflow.instance.*` capabilities return the raw `WorkflowInstance`
 * Pydantic model. There is currently no capability exposing per-step
 * execution history (`WorkflowStepRunModel`/`list_step_runs` exists in the
 * persistence layer but is not registered as a capability anywhere) — this
 * type deliberately has no `steps` field. `InstanceTimeline` shows progress
 * via `currentStepIndex` against the parent `WorkflowDefinition`'s step
 * count instead of a fabricated per-step timeline.
 */
export interface WorkflowInstance {
  id: string;
  definitionId: string;
  definitionVersion: string;
  tenantId: string;
  currentStepIndex: number;
  currentStepId: string | null;
  state: WorkflowState;
  status: WorkflowStatus;
  traceId: string;
  version: number;
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Approval Models (M5.3)
// ---------------------------------------------------------------------------

/**
 * `contextSnapshot` is only ever populated by `kortex.workflow.approval.get`
 * (single-ticket detail) — `kortex.workflow.approval.list`'s handler
 * (`list_approval_requests`) does not include it in its returned dicts at
 * all. There is no `workflowName`, `requesterPrincipalId`, `createdAt`,
 * `decidedAt`, `deciderPrincipalId`, or `decisionRationale` anywhere on the
 * backend response for this capability family — the previous version of
 * this file invented all of them.
 */
export interface ApprovalRequest {
  id: string;
  tenantId: string;
  instanceId: string | null;
  stepId: string | null;
  requiredRole: string;
  state: ApprovalState;
  timeoutAt: string | null;
  signatureRequired: boolean;
  contextSnapshot?: Record<string, unknown>;
}

/**
 * `decide_approval_request(request_id, decision, approver_id, reason=None, ...)`
 * — the backend requires the deciding principal's own ID (`approverId`) and
 * reads `reason`, not `rationale`. `approverId` is the acting operator's own
 * principal ID (from the authenticated session), never free text the
 * operator types — the backend rejects a decision whose `approver_id`
 * doesn't match the dispatcher-verified principal (M5-A2).
 */
export interface ApprovalDecisionPayload {
  requestId: string;
  decision: ApprovalDecision;
  approverId: string;
  reason: string;
}

/**
 * `delegate_approval_role(delegator_id, delegatee_id, role, valid_from,
 * valid_until, tenant_id, principal)` — role delegation is a time-bounded
 * grant of a ROLE from one principal to another, not an action tied to one
 * specific approval ticket the way the previous `{requestId,
 * delegateToPrincipalId, reason}` shape implied (that shape doesn't match
 * any backend parameter at all). The UI derives `role` from the approval
 * card it was opened from and `delegatorId` from the current session.
 */
export interface DelegationPayload {
  delegatorId: string;
  delegateeId: string;
  role: string;
  validFrom: string;
  validUntil: string;
}

// ---------------------------------------------------------------------------
// Schedule Models (M5.4)
// ---------------------------------------------------------------------------

/**
 * `lastRunAt` is only present on `kortex.workflow.schedule.get`'s response
 * (`get_schedule`) — `kortex.workflow.schedule.list`'s handler
 * (`list_schedules`) does not include it. There is no `description`,
 * `maxRuns`, `createdAt`, or `workflowName` field anywhere on either
 * response — the previous version of this file invented all of them, and
 * required `description`/`maxRuns` in its create form even though the
 * backend's `create_schedule` has no such parameters.
 */
export interface WorkflowSchedule {
  id: string;
  name: string;
  definitionId: string;
  scheduleType: ScheduleType;
  cronExpression: string | null;
  intervalSeconds: number | null;
  nextRunAt: string | null;
  lastRunAt?: string | null;
  status: ScheduleStatus;
  runCount: number;
  tenantId: string;
}

/**
 * `create_schedule(name, definition_id, schedule_type="INTERVAL",
 * cron_expression=None, interval_seconds=None, run_at=None,
 * max_runs=None, timezone="UTC", ...)` — `name` and `definitionId` are
 * required with no default; the previous payload shape
 * (`{workflowId, cronExpression, description, maxRuns}`) omitted both and
 * sent two fields (`description`) the backend does not accept, so every
 * real submission threw a `TypeError` server-side.
 */
export interface CreateSchedulePayload {
  name: string;
  definitionId: string;
  scheduleType: ScheduleType;
  cronExpression?: string;
  intervalSeconds?: number;
  runAt?: string;
  maxRuns?: number | null;
  timezone?: string;
}

// ---------------------------------------------------------------------------
// External Execution Models (M5.4)
// ---------------------------------------------------------------------------

/**
 * The real `execute_external_operation`/`get_external_execution`/
 * `list_external_executions` response shape has none of `executable`,
 * `arguments`, `workingDirectory`, `exitCode`, `stdout`, `stderr`,
 * `timeoutSeconds`, `startedAt`, `completedAt`, or `circuitBreakerOpen` —
 * it has `target`, `output`, `error`, `attempts`, and `executionTimeMs`
 * instead. The previous version of this file invented an entirely
 * different, subprocess-shaped record that does not match what the backend
 * actually returns for any of the three capabilities.
 */
export interface ExternalExecution {
  id: string;
  status: ExternalExecutionStatus;
  target: string;
  output: unknown;
  error: string | null;
  attempts: number;
  executionTimeMs: number;
  approvalRequestId: string | null;
  tenantId: string;
}
