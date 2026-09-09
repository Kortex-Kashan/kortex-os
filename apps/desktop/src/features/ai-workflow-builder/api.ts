/**
 * IPC capability wrappers for the AI Workflow Builder feature.
 *
 * Every call uses the existing generic IPC path:
 *   React -> invokeCapability -> Tauri invoke_capability -> Rust -> backend CapabilityDispatcher
 *
 * No dedicated Tauri command is introduced. `create`/`get`/`update`/`validate`/`publish` are the
 * EXISTING F4 `kortex.workflow.definition.*` capabilities, called directly by this feature --
 * never wrapped by a new backend capability. Only the natural-language generation/correction step
 * (`kortex.ai.workflow_builder.generate`) is new. This mirrors the backend module's own stated
 * boundary (`backend/src/kortex/api/workflow_builder.py`): the AI layer proposes, this feature's
 * own direct capability calls (gated by explicit user actions) create/validate/update/publish.
 */

import { invokeCapability } from "@/ipc/client";
import type {
  BuilderFlowPhase,
  WorkflowBuilderGraph,
  WorkflowBuilderProposal,
  WorkflowDraftHandle,
  WorkflowValidationReport,
} from "./types";
import type { IpcResultEnvelope } from "@/ipc/client";

const GENERATE_CAPABILITY = "kortex.ai.workflow_builder.generate";
const CREATE_CAPABILITY = "kortex.workflow.definition.create";
const UPDATE_CAPABILITY = "kortex.workflow.definition.update";
const VALIDATE_CAPABILITY = "kortex.workflow.definition.validate";
const PUBLISH_CAPABILITY = "kortex.workflow.definition.publish";

export class WorkflowBuilderAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowBuilderAccessDeniedError";
  }
}

export class WorkflowBuilderRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowBuilderRequestError";
  }
}

function extractResult(envelope: IpcResultEnvelope, capabilityName: string): unknown {
  if (envelope.status === "SUCCESS") {
    return envelope.payload?.result ?? null;
  }
  const failure = envelope.errors[0];
  const message = failure?.message ?? `Capability ${capabilityName} failed.`;
  if (failure?.category === "PERMISSION_DENIED") {
    throw new WorkflowBuilderAccessDeniedError(message);
  }
  throw new WorkflowBuilderRequestError(message);
}

async function invoke(capabilityName: string, parameters: Record<string, unknown>): Promise<unknown> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName,
    parameters,
  });
  return extractResult(envelope, capabilityName);
}

interface RawProposal {
  status: "proposed" | "generation_failed";
  errors: string[];
  warnings: string[];
  graph: Record<string, unknown> | null;
  name: string;
  description: string;
  conversation_id: string;
}

function toGraph(raw: Record<string, unknown> | null): WorkflowBuilderGraph | null {
  if (raw === null) {
    return null;
  }
  const rawNodes = Array.isArray(raw.nodes) ? (raw.nodes as Array<Record<string, unknown>>) : [];
  const rawEdges = Array.isArray(raw.edges) ? (raw.edges as Array<Record<string, unknown>>) : [];
  return {
    entryNodeId: String(raw.entry_node_id ?? ""),
    nodes: rawNodes.map((node) => {
      const metadata = (node.metadata ?? {}) as Record<string, unknown>;
      const stepMetadata = (metadata["kortex.workflow.step"] ?? {}) as Record<string, unknown>;
      return {
        nodeId: String(node.node_id ?? ""),
        capabilityName: (node.capability_name as string | null) ?? null,
        config: (node.config ?? {}) as Record<string, unknown>,
        isApprovalStep: Boolean(stepMetadata.is_approval_step),
      };
    }),
    edges: rawEdges.map((edge) => ({
      sourceNodeId: String(edge.source_node_id ?? ""),
      targetNodeId: String(edge.target_node_id ?? ""),
    })),
    raw,
  };
}

function toProposal(raw: RawProposal): WorkflowBuilderProposal {
  return {
    status: raw.status,
    errors: raw.errors ?? [],
    warnings: raw.warnings ?? [],
    graph: toGraph(raw.graph),
    name: raw.name ?? "",
    description: raw.description ?? "",
    conversationId: raw.conversation_id ?? "",
  };
}

