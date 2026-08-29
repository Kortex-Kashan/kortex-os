/**
 * TypeScript domain models for the AI Governance workspace (M5.6).
 *
 * Mirrors the safe subset of M5.5 backend models:
 *  - AIGovernancePolicy: tenant content safety and tool governance configuration
 *  - AITenantQuota: token budget consumption and limits
 *  - AIDecisionAuditRecord: immutable AI decision lineage records
 *
 * Sensitive fields (raw prompt content, LLM API keys, secret handles) are
 * deliberately excluded from all types.
 */

export interface AIGovernancePolicy {
  tenantId: string;
  strictLocalOnly: boolean;
  requireHumanApprovalForMutations: boolean;
  bannedPromptPatterns: string[];
  piiRedactionEnabled: boolean;
  allowedTools: string[] | null;
  blockedTools: string[];
  maxTokensPerRequest: number;
  maxDailyBudgetTokens: number;
}

export interface AITenantQuota {
  tenantId: string;
  dailyTokenLimit: number;
  monthlyTokenLimit: number;
  dailyTokensConsumed: number;
  monthlyTokensConsumed: number;
  lastResetDate: string;
  maxConcurrentAgents: number;
  maxConcurrentGenerations: number;
}

export interface AIDecisionAuditRecord {
  recordId: string;
  tenantId: string;
  userId: string | null;
  taskId: string | null;
  promptHash: string;
  outputHash: string;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  latencyMs: number;
  toolCallsRequested: Array<{ tool: string; args: string }>;
  policyViolations: string[];
  createdAt: string;
}
