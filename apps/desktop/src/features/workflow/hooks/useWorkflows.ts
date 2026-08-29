import { useQuery } from "@tanstack/react-query";
import {
  listWorkflowDefinitions,
  listWorkflowInstances,
  WorkflowAccessDeniedError,
} from "../api";

export const WORKFLOWS_QUERY_KEY = ["workflow", "definitions"] as const;

/**
 * Server-derived state for the Workflow definition catalog. Exposes TanStack
 * Query's loading/success/error/refetch surface — WorkflowApp derives "empty"
 * itself from `data.length === 0`.
 */
export function useWorkflows() {
  return useQuery({
    queryKey: WORKFLOWS_QUERY_KEY,
    queryFn: listWorkflowDefinitions,
    retry: (failureCount, error) =>
      !(error instanceof WorkflowAccessDeniedError) && failureCount < 1,
  });
}

export const INSTANCES_QUERY_KEY = (filters?: {
  workflowId?: string;
  status?: string;
}) => ["workflow", "instances", filters ?? {}] as const;

/**
 * Fetches workflow execution instances with optional status/workflow filtering.
 * Polls every 10 seconds for live updates when active instances may exist.
 */
export function useWorkflowInstances(filters?: { workflowId?: string; status?: string }) {
  return useQuery({
    queryKey: INSTANCES_QUERY_KEY(filters),
    queryFn: () => listWorkflowInstances({ ...filters, limit: 100 }),
    retry: (failureCount, error) =>
      !(error instanceof WorkflowAccessDeniedError) && failureCount < 1,
    refetchInterval: 10_000,
  });
}
