"""Tier 2 Event Engine Telemetry Emitter for KORTEX AI Orchestration Engine.

Governed by Milestone 9.5 architecture specification:
docs/architecture/ai_engine_m9_production_runtime_spec.md

Converts AI lifecycle operations into sanitized, non-blocking Event Engine events:
- Generation: ai.generation.started, ai.generation.completed, ai.generation.failed
- Provider: ai.provider.failure, ai.provider.timeout, ai.provider.fallback
- Agent: ai.agent.completed, ai.agent.failed, ai.agent.loop_detected
- Security: ai.security.denied, ai.security.validation_failed
- Tool: ai.tool.invoked, ai.tool.failed, ai.tool.denied

Guarantees:
- Best-effort, non-blocking, exception isolated (never raises).
- Multi-tenant safe (tenant_id, user_id, request_id metadata preserved).
- Secret protection: strips credentials, bearer tokens, passwords.
- Privacy protection: avoids logging raw prompt text or unscrubbed tool payload contents.
"""

from __future__ import annotations

import logging
from typing import Any

from kortex.engines.ai.diagnostics import AIDiagnostics
from kortex.engines.ai.events import (
    AgentLoopDetectedEvent,
    AgentTaskCompletedEvent,
    AgentTaskFailedEvent,
    AIBaseEvent,
    AIGenerationCompletedEvent,
    AIGenerationFailedEvent,
    AIGenerationStartedEvent,
    AIProviderFailureEvent,
    AIProviderFallbackEvent,
    AIProviderTimeoutEvent,
    AISecurityDeniedEvent,
    AISecurityValidationFailedEvent,
    AIToolDeniedEvent,
    AIToolFailedEvent,
    AIToolInvokedEvent,
)
from kortex.engines.ai.interfaces import IKernelBridge
from kortex.engines.ai.telemetry_ports import ITelemetryExporter

logger = logging.getLogger("kortex.engines.ai.telemetry")

_FORBIDDEN_SECRET_KEYS: frozenset[str] = frozenset({
    "api_key",
    "apikey",
    "token",
    "bearer",
    "password",
    "secret",
    "credential",
    "authorization",
    "auth_header",
    "private_key",
    "master_key",
})


