import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cancelExternalExecution, listExternalExecutions, WorkflowAccessDeniedError } from "../api";
import type { ExternalExecutionStatus } from "../types";

export const EXTERNAL_EXECUTIONS_QUERY_KEY = (filters?: { status?: string }) =>
  ["workflow", "external-executions", filters ?? {}] as const;

/** Fetches governed external execution audit records. */
export function useExternalExecutions(filters?: { status?: ExternalExecutionStatus }) {
  return useQuery({
    queryKey: EXTERNAL_EXECUTIONS_QUERY_KEY(filters),
    queryFn: () => listExternalExecutions({ ...filters, limit: 100 }),
    retry: (failureCount, error) =>
      !(error instanceof WorkflowAccessDeniedError) && failureCount < 1,
    refetchInterval: 20_000,
  });
}

/** Cancels a running or pending governed external execution (M5-A7) — the
 * backend has always supported this (`kortex.workflow.external.cancel`); no
 * UI control existed to reach it. */
export function useCancelExternalExecution() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (executionId: string) => cancelExternalExecution(executionId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["workflow", "external-executions"] });
    },
  });
}
