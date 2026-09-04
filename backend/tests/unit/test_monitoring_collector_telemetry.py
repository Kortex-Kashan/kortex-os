"""Unit tests for KORTEX Monitoring SystemTelemetryCollector and MetricsCollector.

Tests:
- Host/process system resource telemetry via standard library only
- CPU calculation (sample 0 returns 0.0, sample 1 calculates delta)
- Engine polling isolation (healthy engine, failing engine, slow engine timeout)
- Self-exclusion of monitoring engine
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from kortex.engines.monitoring.collector import MetricsCollector, SystemTelemetryCollector
from kortex.engines.monitoring.constants import MONITORING_ENGINE_NAME
from kortex.engines.monitoring.registry import MetricRegistry
from kortex.engines.monitoring.timeseries import TimeSeriesBuffer


@pytest.mark.asyncio
async def test_system_telemetry_collector_portable() -> None:
    """Verify system telemetry gathers memory, CPU, threads, tasks, and lag using stdlib."""
    collector = SystemTelemetryCollector()

    # Sample 0: CPU must explicitly return 0.0
    telemetry0 = await collector.collect_system_telemetry()
    assert telemetry0["cpu_percent"] == 0.0
    assert telemetry0["memory_working_set_bytes"] >= 0
    assert telemetry0["thread_count"] >= 1
    assert telemetry0["asyncio_task_count"] >= 1
    assert telemetry0["event_loop_lag_seconds"] >= 0.0

    # Small delay then sample 1: CPU can compute delta
    await asyncio.sleep(0.01)
    telemetry1 = await collector.collect_system_telemetry()
    assert telemetry1["cpu_percent"] >= 0.0
    assert telemetry1["memory_working_set_mb"] >= 0.0


@pytest.mark.asyncio
async def test_metrics_collector_healthy_engine() -> None:
    """Verify collector queries healthy engine diagnostics, normalizes metrics, and records them."""
    reg = MetricRegistry()
    ts = TimeSeriesBuffer()
    collector = MetricsCollector(registry=reg, timeseries_buffer=ts)

    # Mock healthy engine
    engine = MagicMock()
    engine.name = "storage"
    engine.health.return_value = {"status": "HEALTHY", "active_connections": 10}
    engine.metrics.return_value = {"reads_total": 500}
    engine.diagnostics.return_value = {"version": "1.0.0"}

    mock_kernel = MagicMock()
    mock_kernel.get_all_engines.return_value = {"storage": engine}

    result = await collector.collect_cycle(mock_kernel)
    assert result["cycle"] == 1
    assert "storage" in result["engines_polled"]
    assert collector.engine_failures_total == 0
    assert collector.engine_timeouts_total == 0

    # Check metrics recorded in registry
    assert reg.active_series_count >= 2
    g_conn = reg.get_metric("storage.active_connections{subsystem=storage}")
    assert g_conn is not None
    assert g_conn.value == 10.0


@pytest.mark.asyncio
async def test_metrics_collector_slow_engine_timeout() -> None:
    """Verify slow engine exceeding probe_timeout_seconds times out without blocking or crashing."""
    reg = MetricRegistry()
    ts = TimeSeriesBuffer()
    # Fast 0.05s timeout for test
    collector = MetricsCollector(registry=reg, timeseries_buffer=ts, probe_timeout_seconds=0.05)

    class SlowEngine:
        name = "slow_engine"

        async def health(self) -> dict[str, Any]:
            await asyncio.sleep(0.2)
            return {"status": "HEALTHY"}

    mock_kernel = MagicMock()
    mock_kernel.get_all_engines.return_value = {"slow": SlowEngine()}

    result = await collector.collect_cycle(mock_kernel)
    assert "slow" in result["engines_polled"]
    assert collector.engine_timeouts_total == 1
    assert collector.engine_failures_total == 0


@pytest.mark.asyncio
async def test_metrics_collector_failing_engine_isolated() -> None:
    """Verify failing engine throwing an exception is isolated and counted without taking collector down."""
    reg = MetricRegistry()
    ts = TimeSeriesBuffer()
    collector = MetricsCollector(registry=reg, timeseries_buffer=ts)

    class BrokenEngine:
        name = "broken"

        def health(self) -> dict[str, Any]:
            raise RuntimeError("Database connection exploded")

    mock_kernel = MagicMock()
    mock_kernel.get_all_engines.return_value = {"broken": BrokenEngine()}

    result = await collector.collect_cycle(mock_kernel)
    assert "broken" in result["engines_polled"]
    assert collector.engine_failures_total == 1
    assert collector.engine_timeouts_total == 0


@pytest.mark.asyncio
async def test_metrics_collector_self_exclusion() -> None:
    """Verify collector skips the monitoring engine itself to avoid recursive monitoring."""
    reg = MetricRegistry()
    ts = TimeSeriesBuffer()
    collector = MetricsCollector(registry=reg, timeseries_buffer=ts)

    mock_kernel = MagicMock()
    mock_kernel.get_all_engines.return_value = {
        MONITORING_ENGINE_NAME: MagicMock(),
        "storage": MagicMock(health=MagicMock(return_value={"status": "HEALTHY"})),
    }

    result = await collector.collect_cycle(mock_kernel)
    assert MONITORING_ENGINE_NAME not in result["engines_polled"]
    assert "storage" in result["engines_polled"]