def sanitize_telemetry_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact secrets and credentials from telemetry dictionaries."""
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        lower_key = str(key).lower()
        if isinstance(value, dict):
            sanitized[key] = sanitize_telemetry_payload(value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_telemetry_payload(item) if isinstance(item, dict) else item
                for item in value
            ]
        elif any(secret_term in lower_key for secret_term in _FORBIDDEN_SECRET_KEYS):
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = value
    return sanitized


class AITelemetryEmitter:
    """Production telemetry and event emitter for KORTEX AI Orchestration Engine."""

    def __init__(
        self,
        kernel_bridge: IKernelBridge | None = None,
        diagnostics: AIDiagnostics | None = None,
        exporter: ITelemetryExporter | None = None,
    ) -> None:
        self._kernel_bridge = kernel_bridge
        self._diagnostics = diagnostics
        self._exporter = exporter

    @property
    def diagnostics(self) -> AIDiagnostics | None:
        """Active in-memory diagnostics recorder."""
        return self._diagnostics

    @property
    def exporter(self) -> ITelemetryExporter | None:
        """Active external metrics exporter."""
        return self._exporter

    async def _safe_publish(self, event: AIBaseEvent) -> None:
        """Publish an event via the Kernel Event Engine safely without throwing."""
        if self._kernel_bridge is None:
            return
        try:
            raw_payload = event.model_dump()
            sanitized_payload = sanitize_telemetry_payload(raw_payload)
            await self._kernel_bridge.publish_event(
                topic=event.event_type,
                payload=sanitized_payload,
                sender="ai",
            )
        except Exception as exc:
            logger.warning("Failed to publish telemetry event %s: %s", event.event_type, exc)

    # -- Generation Telemetry ------------------------------------------------

    async def emit_generation_started(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        request_id: str,
    ) -> None:
        """Emit generation started lifecycle event."""
        if self._exporter:
            self._exporter.record_counter("ai.generation.requests", 1, {"tenant_id": tenant_id})

        event = AIGenerationStartedEvent(
            request_id=request_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        await self._safe_publish(event)

    async def emit_generation_completed(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        request_id: str,
        latency_ms: float,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        """Emit generation completed event and record tokens/metrics."""
        usage = token_usage or {}
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)

        if self._diagnostics:
            self._diagnostics.record_generation(is_success=True, latency_ms=latency_ms)
            self._diagnostics.record_tokens(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )

        if self._exporter:
            self._exporter.record_counter("ai.generation.success", 1, {"tenant_id": tenant_id})
            self._exporter.record_histogram("ai.generation.latency_ms", latency_ms, {"tenant_id": tenant_id})
            if total_tokens > 0:
                self._exporter.record_counter("ai.tokens.total", total_tokens, {"tenant_id": tenant_id})

        event = AIGenerationCompletedEvent(
            request_id=request_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            execution_time_ms=latency_ms,
            user_id=user_id,
            token_usage=usage,
        )
        await self._safe_publish(event)

    async def emit_generation_failed(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        request_id: str,
        latency_ms: float,
        error_category: str,
    ) -> None:
        """Emit generation failed event."""
        if self._diagnostics:
            self._diagnostics.record_generation(
                is_success=False,
                latency_ms=latency_ms,
                error_category=error_category,
            )

        if self._exporter:
            self._exporter.record_counter(
                "ai.generation.failure", 1, {"tenant_id": tenant_id, "error": error_category}
            )

        event = AIGenerationFailedEvent(
            request_id=request_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            execution_time_ms=latency_ms,
            error_category=error_category,
            user_id=user_id,
        )
        await self._safe_publish(event)

    # -- Provider Telemetry --------------------------------------------------

    async def emit_provider_failure(
        self,
        provider_id: str,
        error_category: str,
        is_transient: bool,
        model_id: str | None = None,
        tenant_id: str | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Emit provider failure event."""
        if self._diagnostics:
            self._diagnostics.record_provider_execution(
                provider_id=provider_id,
                status="FAILURE",
                latency_ms=latency_ms,
                error_category=error_category,
            )

        if self._exporter:
            self._exporter.record_counter(
                "ai.provider.failure", 1, {"provider_id": provider_id, "error": error_category}
            )

        event = AIProviderFailureEvent(
            provider_id=provider_id,
            error_category=error_category,
            is_transient=is_transient,
            model_id=model_id,
            tenant_id=tenant_id,
        )
        await self._safe_publish(event)

    async def emit_provider_timeout(
        self,
        provider_id: str,
        timeout_seconds: float,
        model_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Emit provider timeout event."""
        if self._diagnostics:
            self._diagnostics.record_provider_execution(
                provider_id=provider_id,
                status="TIMEOUT",
                latency_ms=timeout_seconds * 1000.0,
                is_timeout=True,
                error_category="AIProviderTimeoutError",
            )

        if self._exporter:
            self._exporter.record_counter("ai.provider.timeout", 1, {"provider_id": provider_id})

        event = AIProviderTimeoutEvent(
            provider_id=provider_id,
            timeout_seconds=timeout_seconds,
            model_id=model_id,
            tenant_id=tenant_id,
        )
        await self._safe_publish(event)

    async def emit_provider_fallback(
        self,
        primary_provider_id: str,
        fallback_provider_id: str,
        reason: str,
        tenant_id: str | None = None,
    ) -> None:
        """Emit provider fallback event."""
        if self._diagnostics:
            self._diagnostics.record_provider_execution(
                provider_id=primary_provider_id,
                status="FALLBACK",
                latency_ms=0.0,
                is_fallback=True,
            )

        if self._exporter:
            self._exporter.record_counter(
                "ai.provider.fallback",
                1,
                {"from_provider": primary_provider_id, "to_provider": fallback_provider_id},
            )

        event = AIProviderFallbackEvent(
            primary_provider_id=primary_provider_id,
            fallback_provider_id=fallback_provider_id,
            reason=reason,
            tenant_id=tenant_id,
        )
        await self._safe_publish(event)

    # -- Agent Telemetry -----------------------------------------------------

    async def emit_agent_completed(
        self,
        task_id: str,
        tenant_id: str,
        user_id: str,
        total_steps: int,
        latency_ms: float,
        status: str = "COMPLETED",
    ) -> None:
        """Emit agent task completed event."""
        if self._diagnostics:
            self._diagnostics.record_agent_task(
                status=status,
                latency_ms=latency_ms,
                total_steps=total_steps,
            )

        if self._exporter:
            self._exporter.record_counter("ai.agent.completed", 1, {"tenant_id": tenant_id})
            self._exporter.record_histogram("ai.agent.latency_ms", latency_ms, {"tenant_id": tenant_id})

        event = AgentTaskCompletedEvent(
            task_id=task_id,
            tenant_id=tenant_id,
            status=status,
            total_steps=total_steps,
            execution_time_ms=latency_ms,
            user_id=user_id,
        )
        await self._safe_publish(event)

    async def emit_agent_failed(
        self,
        task_id: str,
        tenant_id: str,
        user_id: str,
        total_steps: int,
        latency_ms: float,
        error_category: str,
    ) -> None:
        """Emit agent task failed event."""
        if self._diagnostics:
            self._diagnostics.record_agent_task(
                status="FAILED",
                latency_ms=latency_ms,
                total_steps=total_steps,
                error_category=error_category,
            )

        if self._exporter:
            self._exporter.record_counter(
                "ai.agent.failed", 1, {"tenant_id": tenant_id, "error": error_category}
            )

        event = AgentTaskFailedEvent(
            task_id=task_id,
            tenant_id=tenant_id,
            error_category=error_category,
            total_steps=total_steps,
            execution_time_ms=latency_ms,
            user_id=user_id,
        )
        await self._safe_publish(event)

    async def emit_agent_loop_detected(
        self,
        task_id: str,
        tenant_id: str,
        user_id: str,
        tool_name: str,
        step_count: int,
    ) -> None:
        """Emit agent infinite loop detected event."""
        if self._diagnostics:
            self._diagnostics.record_agent_task(
                status="LOOP_DETECTED",
                latency_ms=0.0,
                total_steps=step_count,
                error_category="AgentLoopDetectedError",
            )

        if self._exporter:
            self._exporter.record_counter(
                "ai.agent.loop_detected", 1, {"tenant_id": tenant_id, "tool": tool_name}
            )

        event = AgentLoopDetectedEvent(
            task_id=task_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            step_count=step_count,
            user_id=user_id,
        )
        await self._safe_publish(event)

    # -- Security Telemetry --------------------------------------------------

    async def emit_security_denied(
        self,
        tenant_id: str,
        user_id: str,
        action: str,
        reason: str,
    ) -> None:
        """Emit security authorization denied event."""
        if self._diagnostics:
            self._diagnostics.record_security_event("authorization_denied")

        if self._exporter:
            self._exporter.record_counter(
                "ai.security.denied", 1, {"tenant_id": tenant_id, "action": action}
            )

        event = AISecurityDeniedEvent(
            tenant_id=tenant_id,
            action=action,
            reason=reason,
            user_id=user_id,
        )
        await self._safe_publish(event)

    async def emit_security_validation_failed(
        self,
        tenant_id: str,
        validation_type: str,
        reason: str,
        user_id: str | None = None,
    ) -> None:
        """Emit security validation failed event."""
        if self._diagnostics:
            if validation_type == "tenant_id":
                self._diagnostics.record_security_event("invalid_tenant")
            elif validation_type == "user_id":
                self._diagnostics.record_security_event("invalid_identity")
            elif validation_type == "context":
                self._diagnostics.record_security_event("blocked_context")

        if self._exporter:
            self._exporter.record_counter(
                "ai.security.validation_failed",
                1,
                {"tenant_id": tenant_id, "validation_type": validation_type},
            )

        event = AISecurityValidationFailedEvent(
            tenant_id=tenant_id,
            validation_type=validation_type,
            reason=reason,
            user_id=user_id,
        )
        await self._safe_publish(event)

    # -- Tool Telemetry ------------------------------------------------------

    async def emit_tool_invoked(
        self,
        tenant_id: str,
        tool_name: str,
        request_id: str,
    ) -> None:
        """Emit tool invoked event."""
        if self._exporter:
            self._exporter.record_counter("ai.tool.invocations", 1, {"tool_name": tool_name})

        event = AIToolInvokedEvent(
            request_id=request_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
        )
        await self._safe_publish(event)

    async def emit_tool_failed(
        self,
        tenant_id: str,
        tool_name: str,
        request_id: str,
        error_category: str,
        latency_ms: float = 0.0,
        is_timeout: bool = False,
    ) -> None:
        """Emit tool failed event."""
        if self._diagnostics:
            self._diagnostics.record_tool_invocation(
                status="FAILED",
                latency_ms=latency_ms,
                error_category=error_category,
                is_timeout=is_timeout,
            )

        if self._exporter:
            self._exporter.record_counter(
                "ai.tool.failed", 1, {"tool_name": tool_name, "error": error_category}
            )

        event = AIToolFailedEvent(
            request_id=request_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            error_category=error_category,
        )
        await self._safe_publish(event)

    async def emit_tool_denied(
        self,
        tenant_id: str,
        tool_name: str,
        request_id: str,
        reason: str,
        latency_ms: float = 0.0,
    ) -> None:
        """Emit tool authorization denied event."""
        if self._diagnostics:
            self._diagnostics.record_tool_invocation(
                status="DENIED",
                latency_ms=latency_ms,
                error_category="ToolAuthorizationError",
            )

        if self._exporter:
            self._exporter.record_counter("ai.tool.denied", 1, {"tool_name": tool_name})

        event = AIToolDeniedEvent(
            request_id=request_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            reason=reason,
        )
        await self._safe_publish(event)


__all__ = [
    "AITelemetryEmitter",
    "sanitize_telemetry_payload",
]
