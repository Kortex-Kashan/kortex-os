"""AI Governance, Safety Guardrails, Quota Enforcement, and Decision Lineage.

This module implements the core AI Governance subsystem for KORTEX OS (Milestone M5.5):
- Multi-tenant AI governance policies (`AIGovernancePolicy`)
- Content safety guardrails & prompt injection / PII redaction (`ContentSafetyGuardrail`)
- Tool authorization & mutation approval checkpoints (`ToolGovernanceEvaluator`)
- Tenant token budget & quota tracking (`TenantQuotaManager`)
- Immutable AI decision audit lineage & provenance (`AIDecisionAuditRecord`)
- Durable human approval policy bridge (`DurableAIApprovalPolicy`)

Security Invariants:
- AST isolated: Never imports `kortex.core.kernel`, `kortex.engines.security`, or `kortex.engines.knowledge`.
- Fail-closed: Missing policies fall back to strict platform defaults.
- All secrets & PII scrubbed before persistence and external transmission.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.ai.agent import (
    AgentTask,
    IApprovalPolicy,
)
from kortex.engines.ai.exceptions import (
    AIGovernanceQuotaExceededError,
    AIPolicyViolationError,
)
from kortex.engines.ai.models import LLMRequest, TokenUsage
from kortex.engines.ai.tools import ToolCall, ToolRegistry, scrub_secrets_from_text

logger = logging.getLogger("kortex.engines.ai.governance")


# ---------------------------------------------------------------------------
# Governance Protocols & Interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class IPolicyProvider(Protocol):
    """Protocol for fetching tenant AI governance policies."""

    async def get_policy(self, tenant_id: str) -> AIGovernancePolicy | None: ...


@runtime_checkable
class IDurableApprovalBridge(Protocol):
    """Protocol for creating durable human approval requests."""

    async def create_request(
        self,
        instance_id: str,
        step_id: str,
        required_role: str,
        tenant_id: str,
        context: dict[str, Any] | None = None,
    ) -> object: ...


@runtime_checkable
class IQuotaStore(Protocol):
    """Protocol for relational quota persistence."""

    async def get_quota(self, tenant_id: str) -> AITenantQuota | None: ...
    async def save_quota(self, quota: AITenantQuota) -> AITenantQuota: ...


@runtime_checkable
class IGovernanceStore(IQuotaStore, Protocol):
    """Protocol for relational governance storage."""

    async def get_policy(self, tenant_id: str) -> AIGovernancePolicy | None: ...
    async def save_policy(
        self,
        policy: AIGovernancePolicy,
        outbox_store: object | None = None,
    ) -> AIGovernancePolicy: ...
    async def save_decision_record(
        self,
        record: AIDecisionAuditRecord,
        outbox_store: object | None = None,
    ) -> None: ...
    async def query_decision_records(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
        user_id: str | None = None,
        task_id: str | None = None,
    ) -> list[AIDecisionAuditRecord]: ...



# ---------------------------------------------------------------------------
# Default Safety Patterns & Regexes
# ---------------------------------------------------------------------------

_PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+(instructions|prompts|directives)", re.IGNORECASE),
    re.compile(r"system\s*:\s*override", re.IGNORECASE),
    re.compile(r"bypass\s+(safety|content|security|policy)\s+(filter|guardrail|checks)", re.IGNORECASE),
    re.compile(r"act\s+as\s+(dan|jailbreak|unrestricted|god\s+mode)", re.IGNORECASE),
    re.compile(r"disregard\s+(the\s+)?(above|system)\s+(instructions|prompt)", re.IGNORECASE),
]

_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[REDACTED_CARD]"),
]


# ---------------------------------------------------------------------------
# Domain Models
# ---------------------------------------------------------------------------


class AIGovernancePolicy(BaseModel):
    """Tenant-specific AI Governance and Guardrail Policy."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4, description="Policy UUID")
    tenant_id: str = Field(min_length=1, description="Tenant identifier")
    strict_local_only: bool = Field(
        default=False, description="Disallow external cloud providers regardless of request flags"
    )
    require_human_approval_for_mutations: bool = Field(
        default=True, description="Require human approval for tools flagged as mutations"
    )
    banned_prompt_patterns: list[str] = Field(
        default_factory=list, description="Custom regex or keyword patterns forbidden in prompts"
    )
    pii_redaction_enabled: bool = Field(
        default=True, description="Automatically redact detected PII from prompt and outputs"
    )
    allowed_tools: list[str] | None = Field(
        default=None, description="Explicit allowlist of tool names (None = all non-blocked allowed)"
    )
    blocked_tools: list[str] = Field(
        default_factory=list, description="Explicit blocklist of forbidden tool names"
    )
    max_tokens_per_request: int = Field(
        default=4096, ge=128, le=32768, description="Maximum token generation limit per request"
    )
    max_daily_budget_tokens: int = Field(
        default=1_000_000, ge=1000, description="Daily token quota ceiling for the tenant"
    )
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC), description="Creation timestamp"
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC), description="Update timestamp"
    )


