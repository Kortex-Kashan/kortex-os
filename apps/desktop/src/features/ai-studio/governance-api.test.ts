/**
 * M5.6 — AI Governance API tests.
 *
 * Tests snake_case → camelCase mapping and error dispatch for governance-api.ts.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import {
  AiGovernanceAccessDeniedError,
  AiGovernanceRequestError,
  getGovernancePolicy,
  getTenantQuota,
  queryDecisionAuditRecords,
} from "./governance-api";

function ok(result: unknown) {
  return {
    requestId: "r1",
    correlationId: "c1",
    status: "SUCCESS" as const,
    payload: { result },
    errors: [],
    warnings: [],
    executionDurationMs: 1,
  };
}

function fail(category: string, message: string) {
  return {
    requestId: "r1",
    correlationId: "c1",
    status: "FAILURE" as const,
    payload: null,
    errors: [{ category, message, correlationId: "c1" }],
    warnings: [],
    executionDurationMs: 1,
  };
}

beforeEach(() => {
  invokeCapabilityMock.mockReset();
});

const rawPolicy = {
  tenant_id: "acme",
  strict_local_only: true,
  require_human_approval_for_mutations: true,
  banned_prompt_patterns: ["jailbreak"],
  pii_redaction_enabled: true,
  allowed_tools: ["kortex.search"],
  blocked_tools: ["shell"],
  max_tokens_per_request: 2048,
  max_daily_budget_tokens: 500_000,
};

describe("getGovernancePolicy", () => {
  it("maps policy snake_case to camelCase AIGovernancePolicy", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(rawPolicy));
    const policy = await getGovernancePolicy("acme");
    expect(policy).not.toBeNull();
    expect(policy!.tenantId).toBe("acme");
    expect(policy!.strictLocalOnly).toBe(true);
    expect(policy!.piiRedactionEnabled).toBe(true);
    expect(policy!.bannedPromptPatterns).toEqual(["jailbreak"]);
    expect(policy!.allowedTools).toEqual(["kortex.search"]);
    expect(policy!.maxTokensPerRequest).toBe(2048);
  });

  it("returns null on no-result SUCCESS", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(null));
    expect(await getGovernancePolicy("acme")).toBeNull();
  });

  it("throws AiGovernanceAccessDeniedError on PERMISSION_DENIED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(fail("PERMISSION_DENIED", "no"));
    await expect(getGovernancePolicy("acme")).rejects.toBeInstanceOf(AiGovernanceAccessDeniedError);
  });

  it("returns null on generic failure (not access-denied)", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(fail("EXECUTION_FAILED", "boom"));
    expect(await getGovernancePolicy("acme")).toBeNull();
  });
});

const rawQuota = {
  tenant_id: "acme",
  daily_token_limit: 1_000_000,
  monthly_token_limit: 30_000_000,
  daily_tokens_consumed: 50_000,
  monthly_tokens_consumed: 200_000,
  last_reset_date: "2026-08-01",
  max_concurrent_agents: 5,
  max_concurrent_generations: 2,
};

describe("getTenantQuota", () => {
  it("maps quota snake_case to camelCase AITenantQuota", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok(rawQuota));
    const quota = await getTenantQuota("acme");
    expect(quota.tenantId).toBe("acme");
    expect(quota.dailyTokenLimit).toBe(1_000_000);
    expect(quota.dailyTokensConsumed).toBe(50_000);
    expect(quota.maxConcurrentAgents).toBe(5);
    expect(quota.lastResetDate).toBe("2026-08-01");
  });

  it("throws AiGovernanceAccessDeniedError on PERMISSION_DENIED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(fail("PERMISSION_DENIED", "no"));
    await expect(getTenantQuota("acme")).rejects.toBeInstanceOf(AiGovernanceAccessDeniedError);
  });

  it("throws AiGovernanceRequestError on generic failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(fail("EXECUTION_FAILED", "boom"));
    await expect(getTenantQuota("acme")).rejects.toBeInstanceOf(AiGovernanceRequestError);
  });
});

const rawAuditRecord = {
  record_id: "rec-1",
  tenant_id: "acme",
  user_id: "alice",
  task_id: "task-1",
  request_id: "req-1",
  correlation_id: "corr-1",
  provider_id: "ollama-llama3",
  model_name: "llama3",
  prompt_hash: "abc123",
  output_hash: "def456",
  prompt_tokens: 100,
  completion_tokens: 200,
  total_tokens: 300,
  latency_ms: 1200,
  tool_calls_requested: [{ tool: "search", args: '{"q":"x"}' }],
  approval_request_id: "appr-1",
  policy_violations: ["pii_detected"],
  created_at: "2026-01-01T00:00:00Z",
};

describe("queryDecisionAuditRecords", () => {
  it("maps audit record snake_case to camelCase AIDecisionAuditRecord[]", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([rawAuditRecord]));
    const records = await queryDecisionAuditRecords("acme", 10);
    expect(records).toHaveLength(1);
    const r = records[0];
    expect(r.recordId).toBe("rec-1");
    expect(r.promptHash).toBe("abc123");
    expect(r.totalTokens).toBe(300);
    expect(r.policyViolations).toEqual(["pii_detected"]);
    expect(r.toolCallsRequested).toEqual([{ tool: "search", args: '{"q":"x"}' }]);
    // M6.1-3: provider/model/request/correlation/approval fields must now
    // survive the snake_case -> camelCase mapping instead of being dropped.
    expect(r.requestId).toBe("req-1");
    expect(r.correlationId).toBe("corr-1");
    expect(r.providerId).toBe("ollama-llama3");
    expect(r.modelName).toBe("llama3");
    expect(r.approvalRequestId).toBe("appr-1");
  });

  it("maps missing provider/model/request/correlation/approval fields to null, not undefined", async () => {
    const { provider_id, model_name, request_id, correlation_id, approval_request_id, ...withoutNewFields } =
      rawAuditRecord;
    invokeCapabilityMock.mockResolvedValueOnce(ok([withoutNewFields]));
    const [r] = await queryDecisionAuditRecords("acme", 10);
    expect(r.providerId).toBeNull();
    expect(r.modelName).toBeNull();
    expect(r.requestId).toBeNull();
    expect(r.correlationId).toBeNull();
    expect(r.approvalRequestId).toBeNull();
  });

  it("returns empty array for empty result", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(ok([]));
    expect(await queryDecisionAuditRecords("acme")).toEqual([]);
  });

  it("throws AiGovernanceAccessDeniedError on PERMISSION_DENIED", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(fail("PERMISSION_DENIED", "no"));
    await expect(queryDecisionAuditRecords("acme")).rejects.toBeInstanceOf(
      AiGovernanceAccessDeniedError,
    );
  });
});
