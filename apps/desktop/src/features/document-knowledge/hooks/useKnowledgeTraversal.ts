import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/auth/AuthProvider";
import { KnowledgeAccessDeniedError, traverseKnowledgeGraph } from "../knowledgeApi";

export const KNOWLEDGE_TRAVERSAL_QUERY_KEY = ["document-knowledge", "knowledge-traversal"] as const;

const MAX_HOPS = 2;

/**
 * Relationship exploration outward from a selected entity
 * (`kortex.knowledge.graph.traverse`, Slice 4.7). `selectedNodeId` is
 * `null` until the user picks a node from `useKnowledgeNodes`'s list —
 * the query stays disabled (never "loading") until then, since there is
 * nothing to traverse from yet. See `useKnowledgeNodes.ts` for why the
 * tenantId null-guard is a type-safety measure, not a real runtime path.
 */
export function useKnowledgeTraversal(selectedNodeId: string | null) {
  const { state } = useAuth();
  const tenantId = state.status === "AUTHENTICATED" ? (state.identity?.tenantId ?? null) : null;

  return useQuery({
    queryKey: [...KNOWLEDGE_TRAVERSAL_QUERY_KEY, tenantId, selectedNodeId],
    queryFn: () => traverseKnowledgeGraph(selectedNodeId as string, tenantId as string, MAX_HOPS),
    enabled: tenantId !== null && selectedNodeId !== null,
    retry: (failureCount, error) => !(error instanceof KnowledgeAccessDeniedError) && failureCount < 1,
  });
}
