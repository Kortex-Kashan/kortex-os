import { useQuery } from "@tanstack/react-query";
import { getAgentStatus } from "../chat-api";
import { TERMINAL_AGENT_STATUSES } from "../chat-types";

export function agentStatusQueryKey(taskId: string, tenantId: string) {
  return ["ai-studio", "chat", "agent-status", tenantId, taskId] as const;
}

/**
 * Polls `kortex.ai.agent.status` for one agent task while it is
 * paused/resuming, and stops automatically once it reaches a terminal
 * status (M7.2 §2.3/§7) -- the desktop only ever *observes* resolution
 * here, it never drives it (no call to `kortex.ai.agent.resume` exists in
 * this feature).
 *
 * `enabled` lets the caller stop polling immediately once a task is known
 * to be resolved locally (e.g. after `sendAgentMessage` itself already
 * returned a terminal status), without needing to first query at all.
 */
export function useAgentStatus(taskId: string, tenantId: string, enabled: boolean) {
  return useQuery({
    queryKey: agentStatusQueryKey(taskId, tenantId),
    queryFn: () => getAgentStatus(taskId, tenantId),
    enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (!status) return 2_000;
      return TERMINAL_AGENT_STATUSES.includes(status) ? false : 2_000;
    },
  });
}
