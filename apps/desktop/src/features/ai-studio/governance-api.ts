/**
 * IPC wrappers for M5.5 AI Governance capabilities (M5.6 desktop consumer).
 */

import { invokeCapability } from "@/ipc/client";
import type { AIDecisionAuditRecord, AIGovernancePolicy, AITenantQuota } from "./governance-types";
import type { IpcResultEnvelope } from "@/ipc/client";

export class AiGovernanceAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AiGovernanceAccessDeniedError";
  }
}

export class AiGovernanceRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AiGovernanceRequestError";
  }
}

function extractResult(envelope: IpcResultEnvelope, cap: string): unknown {
  if (envelope.status === "SUCCESS") return envelope.payload?.result ?? null;
  const failure = envelope.errors[0];
  const message = failure?.message ?? `Capability ${cap} failed.`;
  if (failure?.category === "PERMISSION_DENIED") throw new AiGovernanceAccessDeniedError(message);
  throw new AiGovernanceRequestError(message);
}

async function invoke(cap: string, params: Record<string, unknown> = {}): Promise<unknown> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: cap,
    parameters: params,
  });
  return extractResult(envelope, cap);
}

// ---------------------------------------------------------------------------
// Raw wire → camelCase mappers
// ---------------------------------------------------------------------------

interface RawPolicy {
  tenant_id: string;
  strict_local_only?: boolean;
  require_human_approval_for_mutations?: boolean;
  banned_prompt_patterns?: string[];
  pii_redaction_enabled?: boolean;
  allowed_tools?: string[] | null;
  blocked_tools?: string[];
  max_tokens_per_request?: number;
  max_daily_budget_tokens?: number;
}

interface RawQuota {
  tenant_id: string;
  daily_token_limit?: number;
  monthly_token_limit?: number;
  daily_tokens_consumed?: number;
  monthly_tokens_consumed?: number;
  last_reset_date?: string;
  max_concurrent_agents?: number;
  max_concurrent_generations?: number;
}

interface RawAuditRecord {
  record_id: string;
  tenant_id: string;
  user_id?: string | null;
  task_id?: string | null;
  prompt_hash?: string;
  output_hash?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  latency_ms?: number;
  tool_calls_requested?: Array<{ tool: string; args: string }>;
  policy_violations?: string[];
  created_at: string;
}

function toPolicy(raw: RawPolicy): AIGovernancePolicy {
  return {
    tenantId: raw.tenant_id,
    strictLocalOnly: raw.strict_local_only ?? false,
    requireHumanApprovalForMutations: raw.require_human_approval_for_mutations ?? false,
    bannedPromptPatterns: raw.banned_prompt_patterns ?? [],
    piiRedactionEnabled: raw.pii_redaction_enabled ?? false,
    allowedTools: raw.allowed_tools ?? null,
    blockedTools: raw.blocked_tools ?? [],
    maxTokensPerRequest: raw.max_tokens_per_request ?? 4096,
    maxDailyBudgetTokens: raw.max_daily_budget_tokens ?? 1_000_000,
  };
}

function toQuota(raw: RawQuota): AITenantQuota {
  return {
    tenantId: raw.tenant_id,
    dailyTokenLimit: raw.daily_token_limit ?? 1_000_000,
    monthlyTokenLimit: raw.monthly_token_limit ?? 30_000_000,
    dailyTokensConsumed: raw.daily_tokens_consumed ?? 0,
    monthlyTokensConsumed: raw.monthly_tokens_consumed ?? 0,
    lastResetDate: raw.last_reset_date ?? "",
    maxConcurrentAgents: raw.max_concurrent_agents ?? 10,
    maxConcurrentGenerations: raw.max_concurrent_generations ?? 5,
  };
}

function toAuditRecord(raw: RawAuditRecord): AIDecisionAuditRecord {
  return {
    recordId: raw.record_id,
    tenantId: raw.tenant_id,
    userId: raw.user_id ?? null,
    taskId: raw.task_id ?? null,
    promptHash: raw.prompt_hash ?? "",
    outputHash: raw.output_hash ?? "",
    promptTokens: raw.prompt_tokens ?? 0,
    completionTokens: raw.completion_tokens ?? 0,
    totalTokens: raw.total_tokens ?? 0,
    latencyMs: raw.latency_ms ?? 0,
    toolCallsRequested: raw.tool_calls_requested ?? [],
    policyViolations: raw.policy_violations ?? [],
    createdAt: raw.created_at,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function getGovernancePolicy(tenantId: string): Promise<AIGovernancePolicy | null> {
  try {
    const raw = await invoke("kortex.ai.governance.policy.get", { tenant_id: tenantId });
    if (!raw) return null;
    return toPolicy(raw as RawPolicy);
  } catch (e) {
    if (e instanceof AiGovernanceAccessDeniedError) throw e;
    return null;
  }
}

export async function getTenantQuota(tenantId: string): Promise<AITenantQuota> {
  const raw = await invoke("kortex.ai.governance.quota.get", { tenant_id: tenantId });
  return toQuota(raw as RawQuota);
}

export async function queryDecisionAuditRecords(
  tenantId: string,
  limit = 50,
): Promise<AIDecisionAuditRecord[]> {
  const raw = await invoke("kortex.ai.governance.audit.query", {
    tenant_id: tenantId,
    limit,
  });
  const arr = Array.isArray(raw) ? raw : [];
  return (arr as RawAuditRecord[]).map(toAuditRecord);
}
