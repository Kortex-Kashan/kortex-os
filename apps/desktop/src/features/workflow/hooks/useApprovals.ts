import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  delegateApproval,
  listPendingApprovals,
  submitApprovalDecision,
  WorkflowAccessDeniedError,
} from "../api";
import type { ApprovalDecision } from "../types";

export const APPROVALS_QUERY_KEY = ["workflow", "approvals", "pending"] as const;

/**
 * Fetches pending human approval requests. Polls every 15 seconds so operators
 * see new requests without manual refresh.
 */
export function usePendingApprovals() {
  return useQuery({
    queryKey: APPROVALS_QUERY_KEY,
    queryFn: listPendingApprovals,
    retry: (failureCount, error) =>
      !(error instanceof WorkflowAccessDeniedError) && failureCount < 1,
    refetchInterval: 15_000,
  });
}

/** Submits an APPROVED or REJECTED decision for an approval request. */
export function useSubmitApprovalDecision() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      requestId,
      decision,
      rationale,
    }: {
      requestId: string;
      decision: ApprovalDecision;
      rationale: string;
    }) => submitApprovalDecision({ requestId, decision, rationale }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY });
    },
  });
}

/** Delegates an approval request to another principal. */
export function useDelegateApproval() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      requestId,
      delegateToPrincipalId,
      reason,
    }: {
      requestId: string;
      delegateToPrincipalId: string;
      reason: string;
    }) => delegateApproval({ requestId, delegateToPrincipalId, reason }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY });
    },
  });
}
