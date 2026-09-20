import { useQuery } from "@tanstack/react-query";
import { listPythonActions } from "../api";

export const PYTHON_ACTIONS_QUERY_KEY = ["workflow-engine", "python-automation", "actions"] as const;

/** Server-derived list of the calling tenant's published Python Actions. */
export function usePythonActions() {
  return useQuery({
    queryKey: PYTHON_ACTIONS_QUERY_KEY,
    queryFn: () => listPythonActions(),
  });
}
