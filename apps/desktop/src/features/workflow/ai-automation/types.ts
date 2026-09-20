/**
 * Types for the AI Workflow Builder feature.
 *
 * Mirrors the backend's `kortex.ai.workflow_builder.generate` capability response shape
 * (`backend/src/kortex/api/workflow_builder.py`) and the F4 `kortex.workflow.definition.*`
 * capabilities this feature calls directly (never wrapped) for create/validate/update/publish.
 */

export interface WorkflowBuilderNode {
  nodeId: string;
  capabilityName: string | null;
  config: Record<string, unknown>;
  isApprovalStep: boolean;
}

export interface WorkflowBuilderGraph {
  entryNodeId: string;
  nodes: WorkflowBuilderNode[];
  edges: Array<{ sourceNodeId: string; targetNodeId: string }>;
  /** Opaque raw graph JSON, sent back verbatim to `create`/`update` — this feature never
   * hand-reconstructs the backend's own `WorkflowGraph` shape from the mapped fields above. */
  raw: Record<string, unknown>;
}

export type WorkflowBuilderStatus = "proposed" | "generation_failed";

export interface WorkflowBuilderProposal {
  status: WorkflowBuilderStatus;
  errors: string[];
  warnings: string[];
  graph: WorkflowBuilderGraph | null;
  name: string;
  description: string;
  conversationId: string;
}

export interface WorkflowValidationReport {
  isValid: boolean;
  errors: string[];
  warnings: string[];
}

export interface WorkflowDraftHandle {
  definitionId: string;
  lockVersion: number;
  status: string;
}

/**
 * The builder's own client-side flow state -- distinct from `WorkflowBuilderStatus` (the raw
 * proposal status). Named so the UI can render each phase unambiguously (master prompt §19: the
 * UI must make it impossible to confuse Generate / Create Draft / Publish).
 */
export type BuilderFlowPhase =
  | "idle"
  | "generating"
  | "proposed"
  | "generation_failed"
  | "creating_draft"
  | "validating"
  | "correcting"
  | "correction_exhausted"
  | "ready_for_review"
  | "publishing"
  | "published"
  | "failed";

export const MAX_CORRECTION_ATTEMPTS = 3;
