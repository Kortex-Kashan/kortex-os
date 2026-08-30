/**
 * AI Governance Tab — token quota meters, safety policy overview, and
 * immutable AI decision audit log viewer (M5.5 backend consumed by M5.6 UI).
 *
 * Tenant ID is taken from the active session via a simple prop. In this
 * milestone there is no session store integration — callers pass tenantId
 * directly (tested with a fixed tenantId in Vitest).
 */

import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
} from "@kortex/design-system";
import {
  useAuditRecords,
  useGovernancePolicy,
  useTenantQuota,
} from "../hooks/useAiGovernance";
import { AiGovernanceAccessDeniedError } from "../governance-api";
import type { AIDecisionAuditRecord, AIGovernancePolicy, AITenantQuota } from "../governance-types";

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function AiGovernanceTab({ tenantId }: { tenantId: string }) {
  return (
    <div className="space-y-6" aria-label="AI Governance">
      <QuotaMeter tenantId={tenantId} />
      <PolicyOverview tenantId={tenantId} />
      <AuditLog tenantId={tenantId} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quota Meter
// ---------------------------------------------------------------------------

function QuotaMeter({ tenantId }: { tenantId: string }) {
  const { data, isPending, isError, error } = useTenantQuota(tenantId);

  return (
    <Card aria-label="Token Quota Meter">
      <CardHeader>
        <CardTitle>Token Budget</CardTitle>
        <CardDescription>Daily and monthly token consumption for this tenant.</CardDescription>
      </CardHeader>
      <CardContent>
        {isPending && <Skeleton className="h-16 w-full" />}
        {isError && (
          <p className="text-sm text-destructive" role="alert">
            {error instanceof AiGovernanceAccessDeniedError
              ? `Access denied: ${error.message}`
              : error.message}
          </p>
        )}
        {data && <QuotaDisplay quota={data} />}
      </CardContent>
    </Card>
  );
}

function QuotaDisplay({ quota }: { quota: AITenantQuota }) {
  const dailyPct = quota.dailyTokenLimit > 0
    ? Math.min(100, Math.round((quota.dailyTokensConsumed / quota.dailyTokenLimit) * 100))
    : 0;
  const monthlyPct = quota.monthlyTokenLimit > 0
    ? Math.min(100, Math.round((quota.monthlyTokensConsumed / quota.monthlyTokenLimit) * 100))
    : 0;

  return (
    <div className="space-y-4">
      <QuotaBar
        label="Daily"
        consumed={quota.dailyTokensConsumed}
        limit={quota.dailyTokenLimit}
        pct={dailyPct}
        resetDate={quota.lastResetDate}
      />
      <QuotaBar
        label="Monthly"
        consumed={quota.monthlyTokensConsumed}
        limit={quota.monthlyTokenLimit}
        pct={monthlyPct}
      />
      <div className="grid grid-cols-2 gap-x-4 text-sm text-muted-foreground">
        <span>Max Concurrent Agents</span>
        <span>{quota.maxConcurrentAgents}</span>
        <span>Max Concurrent Generations</span>
        <span>{quota.maxConcurrentGenerations}</span>
      </div>
    </div>
  );
}

function QuotaBar({
  label,
  consumed,
  limit,
  pct,
  resetDate,
}: {
  label: string;
  consumed: number;
  limit: number;
  pct: number;
  resetDate?: string;
}) {
  const isWarning = pct >= 80;
  const isCritical = pct >= 95;

  return (
    <div>
      <div className="flex justify-between text-sm mb-1">
        <span className="font-medium">{label}</span>
        <span className="text-muted-foreground">
          {consumed.toLocaleString()} / {limit.toLocaleString()} tokens ({pct}%)
        </span>
      </div>
      <div
        className="h-2 w-full rounded-full bg-muted overflow-hidden"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${label} token usage ${pct}%`}
      >
        <div
          className={`h-full rounded-full transition-all ${
            isCritical ? "bg-destructive" : isWarning ? "bg-yellow-500" : "bg-primary"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {resetDate && (
        <p className="text-xs text-muted-foreground mt-1">Last reset: {resetDate}</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Policy Overview
// ---------------------------------------------------------------------------

function PolicyOverview({ tenantId }: { tenantId: string }) {
  const { data, isPending, isError, error } = useGovernancePolicy(tenantId);

  return (
    <Card aria-label="Governance Policy">
      <CardHeader>
        <CardTitle>Safety Policy</CardTitle>
        <CardDescription>Active content safety and tool governance configuration.</CardDescription>
      </CardHeader>
      <CardContent>
        {isPending && <Skeleton className="h-16 w-full" />}
        {isError && (
          <p className="text-sm text-destructive" role="alert">
            {error instanceof AiGovernanceAccessDeniedError
              ? `Access denied: ${error.message}`
              : error.message}
          </p>
        )}
        {data === null && !isPending && !isError && (
          <p className="text-sm text-muted-foreground">No policy configured for this tenant.</p>
        )}
        {data && <PolicyDisplay policy={data} />}
      </CardContent>
    </Card>
  );
}

function PolicyDisplay({ policy }: { policy: AIGovernancePolicy }) {
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap gap-2">
        {policy.piiRedactionEnabled && (
          <Badge variant="secondary" aria-label="PII redaction enabled">PII Redaction</Badge>
        )}
        {policy.requireHumanApprovalForMutations && (
          <Badge variant="secondary" aria-label="Human approval required for mutations">
            Mutation Approval
          </Badge>
        )}
        {policy.strictLocalOnly && (
          <Badge variant="secondary" aria-label="Strict local-only mode">Local Only</Badge>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-muted-foreground">
        <span>Max Tokens/Request</span>
        <span>{policy.maxTokensPerRequest.toLocaleString()}</span>
        <span>Daily Budget</span>
        <span>{policy.maxDailyBudgetTokens.toLocaleString()}</span>
        <span>Banned Patterns</span>
        <span>{policy.bannedPromptPatterns.length}</span>
        <span>Blocked Tools</span>
        <span>{policy.blockedTools.length}</span>
        <span>Allowed Tools</span>
        <span>{policy.allowedTools === null ? "All" : policy.allowedTools.length}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Audit Log
// ---------------------------------------------------------------------------

function AuditLog({ tenantId }: { tenantId: string }) {
  const { data, isPending, isError, error } = useAuditRecords(tenantId, 50);

  return (
    <Card aria-label="AI Decision Audit Log">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Decision Audit Log</CardTitle>
          <CardDescription>Immutable AI decision lineage records with hash verification.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {isPending && <Skeleton className="h-24 w-full" />}
        {isError && (
          <div className="space-y-2" role="alert">
            <p className="text-sm text-destructive">
              {error instanceof AiGovernanceAccessDeniedError
                ? `Access denied: ${error.message}`
                : error.message}
            </p>
          </div>
        )}
        {data !== undefined && data.length === 0 && (
          <p className="text-sm text-muted-foreground" role="status">
            No audit records found.
          </p>
        )}
        {data && data.length > 0 && (
          <div className="space-y-2" role="list" aria-label="Audit records">
            {data.map((rec) => (
              <AuditRecordRow key={rec.recordId} record={rec} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AuditRecordRow({ record }: { record: AIDecisionAuditRecord }) {
  return (
    <div
      role="listitem"
      className="rounded-md border p-3 text-sm space-y-1"
      aria-label={`Audit record ${record.recordId}`}
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <span className="font-mono text-xs text-muted-foreground truncate">{record.recordId}</span>
        <span className="text-xs text-muted-foreground">
          {new Date(record.createdAt).toLocaleString()}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-x-4 text-xs text-muted-foreground">
        <span>Tokens: {record.totalTokens.toLocaleString()}</span>
        <span>Latency: {record.latencyMs.toFixed(0)}ms</span>
        <span>Violations: {record.policyViolations.length}</span>
      </div>
      <div className="text-xs text-muted-foreground">
        Provider: {record.providerId ?? "unknown"} · Model: {record.modelName ?? "unknown"}
      </div>
      <div className="text-xs text-muted-foreground font-mono truncate">
        Prompt: {record.promptHash} · Output: {record.outputHash}
      </div>
      {record.policyViolations.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {record.policyViolations.map((v, i) => (
            <Badge key={i} variant="destructive" className="text-xs">
              {v}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
