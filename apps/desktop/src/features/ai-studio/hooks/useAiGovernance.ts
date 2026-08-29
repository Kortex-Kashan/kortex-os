import { useQuery } from "@tanstack/react-query";
import {
  AiGovernanceAccessDeniedError,
  getGovernancePolicy,
  getTenantQuota,
  queryDecisionAuditRecords,
} from "../governance-api";

/** Uses the active session's tenantId (passed in as prop from session store). */
export const GOVERNANCE_POLICY_KEY = (tenantId: string) =>
  ["ai", "governance", "policy", tenantId] as const;

export const GOVERNANCE_QUOTA_KEY = (tenantId: string) =>
  ["ai", "governance", "quota", tenantId] as const;

export const GOVERNANCE_AUDIT_KEY = (tenantId: string) =>
  ["ai", "governance", "audit", tenantId] as const;

export function useGovernancePolicy(tenantId: string) {
  return useQuery({
    queryKey: GOVERNANCE_POLICY_KEY(tenantId),
    queryFn: () => getGovernancePolicy(tenantId),
    enabled: !!tenantId,
    retry: (failureCount, error) =>
      !(error instanceof AiGovernanceAccessDeniedError) && failureCount < 1,
  });
}

export function useTenantQuota(tenantId: string) {
  return useQuery({
    queryKey: GOVERNANCE_QUOTA_KEY(tenantId),
    queryFn: () => getTenantQuota(tenantId),
    enabled: !!tenantId,
    retry: (failureCount, error) =>
      !(error instanceof AiGovernanceAccessDeniedError) && failureCount < 1,
    refetchInterval: 30_000,
  });
}

export function useAuditRecords(tenantId: string, limit = 50) {
  return useQuery({
    queryKey: GOVERNANCE_AUDIT_KEY(tenantId),
    queryFn: () => queryDecisionAuditRecords(tenantId, limit),
    enabled: !!tenantId,
    retry: (failureCount, error) =>
      !(error instanceof AiGovernanceAccessDeniedError) && failureCount < 1,
  });
}
