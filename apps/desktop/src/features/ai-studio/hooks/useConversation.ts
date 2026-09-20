import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getConversationHistory, sendAgentMessage } from "../chat-api";
import { getActiveConversationId, setActiveConversationId, startNewConversationId } from "../chat-conversation-id";
import { conversationsQueryKey } from "./useConversations";
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
 * `chat-api.ts`'s module doc). On mount, and again whenever the active
 * conversation changes (`switchConversation`/`startNewConversation`, Phase
 * C), the transcript is rehydrated from the durable
 * `kortex.ai.conversation.history.get` capability exactly once per
 * conversation -- never from any client-cached copy -- so a reload,
 * restart, or switch to a different conversation always recovers the real
 * transcript for whichever conversation is now active. A
 * `PAUSED_FOR_APPROVAL` result is rendered as a pending-approval
 * placeholder message; the caller is responsible for polling
 * (`useAgentStatus`) and calling `resolvePendingApproval` once that poll
 * observes a terminal status -- this hook never resumes anything itself.
 *
 * `conversationId` is mutable state, not a one-time `useState` initializer
 * (Phase C): a tenant/user now has many conversations, and this hook must
 * be able to point at a different one -- via `switchConversation` (Recent
 * Conversations) or `startNewConversation` (New Chat) -- without the
 * consuming component (`ChatPanel.tsx`/`MiniChatHost.tsx`) unmounting and
 * remounting. Both setters reset local transcript state and the hydration
 * guard so the newly active conversation's own history loads cleanly,
 * exactly as it would on a fresh mount.
 */
export function useConversation({ tenantId, userId }: UseConversationArgs) {
  const queryClient = useQueryClient();
  const [conversationId, setConversationId] = React.useState(() => getActiveConversationId(tenantId, userId));
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [pendingTaskId, setPendingTaskId] = React.useState<string | null>(null);
  const hasHydratedRef = React.useRef(false);
  // Closeout code-review fix: read at `onSuccess`/`onError` execution time
  // (never from a closure captured when the mutation started) to detect a
  // conversation switch that happened while the send was still in flight —
  // without this, a reply for conversation A could be appended to
  // conversation B's freshly-reset transcript if the user switched
  // conversations before A's response resolved.
  const conversationIdRef = React.useRef(conversationId);
  conversationIdRef.current = conversationId;

  const invalidateConversationsList = React.useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: conversationsQueryKey(tenantId, userId) });
  }, [queryClient, tenantId, userId]);

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
    mutationFn: async ({ goal, conversationId: targetConversationId }: { goal: string; conversationId: string }) => {
      const taskId = crypto.randomUUID();
      const result = await sendAgentMessage({ taskId, tenantId, userId, conversationId: targetConversationId, goal });
      return { result, goal, taskId };
    },
    onSuccess: ({ result, goal, taskId }, variables) => {
      // The user switched to a different conversation while this send was
      // still in flight — its own transcript (or new empty one) is what's
      // showing now, so this reply belongs in the OLD conversation's
      // history (already persisted server-side), never appended here.
      if (variables.conversationId !== conversationIdRef.current) return;
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
    onError: (error, variables) => {
      if (variables.conversationId !== conversationIdRef.current) return;
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
    // A completed (or paused) turn may durably create a brand-new
    // conversation, or bump an existing one's last-activity ordering --
    // either way, Recent Conversations must reflect it without a manual
    // refresh. `onSettled` (not just `onSuccess`) so a genuinely-persisted
    // partial turn is never missed by only checking the happy path.
    onSettled: invalidateConversationsList,
  });

  const sendMessage = React.useCallback(
    (goal: string) => {
      const trimmed = goal.trim();
      if (!trimmed) return;
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "user", content: trimmed, createdAt: new Date().toISOString() },
      ]);
      sendMutation.mutate({ goal: trimmed, conversationId });
    },
    [sendMutation, conversationId],
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
      if (status === "COMPLETED") {
        invalidateConversationsList();
      }
    },
    [tenantId, conversationId, invalidateConversationsList],
  );

  /** Selects an existing conversation (Recent Conversations, Phase C).
   * Persists it as the new active pointer, then resets the transcript and
   * hydration guard so `historyQuery`'s own queryKey change (it includes
   * `conversationId`) hydrates the newly active conversation's real
   * history -- exactly the same hydration path a fresh mount takes, not a
   * second one. A no-op when the requested id is already active. */
  const switchConversation = React.useCallback(
    (nextConversationId: string) => {
      if (nextConversationId === conversationId) return;
      setActiveConversationId(tenantId, userId, nextConversationId);
      hasHydratedRef.current = false;
      setMessages([]);
      setPendingTaskId(null);
      setConversationId(nextConversationId);
    },
    [tenantId, userId, conversationId],
  );

  /** Starts a brand-new, genuinely empty conversation (New Chat, Phase C).
   * `historyQuery` still fetches for the new id (its queryKey includes
   * `conversationId`), but marking hydration as already-done means that
   * fetch's inevitable empty result is simply never prepended to
   * `messages` -- there is nothing to hydrate for an id known to have no
   * history yet. */
  const startNewConversation = React.useCallback(() => {
    const generated = startNewConversationId(tenantId, userId);
    hasHydratedRef.current = true;
    setMessages([]);
    setPendingTaskId(null);
    setConversationId(generated);
  }, [tenantId, userId]);

  return {
    conversationId,
    messages,
    isLoadingHistory: historyQuery.isPending,
    historyError: historyQuery.error,
    isSending: sendMutation.isPending,
    pendingTaskId,
    sendMessage,
    resolvePendingApproval,
    switchConversation,
    startNewConversation,
  };
}
