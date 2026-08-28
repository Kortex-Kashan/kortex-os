/**
 * Mirrors the safe subset of `kortex.engines.workflow.models.WorkflowDefinition`
 * (backend/src/kortex/engines/workflow/models.py) returned by
 * `kortex.workflow.definition.list`. Deliberately NOT a field-for-field
 * mirror: `WorkflowStep.parameters` and `WorkflowStep.compensationAction`
 * are arbitrary author-supplied key/value data (step invocation
 * parameters, rollback context) that could carry sensitive configuration
 * depending on how a definition was authored — this type omits both
 * rather than assuming they're safe to render (see api.ts's
 * `toWorkflowDefinition`).
 */
export type WorkflowTrigger = "MANUAL" | "EVENT" | "SCHEDULED" | "API" | "RECIPE";

export type WorkflowPriority = "LOW" | "NORMAL" | "HIGH" | "CRITICAL";

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
