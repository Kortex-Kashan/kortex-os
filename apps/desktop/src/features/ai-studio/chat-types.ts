/**
 * M7.2 AI Studio Chat types. Mirrors `kortex.engines.ai.agent.AgentStatus`,
 * `AgentTask`, `AgentExecutionResult`, `ResumeToken`-adjacent `ToolCall`, and
 * `kortex.engines.ai.memory.ConversationTurn` — the existing backend shapes
 * the new `kortex.ai.agent.orchestrate`/`kortex.ai.agent.status`/
 * `kortex.ai.conversation.history.get` capabilities already return. No new
 * backend concept is introduced here — only camelCase desktop views of it.
 */

/** Mirrors `kortex.engines.ai.agent.AgentStatus` (StrEnum) exactly. */
export type AgentTaskStatus =
  | "RUNNING"
  | "RESUMING"
  | "PAUSED_FOR_APPROVAL"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "TIMED_OUT"
  | "STEP_LIMIT_EXCEEDED"
  | "LOOP_DETECTED";

/** Statuses `useAgentStatus` stops polling on — nothing further will change. */
export const TERMINAL_AGENT_STATUSES: readonly AgentTaskStatus[] = [
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "TIMED_OUT",
  "STEP_LIMIT_EXCEEDED",
  "LOOP_DETECTED",
];

/** Mirrors `kortex.engines.ai.tools.ToolCall`. */
export interface PendingToolCall {
  callId: string;
  toolName: string;
  arguments: Record<string, unknown>;
}

/** Result of `kortex.ai.agent.orchestrate` — mirrors `AgentExecutionResult`,
 * minus `steps`/`resume_token`/`total_token_usage` (the desktop never reads
 * a resume token — see this feature's `chat-api.ts` module doc on why the
 * desktop never calls `kortex.ai.agent.resume` itself). */
export interface AgentTurnResult {
  taskId: string;
  tenantId: string;
  status: AgentTaskStatus;
  finalResponse: string | null;
  totalSteps: number;
  errorMessage: string | null;
  pendingToolCalls: PendingToolCall[];
  degraded: boolean;
}

/** Result of `kortex.ai.agent.status` — mirrors `PersistedAgentTaskRecord`,
 * to the extent the chat UI needs it: just enough to know whether a paused
 * task has moved on, and to what. */
export interface AgentTaskSnapshot {
  taskId: string;
  tenantId: string;
  status: AgentTaskStatus;
  conversationId: string;
}

/** Mirrors `kortex.engines.ai.memory.ConversationTurn`. */
export interface ConversationTurnDto {
  sequence: number;
  userContent: string;
  assistantContent: string;
  createdAt: string;
}

export type ChatMessageRole = "user" | "assistant" | "system";

/** One entry in the chat transcript `useConversation` renders. A message
 * with `pendingApproval` set is an assistant "turn" still awaiting a human
 * decision elsewhere (M7.2 §2.3, Option B) -- its `content` is a
 * placeholder, replaced once `useAgentStatus` observes a terminal status. */
export interface ChatMessage {
  id: string;
  role: ChatMessageRole;
  content: string;
  createdAt: string;
  pendingApproval?: {
    taskId: string;
    goal: string;
    pendingToolCalls: PendingToolCall[];
  };
}
