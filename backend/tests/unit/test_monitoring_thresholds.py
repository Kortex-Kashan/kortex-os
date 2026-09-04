"""Unit tests for KORTEX Monitoring ThresholdEvaluator.

Tests:
- Consecutive cycles requirement (2 cycles required to trip)
- Severity escalation (NORMAL -> WARNING -> CRITICAL)
- 10% hysteresis deadband on recovery
- 60-second alert cooldown suppression
- Event emission for threshold exceeded and recovered
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from kortex.engines.monitoring.events import MonitoringEventPublisher
from kortex.engines.monitoring.models import MetricType, MetricValue, ThresholdSeverity
from kortex.engines.monitoring.thresholds import ThresholdEvaluator, ThresholdRule


def _make_metric(name: str, value: float, subsystem: str = "system") -> MetricValue:
    return MetricValue(
        name=name,
        type=MetricType.GAUGE,
        value=value,
        labels={"subsystem": subsystem},
        timestamp="2026-09-04T12:00:00Z",
    )


@pytest.mark.asyncio
async def test_threshold_consecutive_cycles_requirement() -> None:
    """Verify threshold condition requires 2 consecutive cycles before asserting alert and emitting event."""
    mock_publisher = AsyncMock(spec=MonitoringEventPublisher)
    rule = ThresholdRule(
        metric_name="system.memory.working_set_mb",
        subsystem="system",
        warning_threshold=1000.0,
        critical_threshold=2000.0,
        consecutive_cycles_required=2,
    )
    evaluator = ThresholdEvaluator(event_publisher=mock_publisher, rules=[rule])

    # Cycle 1: breach (1500 MB >= 1000 MB)
    m1 = [_make_metric("system.memory.working_set_mb", 1500.0)]
    alerts1 = await evaluator.evaluate_metrics(m1)
    # Alert is not yet asserted because only 1 cycle elapsed
    assert len(alerts1) == 0
    assert mock_publisher.emit_threshold_exceeded.call_count == 0

    # Cycle 2: 2nd consecutive breach -> alert asserted and event emitted
    alerts2 = await evaluator.evaluate_metrics(m1)
    assert len(alerts2) == 1
    assert alerts2[0].severity == ThresholdSeverity.WARNING
    assert alerts2[0].consecutive_breaches == 2
    assert mock_publisher.emit_threshold_exceeded.call_count == 1


@pytest.mark.asyncio
async def test_threshold_single_spike_cleared_without_alert() -> None:
    """Verify a single-cycle transient spike that clears on the next cycle never asserts an alert."""
    mock_publisher = AsyncMock(spec=MonitoringEventPublisher)
    rule = ThresholdRule(
        metric_name="system.cpu.percent",
        subsystem="system",
        warning_threshold=80.0,
        critical_threshold=95.0,
        consecutive_cycles_required=2,
    )
    evaluator = ThresholdEvaluator(event_publisher=mock_publisher, rules=[rule])

    # Cycle 1: transient spike to 85%
    await evaluator.evaluate_metrics([_make_metric("system.cpu.percent", 85.0)])
    assert mock_publisher.emit_threshold_exceeded.call_count == 0

    # Cycle 2: drops back to 40%
    alerts = await evaluator.evaluate_metrics([_make_metric("system.cpu.percent", 40.0)])
    assert len(alerts) == 0
    assert mock_publisher.emit_threshold_exceeded.call_count == 0


@pytest.mark.asyncio
async def test_threshold_hysteresis_deadband_and_recovery() -> None:
    """Verify 10% hysteresis: dropping below warning threshold but inside deadband stays in alert;

    dropping below warning * 0.90 recovers.
    """
    mock_publisher = AsyncMock(spec=MonitoringEventPublisher)
    rule = ThresholdRule(
        metric_name="system.memory.working_set_mb",
        subsystem="system",
        warning_threshold=1000.0,
        critical_threshold=2000.0,
        hysteresis_pct=0.10,  # Recovery ceiling = 1000 * 0.9 = 900.0 MB
        consecutive_cycles_required=1,  # 1 cycle for easier state progression test
    )
    evaluator = ThresholdEvaluator(event_publisher=mock_publisher, rules=[rule])

    # 1. Breach into WARNING
    await evaluator.evaluate_metrics([_make_metric("system.memory.working_set_mb", 1200.0)])
    assert mock_publisher.emit_threshold_exceeded.call_count == 1

    # 2. Value drops to 950 MB (below 1000, but ABOVE recovery ceiling of 900 MB)
    alerts_deadband = await evaluator.evaluate_metrics([_make_metric("system.memory.working_set_mb", 950.0)])
    # Still active due to hysteresis
    assert len(alerts_deadband) == 1
    assert alerts_deadband[0].current_value == 950.0
    assert mock_publisher.emit_threshold_recovered.call_count == 0

    # 3. Value drops to 850 MB (below 900 MB recovery ceiling)
    alerts_recovered = await evaluator.evaluate_metrics([_make_metric("system.memory.working_set_mb", 850.0)])
    assert len(alerts_recovered) == 0
    assert mock_publisher.emit_threshold_recovered.call_count == 1
    _, kwargs = mock_publisher.emit_threshold_recovered.call_args
    assert kwargs.get("previous_severity") == "WARNING"


@pytest.mark.asyncio
async def test_threshold_severity_escalation_bypasses_cooldown() -> None:
    """Verify escalation from WARNING to CRITICAL emits event immediately even within cooldown window."""
    mock_publisher = AsyncMock(spec=MonitoringEventPublisher)
    rule = ThresholdRule(
        metric_name="system.cpu.percent",
        subsystem="system",
        warning_threshold=80.0,
        critical_threshold=95.0,
        consecutive_cycles_required=1,
        cooldown_seconds=60.0,
    )
    evaluator = ThresholdEvaluator(event_publisher=mock_publisher, rules=[rule])

    # 1. Enter WARNING at 85%
    await evaluator.evaluate_metrics([_make_metric("system.cpu.percent", 85.0)])
    assert mock_publisher.emit_threshold_exceeded.call_count == 1

    # 2. Stay at 88% (still WARNING, within 60s cooldown -> suppressed)
    await evaluator.evaluate_metrics([_make_metric("system.cpu.percent", 88.0)])
    assert mock_publisher.emit_threshold_exceeded.call_count == 1  # No duplicate event

    # 3. Escalate to CRITICAL at 98% -> emits immediately
    await evaluator.evaluate_metrics([_make_metric("system.cpu.percent", 98.0)])
    assert mock_publisher.emit_threshold_exceeded.call_count == 2
    _, kwargs = mock_publisher.emit_threshold_exceeded.call_args
    assert kwargs.get("severity") == "CRITICAL"
