"""In-memory diagnostic metrics and health reporter for KORTEX AI Orchestration Engine.

Conforms strictly to the IEngineDiagnostics protocol for operational health,
state metrics, technical diagnostics snapshots, and capability reporting.

Invariants:
- In-memory metrics only. Zero database connections, zero SQL tables.
- Recording methods are safe, atomic, and never raise exceptions.
- Diagnostics snapshots never expose API keys, bearer tokens, or secret handles.
"""

from __future__ import annotations

import logging
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
]


class AIDiagnostics(IEngineDiagnostics):
    """Standardized in-memory diagnostics provider for the AI Orchestration Engine."""

    def __init__(
        self,
        provider_registry: ProviderRegistry | None = None,
        model_router: ModelRouter | None = None,
        memory_manager: AIMemoryManager | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._provider_registry = provider_registry
        self._model_router = model_router
        self._memory_manager = memory_manager
        self._tool_registry = tool_registry

        # Generation metrics
        self._total_generations: int = 0
        self._successful_generations: int = 0
        self._failed_generations: int = 0
        self._total_generation_latency_ms: float = 0.0
        self._min_generation_latency_ms: float | None = None
        self._max_generation_latency_ms: float | None = None

        # Agent task metrics
        self._total_agent_tasks: int = 0
        self._completed_agent_tasks: int = 0
        self._failed_agent_tasks: int = 0
        self._paused_agent_tasks: int = 0
        self._timed_out_agent_tasks: int = 0
        self._loop_detected_agent_tasks: int = 0
        self._step_limit_exceeded_tasks: int = 0
        self._total_agent_steps: int = 0
        self._total_agent_latency_ms: float = 0.0
        self._min_agent_latency_ms: float | None = None
        self._max_agent_latency_ms: float | None = None

        # Tool invocation metrics
        self._total_tool_invocations: int = 0
        self._successful_tool_invocations: int = 0
        self._denied_tool_invocations: int = 0
        self._failed_tool_invocations: int = 0
        self._total_tool_latency_ms: float = 0.0

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
            self._total_generations += 1
            self._total_generation_latency_ms += latency_ms

            if self._min_generation_latency_ms is None or latency_ms < self._min_generation_latency_ms:
                self._min_generation_latency_ms = latency_ms
            if self._max_generation_latency_ms is None or latency_ms > self._max_generation_latency_ms:
                self._max_generation_latency_ms = latency_ms

            if is_success:
                self._successful_generations += 1
            else:
                self._failed_generations += 1
                if error_category:
                    self._error_counts[error_category] = self._error_counts.get(error_category, 0) + 1
        except Exception as exc:
            logger.debug("Error recording generation metric: %s", exc)

    def record_agent_task(
        self,
        status: str,
        latency_ms: float,
        total_steps: int,
        error_category: str | None = None,
    ) -> None:
        """Record outcome, latency, and step count of an agent orchestration task."""
        try:
            self._total_agent_tasks += 1
            self._total_agent_steps += total_steps
            self._total_agent_latency_ms += latency_ms

            if self._min_agent_latency_ms is None or latency_ms < self._min_agent_latency_ms:
                self._min_agent_latency_ms = latency_ms
            if self._max_agent_latency_ms is None or latency_ms > self._max_agent_latency_ms:
                self._max_agent_latency_ms = latency_ms

            if status == "COMPLETED":
                self._completed_agent_tasks += 1
            elif status == "PAUSED_FOR_APPROVAL":
                self._paused_agent_tasks += 1
            elif status == "TIMED_OUT":
                self._timed_out_agent_tasks += 1
            elif status == "LOOP_DETECTED":
                self._loop_detected_agent_tasks += 1
            elif status == "STEP_LIMIT_EXCEEDED":
                self._step_limit_exceeded_tasks += 1
            else:
                self._failed_agent_tasks += 1

            if error_category:
                self._error_counts[error_category] = self._error_counts.get(error_category, 0) + 1
        except Exception as exc:
            logger.debug("Error recording agent task metric: %s", exc)

    def record_tool_invocation(
        self,
        status: str,
        latency_ms: float,
        error_category: str | None = None,
    ) -> None:
        """Record outcome and latency of a tool invocation."""
        try:
            self._total_tool_invocations += 1
            self._total_tool_latency_ms += latency_ms

            if status == "SUCCESS":
                self._successful_tool_invocations += 1
            elif status == "DENIED":
                self._denied_tool_invocations += 1
            else:
                self._failed_tool_invocations += 1

            if error_category:
                self._error_counts[error_category] = self._error_counts.get(error_category, 0) + 1
        except Exception as exc:
            logger.debug("Error recording tool invocation metric: %s", exc)

    # -- IEngineDiagnostics Protocol Implementation --------------------------

    def health(self) -> dict[str, Any]:
        """Return operational health status and subsystem checks."""
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
            "total_generations": self._total_generations,
            "total_agent_tasks": self._total_agent_tasks,
        }

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance, throughput, and error metrics."""
        avg_gen_latency = (
            (self._total_generation_latency_ms / self._total_generations)
            if self._total_generations > 0
            else 0.0
        )
        avg_agent_latency = (
            (self._total_agent_latency_ms / self._total_agent_tasks)
            if self._total_agent_tasks > 0
            else 0.0
        )

        return {
            "generations": {
                "total": self._total_generations,
                "successful": self._successful_generations,
                "failed": self._failed_generations,
                "avg_latency_ms": round(avg_gen_latency, 2),
                "min_latency_ms": round(self._min_generation_latency_ms or 0.0, 2),
                "max_latency_ms": round(self._max_generation_latency_ms or 0.0, 2),
            },
            "agent_tasks": {
                "total": self._total_agent_tasks,
                "completed": self._completed_agent_tasks,
                "failed": self._failed_agent_tasks,
                "paused_for_approval": self._paused_agent_tasks,
                "timed_out": self._timed_out_agent_tasks,
                "loop_detected": self._loop_detected_agent_tasks,
                "step_limit_exceeded": self._step_limit_exceeded_tasks,
                "total_steps": self._total_agent_steps,
                "avg_latency_ms": round(avg_agent_latency, 2),
            },
            "tool_invocations": {
                "total": self._total_tool_invocations,
                "successful": self._successful_tool_invocations,
                "denied": self._denied_tool_invocations,
                "failed": self._failed_tool_invocations,
            },
            "error_breakdown": dict(self._error_counts),
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics and subsystem configurations."""
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