/** Generate a fresh proposal from natural-language `intent`. Never creates, updates, validates,
 * or publishes anything -- proposal only (D10: confirmation-required before any persistence). */
export async function generateWorkflowProposal(
  intent: string,
  conversationId?: string,
): Promise<WorkflowBuilderProposal> {
  const parameters: Record<string, unknown> = { intent };
  if (conversationId) {
    parameters.conversation_id = conversationId;
  }
  const raw = await invoke(GENERATE_CAPABILITY, parameters);
  return toProposal(raw as RawProposal);
}

/** Ask for a corrected proposal given F4's own real validation errors/warnings for the current
 * draft. One bounded attempt -- the caller (the hook below) enforces `MAX_CORRECTION_ATTEMPTS`. */
export async function correctWorkflowProposal(
  intent: string,
  conversationId: string,
  previousGraph: Record<string, unknown>,
  validationErrors: string[],
  validationWarnings: string[],
): Promise<WorkflowBuilderProposal> {
  const raw = await invoke(GENERATE_CAPABILITY, {
    intent,
    conversation_id: conversationId,
    previous_graph: previousGraph,
    validation_errors: validationErrors,
    validation_warnings: validationWarnings,
  });
  return toProposal(raw as RawProposal);
}

interface RawDefinitionVersion {
  definition_id: string;
  lock_version: number;
  status: string;
}

/** `kortex.workflow.definition.create` -- explicit, user-confirmed persistence of a proposal as
 * an F4 DRAFT (D10: "Create Draft" requires its own explicit user confirmation, separate from
 * generation). Called directly by this feature; never wrapped by a workflow-builder capability. */
export async function createWorkflowDraft(
  name: string,
  description: string,
  graph: Record<string, unknown>,
): Promise<WorkflowDraftHandle> {
  const raw = (await invoke(CREATE_CAPABILITY, { name, description, graph })) as RawDefinitionVersion;
  return { definitionId: raw.definition_id, lockVersion: raw.lock_version, status: raw.status };
}

/** `kortex.workflow.definition.update` -- applies a corrected graph to the existing DRAFT. */
export async function updateWorkflowDraft(
  definitionId: string,
  expectedLockVersion: number,
  graph: Record<string, unknown>,
): Promise<WorkflowDraftHandle> {
  const raw = (await invoke(UPDATE_CAPABILITY, {
    definition_id: definitionId,
    expected_lock_version: expectedLockVersion,
    graph,
  })) as RawDefinitionVersion;
  return { definitionId: raw.definition_id, lockVersion: raw.lock_version, status: raw.status };
}

/** `kortex.workflow.definition.validate` -- F4's own authoritative (informational) validation. */
export async function validateWorkflowDraft(definitionId: string): Promise<WorkflowValidationReport> {
  const raw = (await invoke(VALIDATE_CAPABILITY, { definition_id: definitionId })) as {
    is_valid: boolean;
    errors: string[];
    warnings: string[];
  };
  return { isValid: raw.is_valid, errors: raw.errors ?? [], warnings: raw.warnings ?? [] };
}

/** `kortex.workflow.definition.publish` -- the human-approval-gated point of no return (D9:
 * never called by any AI-originated code path; only this explicit, user-triggered call). */
export async function publishWorkflowDraft(
  definitionId: string,
  expectedLockVersion: number,
): Promise<WorkflowDraftHandle> {
  const raw = (await invoke(PUBLISH_CAPABILITY, {
    definition_id: definitionId,
    expected_lock_version: expectedLockVersion,
  })) as RawDefinitionVersion;
  return { definitionId: raw.definition_id, lockVersion: raw.lock_version, status: raw.status };
}

export const PHASE_LABELS: Record<BuilderFlowPhase, string> = {
  idle: "Describe the automation you want",
  generating: "Generating proposal…",
  proposed: "Proposal ready for review",
  generation_failed: "Generation failed",
  creating_draft: "Creating draft…",
  validating: "Validating…",
  correcting: "Correcting…",
  correction_exhausted: "Could not produce a valid workflow after 3 attempts",
  ready_for_review: "Draft ready for human review",
  publishing: "Publishing…",
  published: "Published",
  failed: "Failed",
};