class AITenantQuota(BaseModel):
    """Tenant token consumption quota and concurrent workload tracking."""

    model_config = ConfigDict(frozen=False)

    tenant_id: str = Field(min_length=1)
    daily_token_limit: int = Field(default=1_000_000, ge=0)
    monthly_token_limit: int = Field(default=25_000_000, ge=0)
    daily_tokens_consumed: int = Field(default=0, ge=0)
    monthly_tokens_consumed: int = Field(default=0, ge=0)
    last_reset_date: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    )
    max_concurrent_agents: int = Field(default=5, ge=1)
    max_concurrent_generations: int = Field(default=10, ge=1)


class AIDecisionAuditRecord(BaseModel):
    """Immutable audit trail record for an AI reasoning turn or agent action."""

    model_config = ConfigDict(frozen=True)

    record_id: UUID = Field(default_factory=uuid4, description="Audit record UUID")
    tenant_id: str = Field(min_length=1, description="Tenant identifier")
    user_id: str = Field(default="SYSTEM", description="User or principal identity")
    task_id: str | None = Field(default=None, description="Linked agent task ID if applicable")
    request_id: str | None = Field(default=None, description="Linked LLM request ID")
    correlation_id: str | None = Field(default=None, description="Correlation identifier")
    provider_id: str | None = Field(default=None, description="Provider ID executing generation")
    model_name: str | None = Field(default=None, description="Model identifier")
    prompt_hash: str = Field(default="", description="SHA-256 hash of input prompt")
    output_hash: str = Field(default="", description="SHA-256 hash of output completion")
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    tool_calls_requested: list[dict[str, Any]] = Field(default_factory=list)
    approval_request_id: UUID | None = Field(default=None, description="Linked approval ticket UUID")
    policy_violations: list[str] = Field(default_factory=list)
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )


