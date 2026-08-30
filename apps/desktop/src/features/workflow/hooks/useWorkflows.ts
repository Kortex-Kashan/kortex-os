import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelWorkflowInstance,
  listWorkflowDefinitions,
  listWorkflowInstances,
  WorkflowAccessDeniedError,
} from "../api";
import type { WorkflowState } from "../types";

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

export const INSTANCES_QUERY_KEY = (filters?: { state?: WorkflowState }) =>
  ["workflow", "instances", filters ?? {}] as const;

/**
 * Fetches workflow execution instances, optionally filtered by `state` — the
 * only filter `kortex.workflow.instance.list` actually supports server-side
 * (M5-A6). There is currently no server-side pagination for this capability;
 * `InstanceTimeline` applies client-side windowing over the full result so
 * the DOM stays bounded even though the network fetch does not.
 */
export function useWorkflowInstances(filters?: { state?: WorkflowState }) {
  return useQuery({
    queryKey: INSTANCES_QUERY_KEY(filters),
    queryFn: () => listWorkflowInstances(filters),
    retry: (failureCount, error) =>
      !(error instanceof WorkflowAccessDeniedError) && failureCount < 1,
    refetchInterval: 10_000,
  });
}

/** Cancels a workflow instance and invalidates the instance list so the
 * change is reflected immediately rather than waiting for the next poll. */
export function useCancelWorkflowInstance() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ instanceId, reason }: { instanceId: string; reason: string }) =>
      cancelWorkflowInstance(instanceId, reason),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["workflow", "instances"] });
    },
  });
}
