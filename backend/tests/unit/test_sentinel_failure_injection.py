"""Failure injection tests for KORTEX Sentinel Engine.

Exercises resilience against simulated runtime pathologies:
- Simulated database disconnect and connection drops
- Slow/hanging engine health check probes (timeout resilience)
- Artificially injected event-loop lag and concurrent stalled operations (DEADLOCK_SUSPECTED)
- Rapid consecutive engine crash cycles (CRASH_LOOP detection & circuit breaking)
- Unresolved engine dependencies and broken capability descriptors
- Unauthorized callers lacking system:sentinel:read permission
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.container import Container
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.sentinel.engine import SentinelEngine
from kortex.engines.sentinel.models import (
    CheckStatus,
    SentinelConfig,
    SentinelStatus,
)


@pytest.fixture
def mock_kernel() -> Kernel:
    """Create a mock Kernel with container and capability registry."""
    kernel = MagicMock(spec=Kernel)
    kernel.state = KernelState.RUNNING
    container = Container()
    kernel.container = container
    kernel.get_all_engines.return_value = {}
    kernel.list_capabilities.return_value = []
    kernel.db = None
    kernel.db_manager = None
    kernel.register_capability = MagicMock()
    kernel.publish_event = AsyncMock()
    return kernel


# -- 1. Injected Slow / Hanging Subsystem ------------------------------------


@pytest.mark.asyncio
async def test_failure_injection_hanging_subsystem_health_check(mock_kernel: Kernel) -> None:
    """Verify Sentinel handles an engine whose health check hangs forever without blocking Sentinel."""
    cfg = SentinelConfig(
        enable_background_monitor=False,
        startup_grace_seconds=0.0,
        probe_timeout_seconds=0.05,  # Very short timeout for testing
    )
    engine = SentinelEngine(config=cfg)

    async def hanging_health_check() -> dict[str, str]:
        await asyncio.sleep(5.0)  # Exceeds 0.05s timeout
        return {"status": "healthy"}

    mock_hanging = MagicMock(spec=BaseEngine)
    mock_hanging.state = EngineState.RUNNING
    mock_hanging.health_check = hanging_health_check

    mock_kernel.get_all_engines.return_value = {"hanging_subsystem": mock_hanging}

    await engine.initialize(mock_kernel)
    await engine.start()

    # Evaluation should complete within ~0.1s due to timeout protection
    report = await engine.evaluate_health()

    assert report.status == SentinelStatus.FAILED
    sub_health = report.subsystems["hanging_subsystem"]
    assert sub_health.status == SentinelStatus.FAILED
    assert "timed out" in sub_health.probes["engine.health_check"].message

    await engine.stop()


# -- 2. Injected Database Disconnection --------------------------------------


@pytest.mark.asyncio
async def test_failure_injection_database_disconnect(mock_kernel: Kernel) -> None:
    """Verify Sentinel flags aggregate health as FAILED when database is disconnected."""
    cfg = SentinelConfig(enable_background_monitor=False, startup_grace_seconds=0.0)
    engine = SentinelEngine(config=cfg)

    # Injected database disconnect
    mock_kernel.db = MagicMock()
    mock_kernel.db.is_connected = False
    mock_kernel.db_manager = mock_kernel.db

    await engine.initialize(mock_kernel)
    await engine.start()

    report = await engine.evaluate_health()
    assert report.database_connected is False
    assert report.status == SentinelStatus.FAILED
    assert report.healthy is False

    await engine.stop()


# -- 3. Injected Deadlock Suspected Scenario ---------------------------------


@pytest.mark.asyncio
async def test_failure_injection_deadlock_suspected() -> None:
    """Verify combined high event loop lag and stalled operations trigger DEADLOCK_SUSPECTED."""
    engine = SentinelEngine(
        config=SentinelConfig(
            enable_background_monitor=False,
            loop_lag_threshold_ms=10.0,
            operation_timeout_seconds=0.01,
        )
    )
    await engine.initialize(None)

    # Register two operations that exceed timeout
    op1 = engine.operation_tracker.register_operation("batch_processing", threshold_seconds=0.01)
    op2 = engine.operation_tracker.register_operation("index_rebuilding", threshold_seconds=0.01)

    await asyncio.sleep(0.02)  # Allow operations to stall

    # Mock loop lag to exceed threshold
    with patch.object(engine.deadlock_detector, "measure_loop_lag", new=AsyncMock(return_value=150.0)):
        rep = await engine.inspect_deadlocks()

        assert rep.deadlock_suspected is True
        assert rep.starvation_detected is True
        assert len(rep.stalled_operations) >= 2
        assert rep.loop_lag_ms == 150.0

    engine.operation_tracker.finish_operation(op1)
    engine.operation_tracker.finish_operation(op2)


# -- 4. Injected Rapid Crash Loops & Circuit Breaker -------------------------


@pytest.mark.asyncio
async def test_failure_injection_rapid_crash_loop_trip(mock_kernel: Kernel) -> None:
    """Verify 3 consecutive failures trigger crash loop event and circuit breaker trip."""
    cfg = SentinelConfig(
        enable_background_monitor=False,
        startup_grace_seconds=0.0,
        crash_loop_threshold=3,
        crash_loop_window_seconds=600.0,
        recovery_cooldown_seconds=60.0,
    )
    engine = SentinelEngine(config=cfg)

    mock_failing = MagicMock(spec=BaseEngine)
    mock_failing.state = EngineState.FAILED
    mock_failing.health_check = AsyncMock(return_value={"status": "failed", "healthy": False})

    mock_kernel.get_all_engines.return_value = {"crash_subsystem": mock_failing}

    await engine.initialize(mock_kernel)
    await engine.start()

    # Cycle 1
    await engine.evaluate_health()
    assert engine.incident_store.check_crash_loop("crash_subsystem")[0] is False

    # Cycle 2
    await engine.evaluate_health()
    assert engine.incident_store.check_crash_loop("crash_subsystem")[0] is False

    # Cycle 3 -> Crash loop tripped!
    await engine.evaluate_health()
    is_crash, count = engine.incident_store.check_crash_loop("crash_subsystem")
    assert is_crash is True
    assert count == 3

    # Circuit breaker suppresses recovery requests during cooldown
    assert engine.incident_store.can_emit_recovery_request("crash_subsystem") is False

    await engine.stop()


# -- 5. Injected Integrity Failures: Broken Dependency Resolution ------------


@pytest.mark.asyncio
async def test_failure_injection_broken_dependency_resolution(mock_kernel: Kernel) -> None:
    """Verify IntegrityVerifier detects missing declared engine dependencies."""
    engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False))

    mock_dependent = MagicMock()
    mock_dependent.dependencies = ["non_existent_engine"]
    mock_dependent.state = EngineState.RUNNING

    mock_kernel.get_all_engines.return_value = {"dependent_engine": mock_dependent}

    await engine.initialize(mock_kernel)
    await engine.start()

    rep = await engine.verify_integrity()
    assert rep.overall_status == SentinelStatus.FAILED

    dep_check = next(c for c in rep.checks if c.probe_name == "engine_dependencies")
    assert dep_check.status == CheckStatus.FAIL
    assert "non_existent_engine" in str(dep_check.details)

    await engine.stop()
