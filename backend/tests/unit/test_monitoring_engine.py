"""Unit tests for KORTEX MonitoringEngine.

Tests:
- BaseEngine lifecycle (UNINITIALIZED -> INITIALIZING -> READY -> RUNNING -> STOPPED)
- Repeated startup and clean shutdown
- Background task management and cancellation
- Sentinel integration (public event cache, public IEngineDiagnostics contract, graceful absence)
- Exactly 4 registered capabilities with correct permissions and classification
- Direct dashboard composition without nested dispatcher invocation
- IEngineDiagnostics contract methods
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.event.engine import Event
from kortex.engines.monitoring.constants import (
    EVENT_SENTINEL_HEALTH_CHANGED,
    MONITORING_CAPABILITIES,
    MONITORING_SECURITY_CLASSIFICATION,
    PERMISSION_MONITORING_READ,
)
from kortex.engines.monitoring.engine import MonitoringEngine
from kortex.engines.monitoring.models import MonitoringConfig


@pytest.mark.asyncio
async def test_monitoring_engine_lifecycle() -> None:
    """Verify clean state progression through initialize, start, and stop."""
    engine = MonitoringEngine(config=MonitoringConfig(collect_interval_seconds=60.0))
    assert engine.state == EngineState.UNINITIALIZED
    assert engine.name == "monitoring"

    mock_kernel = MagicMock()
    mock_kernel.get_all_engines.return_value = {}

    await engine.initialize(mock_kernel)
    assert engine.state == EngineState.READY

    await engine.start()
    assert engine.state == EngineState.RUNNING
    assert len(engine.background_tasks) == 1

    await engine.stop()
    assert engine.state == EngineState.STOPPED
    assert len(engine.background_tasks) == 0


@pytest.mark.asyncio
async def test_monitoring_engine_repeated_start_stop() -> None:
    """Verify engine can be safely initialized, started, stopped, and restarted."""
    engine = MonitoringEngine(config=MonitoringConfig(collect_interval_seconds=60.0))
    mock_kernel = MagicMock()
    mock_kernel.get_all_engines.return_value = {}

    await engine.initialize(mock_kernel)
    await engine.start()
    assert engine.state == EngineState.RUNNING

    await engine.stop()
    assert engine.state == EngineState.STOPPED

    # Second start-stop cycle
    engine._set_state(EngineState.READY)
    await engine.start()
    assert engine.state == EngineState.RUNNING
    await engine.stop()
    assert engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_monitoring_engine_capability_registration() -> None:
    """Verify exactly 4 capabilities are registered with the Kernel with strict security metadata."""
    engine = MonitoringEngine()
    mock_kernel = MagicMock()
    mock_kernel.get_all_engines.return_value = {}

    await engine.initialize(mock_kernel)

    assert mock_kernel.register_capability.call_count == 4
    registered_names = set()
    for call in mock_kernel.register_capability.call_args_list:
        kwargs = call.kwargs
        registered_names.add(kwargs["name"])
        assert kwargs["provider"] == "monitoring"
        assert kwargs["requires_authentication"] is True
        assert kwargs["required_permissions"] == [PERMISSION_MONITORING_READ]
        assert kwargs["security_classification"] == MONITORING_SECURITY_CLASSIFICATION
        assert kwargs["requires_execution_context"] is True

    assert registered_names == set(MONITORING_CAPABILITIES)


@pytest.mark.asyncio
async def test_sentinel_integration_public_event_cache() -> None:
    """Verify Monitoring consumes public Sentinel health changed event and caches state."""
    engine = MonitoringEngine()
    mock_kernel = MagicMock()
    mock_kernel.get_all_engines.return_value = {}
    mock_kernel.subscribe_event.return_value = "sub_123"

    await engine.initialize(mock_kernel)
    assert mock_kernel.subscribe_event.call_count == 1
    _, kwargs = mock_kernel.subscribe_event.call_args
    assert kwargs["topic"] == EVENT_SENTINEL_HEALTH_CHANGED

    # Simulate arrival of public event
    event = MagicMock(spec=Event)
    event.payload = {
        "previous_status": "UNKNOWN",
        "current_status": "HEALTHY",
        "healthy": True,
        "degraded_subsystems": [],
    }
    await engine._on_sentinel_health_changed(event)

    # Resolve Sentinel health returns the cached payload
    resolved = await engine._resolve_sentinel_health()
    assert resolved["current_status"] == "HEALTHY"
    assert resolved["healthy"] is True


@pytest.mark.asyncio
async def test_sentinel_integration_absent_fallback() -> None:
    """Verify graceful fallback to UNKNOWN status when Sentinel is absent."""
    engine = MonitoringEngine()
    mock_kernel = MagicMock()
    mock_kernel.get_engine.return_value = None  # Sentinel absent
    engine._kernel = mock_kernel

    resolved = await engine._resolve_sentinel_health()
    assert resolved["status"] == "UNKNOWN"
    assert resolved["healthy"] is None


@pytest.mark.asyncio
async def test_dashboard_direct_composition() -> None:
    """Verify get_dashboard performs direct internal composition without calling capability dispatcher."""
    engine = MonitoringEngine()
    mock_kernel = MagicMock()
    mock_kernel.get_all_engines.return_value = {}
    mock_kernel.get_engine.return_value = None
    engine._kernel = mock_kernel

    # Populate some test metrics
    engine.registry.gauge("system.memory.working_set_mb", {"subsystem": "system"}).set(512.0)
    engine.registry.counter("storage.reads_total", {"subsystem": "storage"}).inc(10.0)

    dashboard = await engine.get_dashboard()
    assert dashboard.timestamp is not None
    assert isinstance(dashboard.sentinel_health, dict)
    assert isinstance(dashboard.system_resources, dict)
    assert isinstance(dashboard.top_metrics, list)
    assert len(dashboard.top_metrics) >= 1
    assert isinstance(dashboard.active_alerts, list)


@pytest.mark.asyncio
async def test_capability_handlers_execution_context() -> None:
    """Verify capability handlers execute and accept CapabilityExecutionContext."""
    engine = MonitoringEngine()
    mock_kernel = MagicMock()
    mock_kernel.get_all_engines.return_value = {}
    engine._kernel = mock_kernel

    engine.registry.gauge("system.cpu.percent", {"subsystem": "system"}).set(15.0)

    # 1. metrics.get
    ctx = MagicMock(spec=CapabilityExecutionContext)
    metrics_res = await engine.handle_metrics_get(execution_context=ctx, subsystem="system")
    assert isinstance(metrics_res, list)
    assert any(m["name"] == "system.cpu.percent" for m in metrics_res)

    # 2. timeseries.get
    ts_res = await engine.handle_timeseries_get(execution_context=ctx, metric_name="system.cpu.percent")
    assert isinstance(ts_res, dict)
    assert ts_res["metric_name"] == "system.cpu.percent"

    # 3. dashboard.get
    dash_res = await engine.handle_dashboard_get(execution_context=ctx)
    assert isinstance(dash_res, dict)
    assert "system_resources" in dash_res

    # 4. diagnostics.get
    diag_res = await engine.handle_diagnostics_get(execution_context=ctx)
    assert isinstance(diag_res, dict)
    assert diag_res["engine"] == "monitoring"
    assert diag_res["version"] == "1.0.0"


def test_iengine_diagnostics_contract() -> None:
    """Verify MonitoringEngine adheres to IEngineDiagnostics protocol."""
    engine = MonitoringEngine()
    assert engine.status() == "UNINITIALIZED"
    assert engine.version() == "1.0.0"
    assert set(engine.capabilities()) == set(MONITORING_CAPABILITIES)

    h = engine.health()
    assert "status" in h
    assert "healthy" in h

    m = engine.metrics()
    assert "collection_cycles_total" in m
    assert "active_series" in m

    d = engine.diagnostics()
    assert "system_resources" in d
    assert "active_alerts_count" in d
