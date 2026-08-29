"""In-memory diagnostic metrics and health reporter for KORTEX AI Orchestration Engine.

Governed by Milestone 9.5 architecture specification:
docs/architecture/ai_engine_m9_production_runtime_spec.md

Conforms strictly to the IEngineDiagnostics protocol for operational health,
state metrics, technical diagnostics snapshots, and capability reporting.

Invariants:
- In-memory metrics only. Zero database connections, zero SQL tables.
- Thread-safe via internal reentrant synchronization.
- Recording methods are safe, atomic, and never raise exceptions.
- Diagnostics snapshots never expose API keys, bearer tokens, or secret handles.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Any

from kortex.engines.ai.interfaces import IEngineDiagnostics
from kortex.engines.ai.memory import AIMemoryManager
from kortex.engines.ai.registry import ProviderRegistry
from kortex.engines.ai.router import ModelRouter
from kortex.engines.ai.tools import ToolRegistry

logger = logging.getLogger("kortex.engines.ai.diagnostics")

CANONICAL_CAPABILITIES: list[str] = [
    "kortex.ai.response.generate",
    "kortex.ai.agent.orchestrate",
    "kortex.ai.agent.resume",
    "kortex.ai.tool.invoke",
    "kortex.ai.provider.register",
    "kortex.ai.provider.list",
    "kortex.ai.model.list",
    "kortex.ai.agent.cancel",
    "kortex.ai.agent.status",
    "kortex.ai.agent.list",
    "kortex.ai.governance.policy.evaluate",
    "kortex.ai.governance.policy.upsert",
    "kortex.ai.governance.policy.get",
    "kortex.ai.governance.quota.get",
    "kortex.ai.governance.quota.update",
    "kortex.ai.governance.audit.query",
    "kortex.ai.governance.guardrail.check",
    "kortex.ai.governance.approval.create",
]



class AIDiagnostics(IEngineDiagnostics):
    """Standardized in-memory thread-safe diagnostics provider for the AI Orchestration Engine."""

    def __init__(
        self,
        provider_registry: ProviderRegistry | None = None,
        model_router: ModelRouter | None = None,
        memory_manager: AIMemoryManager | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._provider_registry = provider_registry
        self._model_router = model_router
        self._memory_manager = memory_manager
        self._tool_registry = tool_registry

        # Request metrics
        self._generation_requests_total: int = 0
        self._generation_success_total: int = 0
        self._generation_failure_total: int = 0
        self._total_generation_latency_ms: float = 0.0
        self._min_generation_latency_ms: float | None = None
        self._max_generation_latency_ms: float | None = None

        # Agent task metrics
        self._agent_tasks_total: int = 0
        self._agent_completed_total: int = 0
        self._agent_failed_total: int = 0
        self._paused_agent_tasks: int = 0
        self._timed_out_agent_tasks: int = 0
        self._loop_detected_agent_tasks: int = 0
        self._step_limit_exceeded_tasks: int = 0
        self._total_agent_steps: int = 0
        self._total_agent_latency_ms: float = 0.0
        self._min_agent_latency_ms: float | None = None
        self._max_agent_latency_ms: float | None = None

        # Token accounting metrics (from LLMResponse metadata only)
        self._prompt_tokens_total: int = 0
        self._completion_tokens_total: int = 0
        self._total_tokens_used: int = 0

        # Provider resilience & execution metrics
        self._provider_metrics: dict[str, dict[str, Any]] = {}

        # Tool invocation metrics
        self._tool_invocations: int = 0
        self._tool_success: int = 0
        self._tool_denied: int = 0
        self._tool_failed: int = 0
        self._tool_timeout: int = 0
        self._total_tool_latency_ms: float = 0.0

        # Security boundary metrics
        self._authorization_denied: int = 0
        self._invalid_tenant_requests: int = 0
        self._invalid_identity_requests: int = 0
        self._blocked_context_requests: int = 0

        # Error breakdowns by category
        self._error_counts: dict[str, int] = {}

    # -- Safe Metric Recording Methods ---------------------------------------

    def record_generation(
        self,
        is_success: bool,
        latency_ms: float,
        error_category: str | None = None,
    ) -> None:
        """Record outcome and latency of a single-turn generation call."""
        try:
            with self._lock:
                self._generation_requests_total += 1
                self._total_generation_latency_ms += latency_ms

                if (
                    self._min_generation_latency_ms is None
                    or latency_ms < self._min_generation_latency_ms
                ):
                    self._min_generation_latency_ms = latency_ms
                if (
                    self._max_generation_latency_ms is None
                    or latency_ms > self._max_generation_latency_ms
                ):
                    self._max_generation_latency_ms = latency_ms

                if is_success:
                    self._generation_success_total += 1
                else:
                    self._generation_failure_total += 1
                    if error_category:
                        self._error_counts[error_category] = (
                            self._error_counts.get(error_category, 0) + 1
                        )
        except Exception as exc:
            logger.debug("Error recording generation metric: %s", exc)

    def record_tokens(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int | None = None,
    ) -> None:
        """Record real token usage extracted from LLMResponse metadata."""
        try:
            with self._lock:
                self._prompt_tokens_total += max(0, prompt_tokens)
                self._completion_tokens_total += max(0, completion_tokens)
                if total_tokens is not None:
                    self._total_tokens_used += max(0, total_tokens)
                else:
                    self._total_tokens_used += max(0, prompt_tokens) + max(0, completion_tokens)
        except Exception as exc:
            logger.debug("Error recording token metric: %s", exc)

    def record_provider_execution(
        self,
        provider_id: str,
        status: str,
        latency_ms: float,
        is_timeout: bool = False,
        is_fallback: bool = False,
        error_category: str | None = None,
    ) -> None:
        """Record resilience and execution metrics for a specific provider."""
        try:
            with self._lock:
                if provider_id not in self._provider_metrics:
                    self._provider_metrics[provider_id] = {
                        "requests": 0,
                        "successes": 0,
                        "failures": 0,
                        "timeouts": 0,
                        "fallbacks": 0,
                        "total_latency_ms": 0.0,
                        "avg_latency_ms": 0.0,
                    }
                pm = self._provider_metrics[provider_id]
                pm["requests"] += 1
                pm["total_latency_ms"] += latency_ms
                pm["avg_latency_ms"] = round(pm["total_latency_ms"] / pm["requests"], 2)

                if status == "SUCCESS":
                    pm["successes"] += 1
                else:
                    pm["failures"] += 1
                    if error_category:
                        self._error_counts[error_category] = (
                            self._error_counts.get(error_category, 0) + 1
                        )

                if is_timeout:
                    pm["timeouts"] += 1
                if is_fallback:
                    pm["fallbacks"] += 1
        except Exception as exc:
            logger.debug("Error recording provider metric: %s", exc)

    def record_agent_task(
        self,
        status: str,
        latency_ms: float,
        total_steps: int,
        error_category: str | None = None,
    ) -> None:
        """Record outcome, latency, and step count of an agent orchestration task."""
        try:
            with self._lock:
                self._agent_tasks_total += 1
                self._total_agent_steps += total_steps
                self._total_agent_latency_ms += latency_ms

                if (
                    self._min_agent_latency_ms is None
                    or latency_ms < self._min_agent_latency_ms
                ):
                    self._min_agent_latency_ms = latency_ms
                if (
                    self._max_agent_latency_ms is None
                    or latency_ms > self._max_agent_latency_ms
                ):
                    self._max_agent_latency_ms = latency_ms

                if status == "COMPLETED":
                    self._agent_completed_total += 1
                elif status == "PAUSED_FOR_APPROVAL":
                    self._paused_agent_tasks += 1
                elif status == "TIMED_OUT":
                    self._timed_out_agent_tasks += 1
                elif status == "LOOP_DETECTED":
                    self._loop_detected_agent_tasks += 1
                elif status == "STEP_LIMIT_EXCEEDED":
                    self._step_limit_exceeded_tasks += 1
                else:
                    self._agent_failed_total += 1

                if error_category:
                    self._error_counts[error_category] = (
                        self._error_counts.get(error_category, 0) + 1
                    )
        except Exception as exc:
            logger.debug("Error recording agent task metric: %s", exc)

    def record_tool_invocation(
        self,
        status: str,
        latency_ms: float,
        error_category: str | None = None,
        is_timeout: bool = False,
    ) -> None:
        """Record outcome and latency of a tool invocation."""
        try:
            with self._lock:
                self._tool_invocations += 1
                self._total_tool_latency_ms += latency_ms

                if status == "SUCCESS":
                    self._tool_success += 1
                elif status == "DENIED":
                    self._tool_denied += 1
                else:
                    self._tool_failed += 1

                if is_timeout:
                    self._tool_timeout += 1

                if error_category:
                    self._error_counts[error_category] = (
                        self._error_counts.get(error_category, 0) + 1
                    )
        except Exception as exc:
            logger.debug("Error recording tool invocation metric: %s", exc)

    def record_security_event(self, event_type: str) -> None:
        """Record a security boundary event."""
        try:
            with self._lock:
                if event_type == "authorization_denied":
                    self._authorization_denied += 1
                elif event_type == "invalid_tenant":
                    self._invalid_tenant_requests += 1
                elif event_type == "invalid_identity":
                    self._invalid_identity_requests += 1
                elif event_type == "blocked_context":
                    self._blocked_context_requests += 1
        except Exception as exc:
            logger.debug("Error recording security event metric: %s", exc)

    # -- IEngineDiagnostics Protocol Implementation --------------------------

    def health(self) -> dict[str, Any]:
        """Return operational health status and subsystem checks."""
        with self._lock:
            registered_providers_count = 0
            if self._provider_registry is not None:
                try:
                    registered_providers_count = len(self._provider_registry.list_providers())
                except Exception as exc:
                    logger.debug("Error listing providers for health: %s", exc)
                    registered_providers_count = 0

            registered_tools_count = 0
            if self._tool_registry is not None:
                try:
                    registered_tools_count = len(self._tool_registry.list_tools())
                except Exception as exc:
                    logger.debug("Error listing tools for health: %s", exc)
                    registered_tools_count = 0

            is_healthy = registered_providers_count > 0
            health_status = "HEALTHY" if is_healthy else "DEGRADED"

            return {
                "status": health_status,
                "engine": "ai",
                "timestamp": time.time(),
                "providers_registered": registered_providers_count,
                "tools_registered": registered_tools_count,
                "total_generations": self._generation_requests_total,
                "total_agent_tasks": self._agent_tasks_total,
            }

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance, throughput, tokens, and error metrics."""
        with self._lock:
            avg_gen_latency = (
                (self._total_generation_latency_ms / self._generation_requests_total)
                if self._generation_requests_total > 0
                else 0.0
            )
            avg_agent_latency = (
                (self._total_agent_latency_ms / self._agent_tasks_total)
                if self._agent_tasks_total > 0
                else 0.0
            )

            return {
                "generations": {
                    "total": self._generation_requests_total,
                    "successful": self._generation_success_total,
                    "failed": self._generation_failure_total,
                    "avg_latency_ms": round(avg_gen_latency, 2),
                    "min_latency_ms": round(self._min_generation_latency_ms or 0.0, 2),
                    "max_latency_ms": round(self._max_generation_latency_ms or 0.0, 2),
                },
                "tokens": {
                    "prompt_tokens_total": self._prompt_tokens_total,
                    "completion_tokens_total": self._completion_tokens_total,
                    "total_tokens_used": self._total_tokens_used,
                },
                "providers": copy.deepcopy(self._provider_metrics),
                "agent_tasks": {
                    "total": self._agent_tasks_total,
                    "completed": self._agent_completed_total,
                    "failed": self._agent_failed_total,
                    "paused_for_approval": self._paused_agent_tasks,
                    "timed_out": self._timed_out_agent_tasks,
                    "loop_detected": self._loop_detected_agent_tasks,
                    "step_limit_exceeded": self._step_limit_exceeded_tasks,
                    "total_steps": self._total_agent_steps,
                    "avg_latency_ms": round(avg_agent_latency, 2),
                },
                "tool_invocations": {
                    "total": self._tool_invocations,
                    "successful": self._tool_success,
                    "denied": self._tool_denied,
                    "failed": self._tool_failed,
                    "timeout": self._tool_timeout,
                },
                "security": {
                    "authorization_denied": self._authorization_denied,
                    "invalid_tenant_requests": self._invalid_tenant_requests,
                    "invalid_identity_requests": self._invalid_identity_requests,
                    "blocked_context_requests": self._blocked_context_requests,
                },
                "error_breakdown": dict(self._error_counts),
            }

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics and subsystem configurations."""
        with self._lock:
            providers_meta: list[dict[str, Any]] = []
            if self._provider_registry is not None:
                try:
                    for p in self._provider_registry.list_providers():
                        providers_meta.append({
                            "provider_id": p.provider_id,
                            "display_name": p.display_name,
                            "vendor": p.vendor,
                            "endpoint_type": p.endpoint_type,
                            "supported_models": list(p.supported_models),
                        })
                except Exception as exc:
                    logger.debug("Error assembling provider diagnostics: %s", exc)

            tools_meta: list[dict[str, Any]] = []
            if self._tool_registry is not None:
                try:
                    for t in self._tool_registry.list_tools():
                        tools_meta.append({
                            "name": t.name,
                            "canonical_capability": t.canonical_capability,
                            "is_mutation": t.is_mutation,
                            "timeout_seconds": t.timeout_seconds,
                        })
                except Exception as exc:
                    logger.debug("Error assembling tool diagnostics: %s", exc)

            return {
                "engine": "ai",
                "version": self.version(),
                "capabilities": self.capabilities(),
                "providers": providers_meta,
                "tools": tools_meta,
                "memory_configured": self._memory_manager is not None,
                "router_configured": self._model_router is not None,
            }

    def status(self) -> str:
        """Return current engine state name string."""
        return "READY"

    def version(self) -> str:
        """Return semantic version string of the engine."""
        return "1.0.0"

    def capabilities(self) -> list[str]:
        """Return list of capability strings registered by the engine."""
        return list(CANONICAL_CAPABILITIES)


__all__ = [
    "CANONICAL_CAPABILITIES",
    "AIDiagnostics",
]
