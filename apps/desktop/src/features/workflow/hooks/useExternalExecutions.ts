import { useQuery } from "@tanstack/react-query";
import { listExternalExecutions, WorkflowAccessDeniedError } from "../api";

export const EXTERNAL_EXECUTIONS_QUERY_KEY = (filters?: { status?: string }) =>
  ["workflow", "external-executions", filters ?? {}] as const;

/** Fetches governed external execution audit records. Read-only view. */
export function useExternalExecutions(filters?: { status?: string }) {
  return useQuery({
    queryKey: EXTERNAL_EXECUTIONS_QUERY_KEY(filters),
    queryFn: () => listExternalExecutions({ ...filters, limit: 100 }),
    retry: (failureCount, error) =>
      !(error instanceof WorkflowAccessDeniedError) && failureCount < 1,
    refetchInterval: 20_000,
  });
}
