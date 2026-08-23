"""Unit tests for Extended Tier 1 In-Memory Diagnostics (Milestone 9.5).

Tests adhere strictly to the ratified M9.5 specification:
- Request metrics (generation and agent task counts)
- Latency tracking (min, max, average calculations)
- Token accounting (prompt_tokens, completion_tokens, total_tokens_used)
- Provider resilience & execution metrics
- Tool invocation metrics (success, denied, failed, timeout)
- Security boundary metrics (auth denied, invalid tenant, invalid identity, blocked context)
- Thread safety and snapshot isolation
"""

from __future__ import annotations

import concurrent.futures

from kortex.engines.ai.diagnostics import AIDiagnostics


def test_generation_request_counters_and_latency() -> None:
    """Verify request metrics and latency calculations."""
    diag = AIDiagnostics()

    diag.record_generation(is_success=True, latency_ms=100.0)
    diag.record_generation(is_success=True, latency_ms=200.0)
    diag.record_generation(is_success=False, latency_ms=300.0, error_category="Timeout")

    metrics = diag.metrics()
    gens = metrics["generations"]
    assert gens["total"] == 3
    assert gens["successful"] == 2
    assert gens["failed"] == 1
    assert gens["min_latency_ms"] == 100.0
    assert gens["max_latency_ms"] == 300.0
    assert gens["avg_latency_ms"] == 200.0
    assert metrics["error_breakdown"]["Timeout"] == 1


def test_token_accounting_from_metadata_only() -> None:
    """Verify token usage tracking without estimating tokens."""
    diag = AIDiagnostics()

    diag.record_tokens(prompt_tokens=50, completion_tokens=150, total_tokens=200)
    diag.record_tokens(prompt_tokens=30, completion_tokens=70)

    metrics = diag.metrics()
    tokens = metrics["tokens"]
    assert tokens["prompt_tokens_total"] == 80
    assert tokens["completion_tokens_total"] == 220
    assert tokens["total_tokens_used"] == 300


def test_provider_resilience_and_execution_metrics() -> None:
    """Verify per-provider execution, timeout, failure, and fallback metrics."""
    diag = AIDiagnostics()

    diag.record_provider_execution(
        provider_id="ollama-local",
        status="SUCCESS",
        latency_ms=150.0,
    )
    diag.record_provider_execution(
        provider_id="ollama-local",
        status="TIMEOUT",
        latency_ms=60000.0,
        is_timeout=True,
    )
    diag.record_provider_execution(
        provider_id="ollama-local",
        status="FALLBACK",
        latency_ms=0.0,
        is_fallback=True,
    )

    metrics = diag.metrics()
    prov = metrics["providers"]["ollama-local"]
    assert prov["requests"] == 3
    assert prov["successes"] == 1
    assert prov["failures"] == 2
    assert prov["timeouts"] == 1
    assert prov["fallbacks"] == 1
    assert prov["total_latency_ms"] == 60150.0


def test_tool_invocation_metrics() -> None:
    """Verify tool invocation success, failure, denied, and timeout counters."""
    diag = AIDiagnostics()

    diag.record_tool_invocation(status="SUCCESS", latency_ms=10.0)
    diag.record_tool_invocation(status="DENIED", latency_ms=5.0)
    diag.record_tool_invocation(
        status="FAILED", latency_ms=20.0, error_category="ExecutionError"
    )
    diag.record_tool_invocation(status="FAILED", latency_ms=5000.0, is_timeout=True)

    metrics = diag.metrics()
    tools = metrics["tool_invocations"]
    assert tools["total"] == 4
    assert tools["successful"] == 1
    assert tools["denied"] == 1
    assert tools["failed"] == 2
    assert tools["timeout"] == 1
    assert metrics["error_breakdown"]["ExecutionError"] == 1


def test_security_boundary_metrics() -> None:
    """Verify security event tracking (denied, invalid tenant, identity, context)."""
    diag = AIDiagnostics()

    diag.record_security_event("authorization_denied")
    diag.record_security_event("invalid_tenant")
    diag.record_security_event("invalid_identity")
    diag.record_security_event("blocked_context")
    diag.record_security_event("authorization_denied")

    metrics = diag.metrics()
    sec = metrics["security"]
    assert sec["authorization_denied"] == 2
    assert sec["invalid_tenant_requests"] == 1
    assert sec["invalid_identity_requests"] == 1
    assert sec["blocked_context_requests"] == 1


def test_agent_task_metrics() -> None:
    """Verify agent task completion, loop detection, and failure tracking."""
    diag = AIDiagnostics()

    diag.record_agent_task(status="COMPLETED", latency_ms=500.0, total_steps=5)
    diag.record_agent_task(status="LOOP_DETECTED", latency_ms=200.0, total_steps=3)
    diag.record_agent_task(
        status="FAILED",
        latency_ms=100.0,
        total_steps=1,
        error_category="OrchestrationError",
    )

    metrics = diag.metrics()
    agent = metrics["agent_tasks"]
    assert agent["total"] == 3
    assert agent["completed"] == 1
    assert agent["loop_detected"] == 1
    assert agent["failed"] == 2
    assert agent["total_steps"] == 9
    assert metrics["error_breakdown"]["OrchestrationError"] == 1


def test_diagnostics_snapshot_isolation() -> None:
    """Verify metrics dictionary is a deep copy and modifying it does not mutate internal state."""
    diag = AIDiagnostics()
    diag.record_provider_execution(
        provider_id="ollama-local", status="SUCCESS", latency_ms=100.0
    )

    metrics_snap1 = diag.metrics()
    metrics_snap1["providers"]["ollama-local"]["requests"] = 999

    metrics_snap2 = diag.metrics()
    assert metrics_snap2["providers"]["ollama-local"]["requests"] == 1


def test_diagnostics_thread_safety() -> None:
    """Verify concurrent metric recordings from multiple threads execute safely without race conditions."""
    diag = AIDiagnostics()
    num_threads = 10
    iterations = 100

    def worker() -> None:
        for _ in range(iterations):
            diag.record_generation(is_success=True, latency_ms=50.0)
            diag.record_tokens(prompt_tokens=10, completion_tokens=20)
            diag.record_tool_invocation(status="SUCCESS", latency_ms=5.0)
            diag.record_security_event("authorization_denied")

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker) for _ in range(num_threads)]
        concurrent.futures.wait(futures)

    metrics = diag.metrics()
    assert metrics["generations"]["total"] == num_threads * iterations
    assert metrics["generations"]["successful"] == num_threads * iterations
    assert metrics["tokens"]["prompt_tokens_total"] == num_threads * iterations * 10
    assert (
        metrics["tokens"]["completion_tokens_total"] == num_threads * iterations * 20
    )
    assert metrics["tool_invocations"]["total"] == num_threads * iterations
    assert (
        metrics["security"]["authorization_denied"] == num_threads * iterations
    )
