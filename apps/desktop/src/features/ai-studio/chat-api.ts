/**
 * IPC wrappers for M7.2 AI Studio Chat.
 *
 * Every user message is sent as a bounded `AgentTask` through the existing
 * `kortex.ai.agent.orchestrate` capability — the single, consistent path
 * that already supports both plain conversational replies (`COMPLETED` with
 * a `final_response`, zero tool calls needed) and governed tool use
 * (`PAUSED_FOR_APPROVAL`), with no client-side guessing about which a given
 * message needs.
 *
 * The desktop deliberately never calls `kortex.ai.agent.resume`: per M7.2
 * §2.3, an approved ticket resumes automatically on the backend through the
 * existing `workflow.approval.decided` -> `_on_approval_decided` event
 * chain. This client only ever polls `kortex.ai.agent.status` (see
 * `hooks/useAgentStatus.ts`) to observe that resolution — never drives it.
 *
 * Uses the same `invokeCapability` transport as every other AI Studio
 * capability (see `api.ts`/`governance-api.ts`) — no new IPC layer.
 */

import { invokeCapability } from "@/ipc/client";
import type { IpcResultEnvelope } from "@/ipc/client";
import type {
  AgentTaskSnapshot,
  AgentTaskStatus,
  AgentTurnResult,
  ConversationTurnDto,
  PendingToolCall,
} from "./chat-types";

export class AiChatAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AiChatAccessDeniedError";
  }
}

export class AiChatRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AiChatRequestError";
  }
}

function extractResult(envelope: IpcResultEnvelope, capabilityName: string): unknown {
  if (envelope.status === "SUCCESS") return envelope.payload?.result ?? null;
  const failure = envelope.errors[0];
  const message = failure?.message ?? `Capability ${capabilityName} failed.`;
  if (failure?.category === "PERMISSION_DENIED") throw new AiChatAccessDeniedError(message);
  throw new AiChatRequestError(message);
}

async function invoke(capabilityName: string, parameters: Record<string, unknown>): Promise<unknown> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName,
    parameters,
  });
  return extractResult(envelope, capabilityName);
}

// ---------------------------------------------------------------------------
// Raw wire -> camelCase mappers
// ---------------------------------------------------------------------------

interface RawToolCall {
  call_id: string;
  tool_name: string;
  arguments?: Record<string, unknown>;
}

interface RawAgentExecutionResult {
  task_id: string;
  tenant_id: string;
  status: AgentTaskStatus;
  final_response: string | null;
  total_steps?: number;
  error_message?: string | null;
  pending_tool_calls?: RawToolCall[];
  degraded?: boolean;
}

interface RawPersistedAgentTaskRecord {
  task: { task_id: string; tenant_id: string; conversation_id: string };
  status: AgentTaskStatus;
}

interface RawConversationTurn {
  sequence: number;
  user_content: string;
  assistant_content: string;
  created_at: string;
}

function toPendingToolCall(raw: RawToolCall): PendingToolCall {
  return {
    callId: raw.call_id,
    toolName: raw.tool_name,
    arguments: raw.arguments ?? {},
  };
}

function toAgentTurnResult(raw: RawAgentExecutionResult): AgentTurnResult {
  return {
    taskId: raw.task_id,
    tenantId: raw.tenant_id,
    status: raw.status,
    finalResponse: raw.final_response,
    totalSteps: raw.total_steps ?? 0,
    errorMessage: raw.error_message ?? null,
    pendingToolCalls: (raw.pending_tool_calls ?? []).map(toPendingToolCall),
    degraded: raw.degraded ?? false,
  };
}

function toAgentTaskSnapshot(raw: RawPersistedAgentTaskRecord): AgentTaskSnapshot {
  return {
    taskId: raw.task.task_id,
    tenantId: raw.task.tenant_id,
    status: raw.status,
    conversationId: raw.task.conversation_id,
  };
}

function toConversationTurn(raw: RawConversationTurn): ConversationTurnDto {
  return {
    sequence: raw.sequence,
    userContent: raw.user_content,
    assistantContent: raw.assistant_content,
    createdAt: raw.created_at,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface SendAgentMessageInput {
  taskId: string;
  tenantId: string;
  userId: string;
  conversationId: string;
  goal: string;
}

/** Calls the existing `kortex.ai.agent.orchestrate` capability to send one
 * chat message as a bounded agent task. */
export async function sendAgentMessage(input: SendAgentMessageInput): Promise<AgentTurnResult> {
  const raw = await invoke("kortex.ai.agent.orchestrate", {
    task: {
      task_id: input.taskId,
      tenant_id: input.tenantId,
      user_id: input.userId,
      conversation_id: input.conversationId,
      goal: input.goal,
    },
  });
  return toAgentTurnResult(raw as RawAgentExecutionResult);
}

/** Calls the existing `kortex.ai.agent.status` capability. Returns `null`
 * when the task no longer exists (never expected in normal operation, but
 * the backend's own contract allows it). */
export async function getAgentStatus(taskId: string, tenantId: string): Promise<AgentTaskSnapshot | null> {
  const raw = await invoke("kortex.ai.agent.status", { task_id: taskId, tenant_id: tenantId });
  if (!raw) return null;
  return toAgentTaskSnapshot(raw as RawPersistedAgentTaskRecord);
}

/** Calls the new (M7.2) `kortex.ai.conversation.history.get` capability to
 * rehydrate a conversation's durable turns — the same, single history
 * `generate_response`-backed and `orchestrate_agent`-backed turns share. */
export async function getConversationHistory(
  tenantId: string,
  conversationId: string,
): Promise<ConversationTurnDto[]> {
  const raw = await invoke("kortex.ai.conversation.history.get", {
    tenant_id: tenantId,
    conversation_id: conversationId,
  });
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as RawConversationTurn[]).map(toConversationTurn);
}
