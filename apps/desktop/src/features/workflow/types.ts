/**
 * TypeScript domain models for the Workflow workspace (M5.6).
 *
 * Covers the safe, camelCase-mapped subset of backend models across:
 *  - WorkflowDefinition (M5.1): definition registry read-only view
 *  - WorkflowInstance (M5.1): execution lifecycle and step timeline
 *  - WorkflowApproval (M5.3): pending human governance decisions
 *  - WorkflowSchedule (M5.4): durable cron schedule registry
 *  - ExternalExecution (M5.4): governed external subprocess audit
 *
 * Sensitive fields (principal credentials, raw execution context, system
 * paths) are deliberately absent — never merely hidden in the UI.
 */

// ---------------------------------------------------------------------------
// Shared Enumerations
// ---------------------------------------------------------------------------

export type WorkflowTrigger = "MANUAL" | "EVENT" | "SCHEDULED" | "API" | "RECIPE";

export type WorkflowPriority = "LOW" | "NORMAL" | "HIGH" | "CRITICAL";

export type WorkflowStatus =
  | "PENDING"
  | "RUNNING"
  | "SUSPENDED"
  | "WAITING_APPROVAL"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "TIMED_OUT"
  | "COMPENSATING"
  | "COMPENSATED";

export type StepStatus =
  | "PENDING"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "SKIPPED"
  | "COMPENSATING"
  | "COMPENSATED";

export type ApprovalStatus = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" | "DELEGATED";

export type ApprovalDecision = "APPROVED" | "REJECTED";

export type ScheduleStatus = "ACTIVE" | "PAUSED" | "CANCELLED";

export type ExternalExecutionStatus =
  | "PENDING"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "TIMEOUT";

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

export interface StepRecord {
  stepId: string;
  stepName: string;
  capabilityName: string | null;
  status: StepStatus;
  startedAt: string | null;
  completedAt: string | null;
  errorMessage: string | null;
  attemptNumber: number;
}

export interface WorkflowInstance {
  instanceId: string;
  tenantId: string;
  workflowId: string;
  workflowName: string;
  workflowVersion: string;
  status: WorkflowStatus;
  trigger: WorkflowTrigger;
  priority: WorkflowPriority;
  currentStepIndex: number;
  totalSteps: number;
  steps: StepRecord[];
  errorMessage: string | null;
  startedAt: string;
  completedAt: string | null;
  timeoutSeconds: number;
  correlationId: string | null;
}

// ---------------------------------------------------------------------------
// Approval Models (M5.3)
// ---------------------------------------------------------------------------

export interface ApprovalRequest {
  requestId: string;
  tenantId: string;
  instanceId: string;
  stepId: string;
  workflowName: string;
  requiredRole: string;
  requesterPrincipalId: string;
  status: ApprovalStatus;
  context: Record<string, unknown>;
  expiresAt: string | null;
  createdAt: string;
  decidedAt: string | null;
  deciderPrincipalId: string | null;
  decisionRationale: string | null;
}

export interface ApprovalDecisionPayload {
  requestId: string;
  decision: ApprovalDecision;
  rationale: string;
}

export interface DelegationPayload {
  requestId: string;
  delegateToPrincipalId: string;
  reason: string;
}

// ---------------------------------------------------------------------------
// Schedule Models (M5.4)
// ---------------------------------------------------------------------------

export interface WorkflowSchedule {
  scheduleId: string;
  tenantId: string;
  workflowId: string;
  workflowName: string;
  cronExpression: string;
  status: ScheduleStatus;
  nextRunAt: string | null;
  lastRunAt: string | null;
  lastRunStatus: string | null;
  runCount: number;
  maxRuns: number | null;
  createdAt: string;
  description: string;
}

export interface CreateSchedulePayload {
  workflowId: string;
  cronExpression: string;
  description: string;
  maxRuns: number | null;
}

// ---------------------------------------------------------------------------
// External Execution Models (M5.4)
// ---------------------------------------------------------------------------

export interface ExternalExecution {
  executionId: string;
  tenantId: string;
  instanceId: string | null;
  workflowId: string | null;
  executable: string;
  arguments: string[];
  workingDirectory: string | null;
  status: ExternalExecutionStatus;
  exitCode: number | null;
  stdout: string | null;
  stderr: string | null;
  timeoutSeconds: number;
  startedAt: string;
  completedAt: string | null;
  approvalRequestId: string | null;
  circuitBreakerOpen: boolean;
}
