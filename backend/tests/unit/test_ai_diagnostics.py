"""Unit tests for KORTEX AI Orchestration Engine Diagnostics (Milestone 8).

Tests conform to the M8 specification:
- In-memory metrics tracking only
- Thread-safe / exception-safe recording
- Operational health status
- Diagnostics snapshots without leaking secrets
- IEngineDiagnostics protocol adherence
"""

from __future__ import annotations

import pytest

from kortex.engines.ai.diagnostics import CANONICAL_CAPABILITIES, AIDiagnostics
from kortex.engines.ai.interfaces import IEngineDiagnostics
from kortex.engines.ai.memory import AIMemoryManager, InMemoryConversationStore
from kortex.engines.ai.models import AIProviderMetadata
from kortex.engines.ai.registry import MetadataOnlyAIProvider, ProviderRegistry
from kortex.engines.ai.router import ModelRouter
from kortex.engines.ai.tools import ToolDefinition, ToolRegistry


def _make_diagnostics() -> AIDiagnostics:
    registry = ProviderRegistry()
    registry.register(
        MetadataOnlyAIProvider(
            AIProviderMetadata(
                provider_id="prov-1",
                display_name="Provider 1",
                vendor="TestVendor",
                endpoint_type="local_host",
                supported_models=["model-a"],
            )
        )
    )
    router = ModelRouter(registry=registry)
    memory = AIMemoryManager(store=InMemoryConversationStore())
    tools = ToolRegistry()
    tools.register_tool(
        ToolDefinition(
            name="test_tool",
            description="Test tool",
            canonical_capability="kortex.test.action",
            parameters_schema={"type": "object"},
        )
    )
    return AIDiagnostics(
        provider_registry=registry,
        model_router=router,
        memory_manager=memory,
        tool_registry=tools,
    )


def test_diagnostics_implements_interface() -> None:
    diag = _make_diagnostics()
    assert isinstance(diag, IEngineDiagnostics)


def test_diagnostics_version_and_status() -> None:
    diag = _make_diagnostics()
    assert diag.version() == "1.0.0"
    assert diag.status() == "READY"
    assert diag.capabilities() == CANONICAL_CAPABILITIES


def test_health_reporting_healthy() -> None:
    diag = _make_diagnostics()
    health = diag.health()
    assert health["status"] == "HEALTHY"
    assert health["engine"] == "ai"
    assert health["providers_registered"] == 1
    assert health["tools_registered"] == 1


def test_health_reporting_degraded_when_empty() -> None:
    empty_diag = AIDiagnostics()
    health = empty_diag.health()
    assert health["status"] == "DEGRADED"
    assert health["providers_registered"] == 0
    assert health["tools_registered"] == 0


def test_record_generation_metrics() -> None:
    diag = _make_diagnostics()

    # Record 2 successful generations and 1 failure
    diag.record_generation(is_success=True, latency_ms=100.0)
    diag.record_generation(is_success=True, latency_ms=200.0)
    diag.record_generation(is_success=False, latency_ms=50.0, error_category="RoutingError")

    metrics = diag.metrics()
    gens = metrics["generations"]
    assert gens["total"] == 3
    assert gens["successful"] == 2
    assert gens["failed"] == 1
    assert gens["min_latency_ms"] == 50.0
    assert gens["max_latency_ms"] == 200.0
    assert gens["avg_latency_ms"] == pytest.approx(116.67, 0.1)
    assert metrics["error_breakdown"]["RoutingError"] == 1


def test_record_agent_task_metrics() -> None:
    diag = _make_diagnostics()

    diag.record_agent_task(status="COMPLETED", latency_ms=500.0, total_steps=3)
    diag.record_agent_task(status="PAUSED_FOR_APPROVAL", latency_ms=200.0, total_steps=1)
    diag.record_agent_task(status="TIMED_OUT", latency_ms=1000.0, total_steps=5, error_category="Timeout")
    diag.record_agent_task(status="LOOP_DETECTED", latency_ms=300.0, total_steps=4)
    diag.record_agent_task(status="STEP_LIMIT_EXCEEDED", latency_ms=600.0, total_steps=10)
    diag.record_agent_task(status="FAILED", latency_ms=150.0, total_steps=2, error_category="ProviderError")

    metrics = diag.metrics()
    agent_m = metrics["agent_tasks"]
    assert agent_m["total"] == 6
    assert agent_m["completed"] == 1
    assert agent_m["paused_for_approval"] == 1
    assert agent_m["timed_out"] == 1
    assert agent_m["loop_detected"] == 1
    assert agent_m["step_limit_exceeded"] == 1
    assert agent_m["failed"] == 1
    assert agent_m["total_steps"] == 25
    assert metrics["error_breakdown"]["Timeout"] == 1
    assert metrics["error_breakdown"]["ProviderError"] == 1


def test_record_tool_invocation_metrics() -> None:
    diag = _make_diagnostics()

    diag.record_tool_invocation(status="SUCCESS", latency_ms=25.0)
    diag.record_tool_invocation(status="DENIED", latency_ms=10.0)
    diag.record_tool_invocation(status="EXECUTION_ERROR", latency_ms=45.0, error_category="ToolError")

    metrics = diag.metrics()
    tool_m = metrics["tool_invocations"]
    assert tool_m["total"] == 3
    assert tool_m["successful"] == 1
    assert tool_m["denied"] == 1
    assert tool_m["failed"] == 1
    assert metrics["error_breakdown"]["ToolError"] == 1


def test_diagnostics_snapshot() -> None:
    diag = _make_diagnostics()
    snapshot = diag.diagnostics()

    assert snapshot["engine"] == "ai"
    assert snapshot["version"] == "1.0.0"
    assert "kortex.ai.response.generate" in snapshot["capabilities"]
    assert len(snapshot["providers"]) == 1
    assert snapshot["providers"][0]["provider_id"] == "prov-1"
    assert len(snapshot["tools"]) == 1
    assert snapshot["tools"][0]["name"] == "test_tool"
    assert snapshot["memory_configured"] is True
    assert snapshot["router_configured"] is True


def test_recording_methods_are_safe_on_unexpected_types() -> None:
    diag = _make_diagnostics()
    # Ensure invalid inputs do not raise exceptions
    diag.record_generation(is_success=True, latency_ms=float("nan"))
    diag.record_agent_task(status="INVALID", latency_ms=0.0, total_steps=0)
    diag.record_tool_invocation(status="UNKNOWN", latency_ms=0.0)
