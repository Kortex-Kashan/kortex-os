import { useQuery } from "@tanstack/react-query";
import { listConversations } from "../chat-api";

export function conversationsQueryKey(tenantId: string, userId: string) {
  return ["ai-studio", "chat", "conversations", tenantId, userId] as const;
}

/**
 * Server-derived "Recent Conversations" list for one tenant/user (AI
 * Studio functional stabilization, Phase C). `listConversations` itself
 * takes no identity parameters — the query key is still scoped by
 * `tenantId`/`userId` purely for cache partitioning (so switching
 * authenticated identity never shows a stale, previous user's list from
 * cache), not because the capability call needs either value.
 *
 * Plain `useQuery`, matching `useAgentStatus.ts`'s own choice not to route
 * through `useAiStudioQueryInterceptor` — that interceptor is scoped to
 * `api.ts`'s `AiStudioAccessDeniedError`/`AiStudioRequestError`, while this
 * feature's capabilities (`chat-api.ts`) throw their own
 * `AiChatAccessDeniedError`/`AiChatRequestError` and are handled inline by
 * each caller instead.
 */
export function useConversations(tenantId: string, userId: string) {
  return useQuery({
    queryKey: conversationsQueryKey(tenantId, userId),
    queryFn: () => listConversations(),
    enabled: tenantId.length > 0 && userId.length > 0,
  });
}
