"""Immutable system event payload definitions for the KORTEX OS AI Orchestration Engine.

Governed by Milestone 9.5 architecture specification:
docs/architecture/ai_engine_m9_production_runtime_spec.md

Provides typed, frozen event payloads for all AI lifecycle events:
- Generation: started, completed, failed
- Provider: failure, timeout, fallback
- Storage: write_failed
- Agent: completed, failed, loop_detected
- Security: denied, validation_failed
- Tool: invoked, failed, denied
"""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AIBaseEvent(BaseModel):
    """Base class for all immutable AI Orchestration Engine system event payloads."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"evt-{uuid.uuid4().hex}")
    event_type: str
    timestamp: str = Field(default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat())


# ---------------------------------------------------------------------------
# Generation Lifecycle Events
# ---------------------------------------------------------------------------


class AIGenerationStartedEvent(AIBaseEvent):
    """Dispatched when an AI generation request is initiated."""

    event_type: Literal["ai.generation.started"] = "ai.generation.started"
    request_id: str
    tenant_id: str
    conversation_id: str
    user_id: str | None = None


class AIGenerationCompletedEvent(AIBaseEvent):
    """Dispatched when an AI generation request successfully completes."""

    event_type: Literal["ai.generation.completed"] = "ai.generation.completed"
    request_id: str
    tenant_id: str
    conversation_id: str
    execution_time_ms: float
    user_id: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)


class AIGenerationFailedEvent(AIBaseEvent):
    """Dispatched when an AI generation request fails."""

    event_type: Literal["ai.generation.failed"] = "ai.generation.failed"
    request_id: str
    tenant_id: str
    conversation_id: str
    execution_time_ms: float
    error_category: str
    user_id: str | None = None


# ---------------------------------------------------------------------------
# Provider Resilience Events
# ---------------------------------------------------------------------------


class AIProviderFailureEvent(AIBaseEvent):
    """Dispatched when a provider request fails during execution."""

    event_type: Literal["ai.provider.failure"] = "ai.provider.failure"
    provider_id: str
    error_category: str
    is_transient: bool
    model_id: str | None = None
    tenant_id: str | None = None


class AIProviderTimeoutEvent(AIBaseEvent):
    """Dispatched when a provider execution exceeds its timeout deadline."""

    event_type: Literal["ai.provider.timeout"] = "ai.provider.timeout"
    provider_id: str
    timeout_seconds: float
    model_id: str | None = None
    tenant_id: str | None = None


class AIProviderFallbackEvent(AIBaseEvent):
    """Dispatched when provider execution falls back to a secondary provider."""

    event_type: Literal["ai.provider.fallback"] = "ai.provider.fallback"
    primary_provider_id: str
    fallback_provider_id: str
    reason: str
    tenant_id: str | None = None


# ---------------------------------------------------------------------------
# Storage Reliability Events
# ---------------------------------------------------------------------------


class AIStorageWriteFailedEvent(AIBaseEvent):
    """Dispatched when conversation-history persistence fails after a
    generation has already completed successfully.

    Signals a system alert per the M9 architecture spec's Systematic Failure
    Recovery Matrix ("Storage Engine Offline / DB Lock... return generation
    with degraded flag and emit system alert"). Never carries prompt,
    response, or history content — only identifiers and an error category.
    """

    event_type: Literal["ai.storage.write_failed"] = "ai.storage.write_failed"
    request_id: str
    tenant_id: str
    conversation_id: str
    error_category: str
    user_id: str | None = None


# ---------------------------------------------------------------------------
# Agent Orchestration Events
# ---------------------------------------------------------------------------


class AgentTaskCompletedEvent(AIBaseEvent):
    """Dispatched when an agent orchestration task completes."""

    event_type: Literal["ai.agent.completed"] = "ai.agent.completed"
    task_id: str
    tenant_id: str
    status: str
    total_steps: int = 0
    execution_time_ms: float = 0.0
    user_id: str | None = None


class AgentTaskFailedEvent(AIBaseEvent):
    """Dispatched when an agent orchestration task fails or aborts."""

    event_type: Literal["ai.agent.failed"] = "ai.agent.failed"
    task_id: str
    tenant_id: str
    error_category: str
    total_steps: int = 0
    execution_time_ms: float = 0.0
    user_id: str | None = None


class AgentLoopDetectedEvent(AIBaseEvent):
    """Dispatched when an infinite reasoning loop is detected in agent execution."""

    event_type: Literal["ai.agent.loop_detected"] = "ai.agent.loop_detected"
    task_id: str
    tenant_id: str
    tool_name: str
    step_count: int
    user_id: str | None = None


# ---------------------------------------------------------------------------
# Security & Enforcement Events
# ---------------------------------------------------------------------------


class AISecurityDeniedEvent(AIBaseEvent):
    """Dispatched when a capability or action is denied by security boundary."""

    event_type: Literal["ai.security.denied"] = "ai.security.denied"
    tenant_id: str
    action: str
    reason: str
    user_id: str | None = None


class AISecurityValidationFailedEvent(AIBaseEvent):
    """Dispatched when tenant, user, or payload boundary validation fails."""

    event_type: Literal["ai.security.validation_failed"] = "ai.security.validation_failed"
    tenant_id: str
    validation_type: str
    reason: str
    user_id: str | None = None


# ---------------------------------------------------------------------------
# Tool Invocation Events
# ---------------------------------------------------------------------------


class AIToolInvokedEvent(AIBaseEvent):
    """Dispatched when an AI-requested tool invocation occurs."""

    event_type: Literal["ai.tool.invoked"] = "ai.tool.invoked"
    request_id: str
    tenant_id: str
    tool_name: str


class AIToolCompletedEvent(AIBaseEvent):
    """Dispatched when an AI-requested tool invocation completes successfully
    (M7.6-W3). Mirrors `AIGenerationCompletedEvent`'s established convention
    of carrying `execution_time_ms` on a "completed" event -- previously no
    event existed for a successful tool completion at all: `AIToolInvokedEvent`
    fires before execution (no latency to report yet, correctly), and only
    the failure/denial paths (`AIToolFailedEvent`/`AIToolDeniedEvent`)
    published a domain event on completion. A successful invocation's
    already-computed `ToolResult.execution_time_ms` was recorded only into
    `AIDiagnostics` directly, bypassing `AITelemetryEmitter` and its
    domain-event/exporter-counter behavior entirely -- the one telemetry
    consumers most need to see (the common case) was invisible to them."""

    event_type: Literal["ai.tool.completed"] = "ai.tool.completed"
    request_id: str
    tenant_id: str
    tool_name: str
    execution_time_ms: float


class AIToolFailedEvent(AIBaseEvent):
    """Dispatched when a tool execution fails with an error."""

    event_type: Literal["ai.tool.failed"] = "ai.tool.failed"
    request_id: str
    tenant_id: str
    tool_name: str
    error_category: str


class AIToolDeniedEvent(AIBaseEvent):
    """Dispatched when a tool invocation is rejected by authorization policy."""

    event_type: Literal["ai.tool.denied"] = "ai.tool.denied"
    request_id: str
    tenant_id: str
    tool_name: str
    reason: str


__all__ = [
    "AIBaseEvent",
    "AIGenerationCompletedEvent",
    "AIGenerationFailedEvent",
    "AIGenerationStartedEvent",
    "AIProviderFailureEvent",
    "AIProviderFallbackEvent",
    "AIProviderTimeoutEvent",
    "AISecurityDeniedEvent",
    "AISecurityValidationFailedEvent",
    "AIStorageWriteFailedEvent",
    "AIToolCompletedEvent",
    "AIToolDeniedEvent",
    "AIToolFailedEvent",
    "AIToolInvokedEvent",
    "AgentLoopDetectedEvent",
    "AgentTaskCompletedEvent",
    "AgentTaskFailedEvent",
]
