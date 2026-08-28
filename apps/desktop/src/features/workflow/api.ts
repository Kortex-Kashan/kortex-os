import { invokeCapability } from "@/ipc/client";
import type { WorkflowDefinition, WorkflowPriority, WorkflowStepSummary, WorkflowTrigger } from "./types";

const DEFINITION_LIST_CAPABILITY = "kortex.workflow.definition.list";

/**
 * Thrown when the backend denies the call with `PERMISSION_DENIED` — see
 * `apps/desktop/src/features/connectors/api.ts`'s `ConnectorAccessDeniedError`
 * for why this stays a single, unified category (the IPC transport doesn't
 * distinguish 401 from 403 in `errors[].category`, only in the newer
 * `httpStatus` field this feature does not yet consume).
 */
export class WorkflowAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowAccessDeniedError";
  }
}

/** Any other `FAILURE` envelope — a generic, recoverable failure. */
export class WorkflowRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowRequestError";
  }
}

/** Raw wire shape of one step within a definition — snake_case, since
 * `WorkflowStep` has no camelCase alias generator on the Python side.
 * `parameters` and `compensation_action` exist on the wire but are
 * deliberately not declared here — see `types.ts`'s module doc. */
interface RawWorkflowStep {
  id: string;
  name: string;
  capability_name?: string | null;
  is_approval_step?: boolean;
}

/** Raw wire shape of one entry in the capability's `result` array. */
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

function toStepSummary(raw: RawWorkflowStep): WorkflowStepSummary {
  return {
    id: raw.id,
    name: raw.name,
    capabilityName: raw.capability_name ?? null,
    isApprovalStep: raw.is_approval_step ?? false,
  };
}

function toWorkflowDefinition(raw: RawWorkflowDefinition): WorkflowDefinition {
  return {
    id: raw.id,
    name: raw.name,
    version: raw.version,
    description: raw.description,
    trigger: raw.trigger as WorkflowTrigger,
    priority: raw.priority as WorkflowPriority,
    timeoutSeconds: raw.timeout_seconds,
    steps: (raw.steps ?? []).map(toStepSummary),
  };
}

/**
 * Calls the existing `kortex.workflow.definition.list` capability through
 * the existing generic IPC path (React -> `ipc/client.ts` -> Tauri
 * `invoke_capability` -> Rust -> backend `CapabilityDispatcher`). No
 * dedicated Tauri command is introduced.
 */
export async function listWorkflowDefinitions(): Promise<WorkflowDefinition[]> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: DEFINITION_LIST_CAPABILITY,
    parameters: {},
  });

  if (envelope.status === "SUCCESS") {
    const raw = (envelope.payload?.result as RawWorkflowDefinition[] | undefined) ?? [];
    return raw.map(toWorkflowDefinition);
  }

  const failure = envelope.errors[0];
  const message = failure?.message ?? "Failed to load the workflow registry.";
  if (failure?.category === "PERMISSION_DENIED") {
    throw new WorkflowAccessDeniedError(message);
  }
  throw new WorkflowRequestError(message);
}
