import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getConversationHistory, sendAgentMessage } from "../chat-api";
import { getOrCreateConversationId } from "../chat-conversation-id";
import type { AgentTaskStatus, AgentTurnResult, ChatMessage } from "../chat-types";

function describeTerminalStatus(status: AgentTaskStatus): string {
  switch (status) {
    case "CANCELLED":
      return "This request was rejected and will not be carried out.";
    case "FAILED":
      return "This request failed.";
    case "TIMED_OUT":
      return "This request timed out.";
    case "STEP_LIMIT_EXCEEDED":
      return "This request could not be completed within its step limit.";
    case "LOOP_DETECTED":
      return "This request was stopped after detecting a repeating loop.";
    default:
      return "This request ended without a response.";
  }
}

function describeNonCompletedOutcome(result: AgentTurnResult): string {
  return result.errorMessage ?? describeTerminalStatus(result.status);
}

function describeSendError(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong sending this message.";
}

export interface UseConversationArgs {
  tenantId: string;
  userId: string;
}

/**
 * Owns the AI Studio Chat transcript for one (tenant, user) pair (M7.2).
 *
 * Every message is sent via `kortex.ai.agent.orchestrate` (see
 * `chat-api.ts`'s module doc). On mount, the transcript is rehydrated from
 * the durable `kortex.ai.conversation.history.get` capability exactly once
 * -- never from any client-cached copy -- so a reload or restart recovers
 * the real conversation. A `PAUSED_FOR_APPROVAL` result is rendered as a
 * pending-approval placeholder message; the caller is responsible for
 * polling (`useAgentStatus`) and calling `resolvePendingApproval` once that
 * poll observes a terminal status -- this hook never resumes anything
 * itself.
 */
export function useConversation({ tenantId, userId }: UseConversationArgs) {
  const [conversationId] = React.useState(() => getOrCreateConversationId(tenantId, userId));
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [pendingTaskId, setPendingTaskId] = React.useState<string | null>(null);
  const hasHydratedRef = React.useRef(false);

  const historyQuery = useQuery({
    queryKey: ["ai-studio", "chat", "history", tenantId, conversationId],
    queryFn: () => getConversationHistory(tenantId, conversationId),
  });

  React.useEffect(() => {
    if (hasHydratedRef.current || !historyQuery.isSuccess) return;
    hasHydratedRef.current = true;
    const hydrated: ChatMessage[] = [];
    for (const turn of historyQuery.data) {
      hydrated.push({
        id: `${turn.sequence}-user`,
        role: "user",
        content: turn.userContent,
        createdAt: turn.createdAt,
      });
      hydrated.push({
        id: `${turn.sequence}-assistant`,
        role: "assistant",
        content: turn.assistantContent,
        createdAt: turn.createdAt,
      });
    }
    // Prepended, never replaced: history resolves asynchronously, so a
    // message the user already sent before this fires (initial state is
    // always `[]`) must never be discarded -- it is chronologically after
    // every hydrated turn regardless of when this effect happens to run.
    setMessages((prev) => [...hydrated, ...prev]);
  }, [historyQuery.isSuccess, historyQuery.data]);

  const sendMutation = useMutation({
    mutationFn: async (goal: string) => {
      const taskId = crypto.randomUUID();
      const result = await sendAgentMessage({ taskId, tenantId, userId, conversationId, goal });
      return { result, goal, taskId };
    },
    onSuccess: ({ result, goal, taskId }) => {
      if (result.status === "PAUSED_FOR_APPROVAL") {
        setPendingTaskId(taskId);
        setMessages((prev) => [
          ...prev,
          {
            id: taskId,
            role: "assistant",
            content: "Waiting for approval before this can continue.",
            createdAt: new Date().toISOString(),
            pendingApproval: { taskId, goal, pendingToolCalls: result.pendingToolCalls },
          },
        ]);
        return;
      }

      const content = result.status === "COMPLETED" && result.finalResponse !== null
        ? result.finalResponse
        : describeNonCompletedOutcome(result);
      setMessages((prev) => [
        ...prev,
        { id: taskId, role: "assistant", content, createdAt: new Date().toISOString() },
      ]);
    },
    onError: (error) => {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: describeSendError(error),
          createdAt: new Date().toISOString(),
        },
      ]);
    },
  });

  const sendMessage = React.useCallback(
    (goal: string) => {
      const trimmed = goal.trim();
      if (!trimmed) return;
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "user", content: trimmed, createdAt: new Date().toISOString() },
      ]);
      sendMutation.mutate(trimmed);
    },
    [sendMutation],
  );

  /** Called once `useAgentStatus` observes a terminal status for a
   * previously PAUSED_FOR_APPROVAL task. `COMPLETED` re-reads durable
   * history for the resolved reply (the server -- never this client --
   * wrote it there, automatically, when the approval was decided); any
   * other terminal status has no response to show, so the placeholder is
   * simply replaced with a short outcome notice. */
  const resolvePendingApproval = React.useCallback(
    async (taskId: string, status: AgentTaskStatus) => {
      if (status === "COMPLETED") {
        const turns = await getConversationHistory(tenantId, conversationId);
        const latest = turns.length > 0 ? turns[turns.length - 1] : undefined;
        setMessages((prev) =>
          prev.map((message) =>
            message.pendingApproval?.taskId === taskId
              ? {
                  id: message.id,
                  role: "assistant",
                  content: latest?.assistantContent ?? "The request completed.",
                  createdAt: latest?.createdAt ?? new Date().toISOString(),
                }
              : message,
          ),
        );
      } else {
        setMessages((prev) =>
          prev.map((message) =>
            message.pendingApproval?.taskId === taskId
              ? { id: message.id, role: "system", content: describeTerminalStatus(status), createdAt: message.createdAt }
              : message,
          ),
        );
      }
      setPendingTaskId((current) => (current === taskId ? null : current));
    },
    [tenantId, conversationId],
  );

  return {
    conversationId,
    messages,
    isLoadingHistory: historyQuery.isPending,
    historyError: historyQuery.error,
    isSending: sendMutation.isPending,
    pendingTaskId,
    sendMessage,
    resolvePendingApproval,
  };
}
