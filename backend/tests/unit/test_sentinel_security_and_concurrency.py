"""Unit tests for Sentinel Security, Persistence Boundaries, and Concurrency.

Covers:
- Capability security declarations (requires_authentication, INTERNAL classification, system:sentinel:read)
- Read-only execution context contracts
- Bounded in-memory ring buffer (100 items max, deterministic FIFO eviction)
- Ephemeral state clearance on engine recreation
- Concurrent heartbeat registrations and operation tracking
- Clean shutdown during active background monitoring loop
- Failure isolation (Sentinel never terminates or restarts subsystems)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.container import Container
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.sentinel.constants import (
    CAPABILITY_DIAGNOSTICS_GET,
    CAPABILITY_HEALTH_GET,
    CAPABILITY_STATUS_GET,
    SENTINEL_PERMISSION_READ,
    SENTINEL_SECURITY_CLASSIFICATION,
)
from kortex.engines.sentinel.engine import SentinelEngine
from kortex.engines.sentinel.incident import IncidentStore
from kortex.engines.sentinel.models import FailureClass, SentinelConfig, SentinelStatus


@pytest.fixture
def mock_kernel() -> Kernel:
    """Create a mock Kernel with container and capability registration."""
    kernel = MagicMock(spec=Kernel)
    kernel.state = KernelState.RUNNING
    container = Container()
    kernel.container = container
    kernel.get_all_engines.return_value = {}
    kernel.list_capabilities.return_value = []
    kernel.register_capability = MagicMock()
    kernel.publish_event = AsyncMock()
    return kernel


# -- 1. Security & Capability Contracts --------------------------------------


@pytest.mark.asyncio
async def test_capabilities_security_registration(mock_kernel: Kernel) -> None:
    """Verify all 3 Sentinel capabilities require authentication, INTERNAL clearance, and proper permission."""
    engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False))
    await engine.initialize(mock_kernel)

    # 3 capability calls
    assert mock_kernel.register_capability.call_count == 3

    registered_names = set()
    for call in mock_kernel.register_capability.call_args_list:
        kwargs = call[1]
        name = kwargs["name"]
        registered_names.add(name)

        assert kwargs["requires_authentication"] is True
        assert kwargs["security_classification"] == SENTINEL_SECURITY_CLASSIFICATION
        assert kwargs["required_permissions"] == [SENTINEL_PERMISSION_READ]
        assert kwargs["requires_execution_context"] is True
        assert callable(kwargs["handler"])

    assert registered_names == {
        CAPABILITY_HEALTH_GET,
        CAPABILITY_STATUS_GET,
        CAPABILITY_DIAGNOSTICS_GET,
    }


# -- 2. Persistence & Bounded Ring Buffer ------------------------------------


def test_bounded_incident_ring_buffer_eviction() -> None:
    """Verify in-memory incident ring buffer strictly enforces bounded capacity with FIFO eviction."""
    max_entries = 10
    store = IncidentStore(max_size=max_entries)

    for i in range(25):
        store.record_incident(
            subsystem=f"sub_{i}",
            failure_class=FailureClass.TRANSIENT,
            health_status=SentinelStatus.DEGRADED,
            message=f"Incident {i}",
        )

    incidents = store.get_recent_incidents()
    assert len(incidents) == max_entries

    # Most recent incident should be sub_24, oldest in buffer should be sub_15
    assert incidents[0]["subsystem"] == "sub_24"
    assert incidents[-1]["subsystem"] == "sub_15"


def test_ephemeral_state_cleared_on_reset() -> None:
    """Verify clearing the incident store removes all diagnostic state."""
    store = IncidentStore()
    store.record_incident("test", FailureClass.TRANSIENT, SentinelStatus.DEGRADED, "msg")
    assert len(store.get_recent_incidents()) == 1

    store.clear()
    assert len(store.get_recent_incidents()) == 0


# -- 3. Concurrency & Clean Shutdown -----------------------------------------


@pytest.mark.asyncio
async def test_concurrent_heartbeat_recordings() -> None:
    """Verify concurrent heartbeat pings from multiple async tasks operate safely."""
    engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False))
    hm = engine.heartbeat_manager

    hm.register_source("worker.concurrent", expected_interval_seconds=10.0)

    async def ping_worker(n: int) -> None:
        for _ in range(20):
            hm.record_heartbeat("worker.concurrent")
            await asyncio.sleep(0.001)

    tasks = [asyncio.create_task(ping_worker(i)) for i in range(5)]
    await asyncio.gather(*tasks)

    assert hm.has_source("worker.concurrent")
    assert hm._sources["worker.concurrent"].heartbeat_count == 100


@pytest.mark.asyncio
async def test_clean_shutdown_during_active_monitoring_loop(mock_kernel: Kernel) -> None:
    """Verify Sentinel shuts down cleanly without leaking tasks while background monitor is active."""
    # Configure fast monitor interval
    cfg = SentinelConfig(
        monitor_interval_seconds=0.05,
        enable_background_monitor=True,
    )
    engine = SentinelEngine(config=cfg)

    await engine.initialize(mock_kernel)
    await engine.start()
    assert engine.state == EngineState.RUNNING
    assert len(engine.background_tasks) == 1

    # Let the loop run for a few ticks
    await asyncio.sleep(0.12)

    # Clean shutdown
    await engine.stop()
    assert engine.state == EngineState.STOPPED
    assert len(engine.background_tasks) == 0


# -- 4. Failure Isolation ----------------------------------------------------


@pytest.mark.asyncio
async def test_failure_isolation_no_destructive_actions(mock_kernel: Kernel) -> None:
    """Verify Sentinel detects catastrophic failure but never restarts or terminates engines."""
    engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False, startup_grace_seconds=0.0))

    mock_crashed = MagicMock()
    mock_crashed.state = EngineState.FAILED
    mock_crashed.stop = AsyncMock()
    mock_crashed.start = AsyncMock()

    mock_kernel.get_all_engines.return_value = {"critical_engine": mock_crashed}

    await engine.initialize(mock_kernel)
    await engine.start()

    report = await engine.evaluate_health()
    assert report.status == SentinelStatus.FAILED

    # Sentinel must NEVER call stop() or start() on another engine!
    assert mock_crashed.stop.call_count == 0
    assert mock_crashed.start.call_count == 0

    await engine.stop()
