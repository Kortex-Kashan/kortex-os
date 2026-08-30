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
  requestId: string | null;
  correlationId: string | null;
  /** Which registered provider actually served this decision (e.g. "ollama-llama3"). Null for
   * records predating M6.1-2's real provider, or any generation the provider itself didn't self-report. */
  providerId: string | null;
  /** Which model the serving provider used (e.g. "llama3"). Same nullability caveat as providerId. */
  modelName: string | null;
  promptHash: string;
  outputHash: string;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  latencyMs: number;
  toolCallsRequested: Array<{ tool: string; args: string }>;
  approvalRequestId: string | null;
  policyViolations: string[];
  createdAt: string;
}