class GuardrailEvaluationResult(BaseModel):
    """Outcome of evaluating prompt or output against safety policies."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    violations: list[str] = Field(default_factory=list)
    sanitized_content: str = ""
    redacted_count: int = 0


# ---------------------------------------------------------------------------
# Content Safety Guardrail
# ---------------------------------------------------------------------------


class ContentSafetyGuardrail:
    """Evaluates input prompts and completions against injection attacks, banned patterns, and PII."""

    @staticmethod
    def evaluate_text(
        text: str,
        policy: AIGovernancePolicy | None = None,
    ) -> GuardrailEvaluationResult:
        """Scan and sanitize text against prompt injection patterns, banned rules, and PII."""
        if not text:
            return GuardrailEvaluationResult(passed=True, sanitized_content="")

        violations: list[str] = []
        sanitized = scrub_secrets_from_text(text)
        redacted_count = 0

        # 1. Prompt Injection Checks
        for pattern in _PROMPT_INJECTION_PATTERNS:
            if pattern.search(sanitized):
                violations.append(f"Prompt injection attempt detected matching: {pattern.pattern}")

        # 2. Custom Banned Patterns
        if policy and policy.banned_prompt_patterns:
            for custom_pat in policy.banned_prompt_patterns:
                try:
                    if re.search(custom_pat, sanitized, re.IGNORECASE):
                        violations.append(f"Banned pattern matched: {custom_pat}")
                except re.error:
                    if custom_pat.lower() in sanitized.lower():
                        violations.append(f"Banned keyword matched: {custom_pat}")

        # 3. PII Redaction
        if policy is None or policy.pii_redaction_enabled:
            for pii_regex, replacement in _PII_PATTERNS:
                matches = list(pii_regex.finditer(sanitized))
                if matches:
                    redacted_count += len(matches)
                    sanitized = pii_regex.sub(replacement, sanitized)

        passed = len(violations) == 0
        return GuardrailEvaluationResult(
            passed=passed,
            violations=violations,
            sanitized_content=sanitized,
            redacted_count=redacted_count,
        )


# ---------------------------------------------------------------------------
# Tool Governance Evaluator
# ---------------------------------------------------------------------------


class ToolGovernanceEvaluator:
    """Enforces tool allowlists, blocklists, and mutation human-approval gates."""

    def __init__(self, tool_registry: ToolRegistry | None = None) -> None:
        self._tool_registry = tool_registry

    def evaluate_tool_calls(
        self,
        tool_calls: list[ToolCall],
        policy: AIGovernancePolicy | None = None,
    ) -> tuple[bool, list[str], bool]:
        """Evaluate a list of tool calls against governance policy.

        Returns:
            (is_allowed, violations, requires_human_approval)
        """
        violations: list[str] = []
        requires_approval = False

        allowed_tools = set(policy.allowed_tools) if policy and policy.allowed_tools is not None else None
        blocked_tools = set(policy.blocked_tools) if policy and policy.blocked_tools else set()
        require_approval_mutations = policy.require_human_approval_for_mutations if policy else True

        for call in tool_calls:
            tname = call.tool_name

            # Check explicit blocklist
            if tname in blocked_tools:
                violations.append(f"Tool '{tname}' is explicitly blocked by policy.")

            # Check explicit allowlist
            if allowed_tools is not None and tname not in allowed_tools:
                violations.append(f"Tool '{tname}' is not in the allowed tools list.")

            # Check mutation flag
            if self._tool_registry is not None:
                try:
                    tool_def = self._tool_registry.get_tool(tname)
                    if tool_def.is_mutation and require_approval_mutations:
                        requires_approval = True
                except Exception:
                    # Unknown tool defaults to requiring approval for safety
                    if require_approval_mutations:
                        requires_approval = True

        is_allowed = len(violations) == 0
        return is_allowed, violations, requires_approval


# ---------------------------------------------------------------------------
# Durable AI Approval Policy
# ---------------------------------------------------------------------------


class DurableAIApprovalPolicy(IApprovalPolicy):
    """Adapter for `IApprovalPolicy` integrating AI Agent tasks with the KORTEX Approval System."""

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        policy_provider: IPolicyProvider | None = None,
        approval_manager: IDurableApprovalBridge | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._policy_provider = policy_provider
        self._approval_manager = approval_manager

    async def requires_approval(
        self,
        task: AgentTask,
        proposed_calls: list[ToolCall],
    ) -> bool:
        """Evaluate if proposed tool calls require durable human approval."""
        if not task.require_human_approval_for_mutations:
            return False

        # If policy provider given, retrieve tenant policy
        policy: AIGovernancePolicy | None = None
        if self._policy_provider is not None:
            try:
                if hasattr(self._policy_provider, "get_policy"):
                    policy = await self._policy_provider.get_policy(task.tenant_id)
            except Exception as exc:
                logger.warning("Could not fetch policy for tenant '%s': %s", task.tenant_id, exc)

        evaluator = ToolGovernanceEvaluator(self._tool_registry)
        is_allowed, violations, requires_approval = evaluator.evaluate_tool_calls(proposed_calls, policy)

        if not is_allowed:
            raise AIPolicyViolationError(task.tenant_id, violations)

        if requires_approval and self._approval_manager is not None:
            # Create durable approval request if manager available
            try:
                calls_summary = [
                    {"tool": c.tool_name, "args": scrub_secrets_from_text(json.dumps(c.arguments))}
                    for c in proposed_calls
                ]
                await self._approval_manager.create_request(
                    instance_id=task.task_id,
                    step_id=task.task_id,
                    required_role="ai_approver",
                    tenant_id=task.tenant_id,
                    context={
                        "action": "ai_tool_invocation",
                        "task_id": task.task_id,
                        "goal": task.goal,
                        "proposed_calls": calls_summary,
                    },
                )
            except Exception as exc:
                logger.error("Failed to stage durable approval request for task '%s': %s", task.task_id, exc)

        return requires_approval


# ---------------------------------------------------------------------------
# In-Memory / Relational Quota Manager
# ---------------------------------------------------------------------------


class TenantQuotaManager:
    """Manages multi-tenant token consumption, daily roll-over, and budget limits."""

    def __init__(self, quota_store: IQuotaStore | None = None) -> None:
        self._quota_store = quota_store
        self._memory_quotas: dict[str, AITenantQuota] = {}

    async def get_or_create_quota(self, tenant_id: str) -> AITenantQuota:
        """Get or initialize tenant quota record."""
        if self._quota_store is not None:
            q = await self._quota_store.get_quota(tenant_id)
            if q is not None:
                return q

        if tenant_id not in self._memory_quotas:
            self._memory_quotas[tenant_id] = AITenantQuota(tenant_id=tenant_id)
        return self._memory_quotas[tenant_id]

    async def check_and_record_consumption(
        self,
        tenant_id: str,
        token_usage: TokenUsage,
        policy: AIGovernancePolicy | None = None,
    ) -> None:
        """Verify token budget limits and record consumption."""
        quota = await self.get_or_create_quota(tenant_id)
        today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")

        # Daily rollover
        if quota.last_reset_date != today:
            quota.daily_tokens_consumed = 0
            quota.last_reset_date = today

        daily_limit = policy.max_daily_budget_tokens if policy else quota.daily_token_limit

        new_daily = quota.daily_tokens_consumed + token_usage.total_tokens
        if new_daily > daily_limit:
            raise AIGovernanceQuotaExceededError(
                tenant_id,
                f"Daily limit of {daily_limit} tokens exceeded (attempted {new_daily}).",
            )

        quota.daily_tokens_consumed = new_daily
        quota.monthly_tokens_consumed += token_usage.total_tokens

        if self._quota_store is not None:
            await self._quota_store.save_quota(quota)


# ---------------------------------------------------------------------------
# AI Governance Manager Facade
# ---------------------------------------------------------------------------


class AIGovernanceManager:
    """Coordinates AI policies, content guardrails, quotas, and decision audit records."""

    def __init__(
        self,
        governance_store: IGovernanceStore | None = None,
        tool_registry: ToolRegistry | None = None,
        approval_manager: IDurableApprovalBridge | None = None,
    ) -> None:
        self._store = governance_store
        self._tool_registry = tool_registry
        self._approval_manager = approval_manager
        self._memory_policies: dict[str, AIGovernancePolicy] = {}
        self._quota_manager = TenantQuotaManager(governance_store)


    @property
    def quota_manager(self) -> TenantQuotaManager:
        return self._quota_manager

    def create_approval_policy(self) -> DurableAIApprovalPolicy:
        """Create a durable approval policy wired to this governance manager."""
        return DurableAIApprovalPolicy(
            tool_registry=self._tool_registry,
            policy_provider=self,
            approval_manager=self._approval_manager,
        )

    async def get_policy(self, tenant_id: str) -> AIGovernancePolicy:
        """Retrieve governance policy for a tenant (or default policy)."""
        if self._store is not None:
            p = await self._store.get_policy(tenant_id)
            if p is not None:
                return p

        if tenant_id not in self._memory_policies:
            self._memory_policies[tenant_id] = AIGovernancePolicy(tenant_id=tenant_id)
        return self._memory_policies[tenant_id]

    async def set_policy(self, policy: AIGovernancePolicy) -> AIGovernancePolicy:
        """Save or update tenant governance policy."""
        if self._store is not None:
            return await self._store.save_policy(policy)
        self._memory_policies[policy.tenant_id] = policy
        return policy

    async def evaluate_prompt_guardrails(
        self,
        request: LLMRequest,
    ) -> GuardrailEvaluationResult:
        """Check prompt and system instructions against tenant safety guardrails."""
        policy = await self.get_policy(request.tenant_id)

        # Evaluate prompt
        res_prompt = ContentSafetyGuardrail.evaluate_text(request.prompt, policy)
        if not res_prompt.passed:
            raise AIPolicyViolationError(request.tenant_id, res_prompt.violations)

        # Evaluate system instruction if present
        if request.system_instruction:
            res_sys = ContentSafetyGuardrail.evaluate_text(request.system_instruction, policy)
            if not res_sys.passed:
                raise AIPolicyViolationError(request.tenant_id, res_sys.violations)

        return res_prompt

    async def evaluate_output_guardrails(
        self,
        tenant_id: str,
        text: str,
    ) -> GuardrailEvaluationResult:
        """Sanitize completion text against PII and safety rules."""
        policy = await self.get_policy(tenant_id)
        return ContentSafetyGuardrail.evaluate_text(text, policy)

    async def log_decision(
        self,
        tenant_id: str,
        user_id: str,
        prompt_text: str,
        output_text: str | None = None,
        provider_id: str | None = None,
        model_name: str | None = None,
        task_id: str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        token_usage: TokenUsage | None = None,
        latency_ms: float = 0.0,
        tool_calls: list[ToolCall] | None = None,
        approval_request_id: UUID | None = None,
        policy_violations: list[str] | None = None,
    ) -> AIDecisionAuditRecord:
        """Record an immutable audit entry and stage outbox event."""
        p_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() if prompt_text else ""
        o_hash = hashlib.sha256((output_text or "").encode("utf-8")).hexdigest() if output_text else ""

        usage = token_usage or TokenUsage()
        raw_tools = [
            {"tool": c.tool_name, "args": scrub_secrets_from_text(json.dumps(c.arguments))}
            for c in (tool_calls or [])
        ]

        record = AIDecisionAuditRecord(
            record_id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            task_id=task_id,
            request_id=request_id,
            correlation_id=correlation_id,
            provider_id=provider_id,
            model_name=model_name,
            prompt_hash=p_hash,
            output_hash=o_hash,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            latency_ms=latency_ms,
            tool_calls_requested=raw_tools,
            approval_request_id=approval_request_id,
            policy_violations=policy_violations or [],
        )

        if self._store is not None:
            await self._store.save_decision_record(record)

        return record
