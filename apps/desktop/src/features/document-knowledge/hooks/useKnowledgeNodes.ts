import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/auth/AuthProvider";
import { KnowledgeAccessDeniedError, listKnowledgeNodes } from "../knowledgeApi";

export const KNOWLEDGE_NODES_QUERY_KEY = ["document-knowledge", "knowledge-nodes"] as const;

/**
 * Server-derived state for the Knowledge Graph's entity registry
 * (`kortex.knowledge.graph.list`, Slice 4.7). Scoped to the current
 * session's own tenant — `AuthGate` guarantees `state.status ===
 * "AUTHENTICATED"` for every component that can mount this hook (this
 * workspace only ever renders inside the authenticated shell), so
 * `identity` is only ever `null` in a state this hook cannot actually be
 * reached from; the guard exists for type-safety, not a real runtime path.
 */
export function useKnowledgeNodes() {
  const { state } = useAuth();
  const tenantId = state.status === "AUTHENTICATED" ? (state.identity?.tenantId ?? null) : null;

  return useQuery({
    queryKey: [...KNOWLEDGE_NODES_QUERY_KEY, tenantId],
    queryFn: () => listKnowledgeNodes(tenantId as string),
    enabled: tenantId !== null,
    retry: (failureCount, error) => !(error instanceof KnowledgeAccessDeniedError) && failureCount < 1,
  });
}
