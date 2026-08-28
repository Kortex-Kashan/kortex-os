import { useQuery } from "@tanstack/react-query";
import { listWorkflowDefinitions, WorkflowAccessDeniedError } from "../api";

/** No Workflow Engine event is wired to trigger an automatic refresh in
 * this milestone (see `apps/desktop/src/hooks/useKortexEventStream.ts`) —
 * "refresh" is the explicit, user-triggered `refetch()` this hook exposes
 * via TanStack Query. */
export const WORKFLOWS_QUERY_KEY = ["workflow", "definitions"] as const;

/**
 * Server-derived state for the Workflow workspace, per ADR-0002 §12
 * (TanStack Query owns all server-derived state). Exposes exactly
 * TanStack Query's own loading/success/error/refetch surface — `WorkflowApp`
 * derives "empty" itself from `data.length === 0`.
 */
export function useWorkflows() {
  return useQuery({
    queryKey: WORKFLOWS_QUERY_KEY,
    queryFn: listWorkflowDefinitions,
    // An access-denied result is deterministic — see
    // `features/connectors/hooks/useConnectors.ts`'s identical rationale.
    retry: (failureCount, error) => !(error instanceof WorkflowAccessDeniedError) && failureCount < 1,
  });
}
