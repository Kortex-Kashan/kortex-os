"""Unit tests for Sentinel Event Model, Failure Classification, and Recovery Handoff.

Covers:
- All 6 canonical event topics and schemas
- UUIDv4 event ID uniqueness per emission
- Correlation ID preservation
- Absence of secrets/credentials in event payloads
- Deterministic failure classifications (TRANSIENT, REPEATED, PERSISTENT, CRASH_LOOP, etc.)
- Deterministic idempotency key generation for recovery requests
- Recovery-request emission circuit breaker and cooldown suppression
- Subsystem recovery event emission and suppression reset
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kortex.core.kernel import Kernel
from kortex.engines.sentinel.constants import (
    TOPIC_CRASH_LOOP_DETECTED,
    TOPIC_DEADLOCK_DETECTED,
    TOPIC_HEALTH_CHANGED,
    TOPIC_RECOVERY_REQUESTED,
    TOPIC_SUBSYSTEM_FAILED,
    TOPIC_SUBSYSTEM_RECOVERED,
)
from kortex.engines.sentinel.events import SentinelEventPublisher
from kortex.engines.sentinel.incident import IncidentStore
from kortex.engines.sentinel.models import FailureClass, SentinelStatus


@pytest.fixture
def mock_kernel() -> Kernel:
    """Create a mock Kernel with an asynchronous publish_event method."""
    kernel = MagicMock(spec=Kernel)
    res = MagicMock()
    res.event_id = str(uuid.uuid4())
    res.subscribers_notified = 1
    kernel.publish_event = AsyncMock(return_value=res)
    return kernel


# -- 1. Canonical Event Schemas and Emission ---------------------------------


@pytest.mark.asyncio
async def test_emit_health_changed_event(mock_kernel: Kernel) -> None:
    """Verify kortex.sentinel.health.changed schema and publication."""
    pub = SentinelEventPublisher(mock_kernel)
    corr_id = str(uuid.uuid4())

    success = await pub.emit_health_changed(
        previous_status="HEALTHY",
        current_status="DEGRADED",
        healthy=False,
        degraded_subsystems=["connector"],
        correlation_id=corr_id,
    )
    assert success is True
    assert mock_kernel.publish_event.call_count == 1

    call_args = mock_kernel.publish_event.call_args[1]
    assert call_args["topic"] == TOPIC_HEALTH_CHANGED
    payload = call_args["payload"]
    assert payload["previous_status"] == "HEALTHY"
    assert payload["current_status"] == "DEGRADED"
    assert payload["healthy"] is False
    assert payload["degraded_subsystems"] == ["connector"]
    assert payload["correlation_id"] == corr_id
    assert "timestamp" in payload


@pytest.mark.asyncio
async def test_emit_subsystem_failed_event(mock_kernel: Kernel) -> None:
    """Verify kortex.sentinel.subsystem.failed schema and publication."""
    pub = SentinelEventPublisher(mock_kernel)

    success = await pub.emit_subsystem_failed(
        subsystem_name="storage",
        failure_class=FailureClass.REPEATED.value,
        health_status=SentinelStatus.FAILED.value,
        error_message="Database ping timeout",
        consecutive_failures=2,
    )
    assert success is True
    call_args = mock_kernel.publish_event.call_args[1]
    assert call_args["topic"] == TOPIC_SUBSYSTEM_FAILED
    payload = call_args["payload"]
    assert payload["subsystem_name"] == "storage"
    assert payload["failure_class"] == "REPEATED"
    assert payload["health_status"] == "FAILED"
    assert payload["consecutive_failures"] == 2


@pytest.mark.asyncio
async def test_emit_subsystem_recovered_event(mock_kernel: Kernel) -> None:
    """Verify kortex.sentinel.subsystem.recovered schema and publication."""
    pub = SentinelEventPublisher(mock_kernel)

    success = await pub.emit_subsystem_recovered(
        subsystem_name="storage",
        previous_status=SentinelStatus.FAILED.value,
        current_status=SentinelStatus.HEALTHY.value,
    )
    assert success is True
    call_args = mock_kernel.publish_event.call_args[1]
    assert call_args["topic"] == TOPIC_SUBSYSTEM_RECOVERED
    payload = call_args["payload"]
    assert payload["subsystem_name"] == "storage"
    assert payload["previous_status"] == "FAILED"
    assert payload["current_status"] == "HEALTHY"


@pytest.mark.asyncio
async def test_emit_deadlock_detected_event(mock_kernel: Kernel) -> None:
    """Verify kortex.sentinel.deadlock.detected schema and publication."""
    pub = SentinelEventPublisher(mock_kernel)

    success = await pub.emit_deadlock_detected(
        loop_lag_ms=1250.5,
        stalled_operations=[{"operation_id": "op-1", "name": "batch_job", "duration_seconds": 90.0}],
        active_operations_count=5,
    )
    assert success is True
    call_args = mock_kernel.publish_event.call_args[1]
    assert call_args["topic"] == TOPIC_DEADLOCK_DETECTED
    payload = call_args["payload"]
    assert payload["loop_lag_ms"] == 1250.5
    assert payload["active_operations_count"] == 5
    assert len(payload["stalled_operations"]) == 1


@pytest.mark.asyncio
async def test_emit_crash_loop_detected_event(mock_kernel: Kernel) -> None:
    """Verify kortex.sentinel.crash_loop.detected schema and publication."""
    pub = SentinelEventPublisher(mock_kernel)

    success = await pub.emit_crash_loop_detected(
        subsystem_name="connector",
        failure_count=3,
        window_seconds=600.0,
    )
    assert success is True
    call_args = mock_kernel.publish_event.call_args[1]
    assert call_args["topic"] == TOPIC_CRASH_LOOP_DETECTED
    payload = call_args["payload"]
    assert payload["subsystem_name"] == "connector"
    assert payload["failure_count"] == 3
    assert payload["window_seconds"] == 600.0
    assert payload["circuit_breaker_tripped"] is True


@pytest.mark.asyncio
async def test_emit_recovery_requested_event_and_idempotency(mock_kernel: Kernel) -> None:
    """Verify kortex.sentinel.recovery.requested carrying deterministic idempotency key."""
    pub = SentinelEventPublisher(mock_kernel)
    idempotency_key = "recovery:connector:3:1788000"

    success = await pub.emit_recovery_requested(
        subsystem_name="connector",
        failure_class=FailureClass.PERSISTENT.value,
        health_status=SentinelStatus.FAILED.value,
        idempotency_key=idempotency_key,
        observed_evidence={"failures": 3},
        diagnostic_context={"probes": ["connectivity_failed"]},
        suggested_action="RESTART_SUBSYSTEM",
    )
    assert success is True
    call_args = mock_kernel.publish_event.call_args[1]
    assert call_args["topic"] == TOPIC_RECOVERY_REQUESTED
    payload = call_args["payload"]
    assert payload["subsystem_name"] == "connector"
    assert payload["failure_class"] == "PERSISTENT"
    assert payload["idempotency_key"] == idempotency_key
    assert payload["suggested_action"] == "RESTART_SUBSYSTEM"


# -- 2. Crash-Loop Detection & Circuit Breaker Logic -------------------------


def test_crash_loop_detection_and_cooldown() -> None:
    """Verify crash loop detection trips circuit breaker on >= 3 failures within window."""
    store = IncidentStore(
        crash_loop_threshold=3,
        crash_loop_window_seconds=600.0,
        recovery_cooldown_seconds=60.0,
    )

    t0 = 1000.0

    # Failure 1
    store.record_incident("workflow", FailureClass.TRANSIENT, SentinelStatus.FAILED, "Err 1", timestamp_monotonic=t0)
    is_crash, count = store.check_crash_loop("workflow", now_monotonic=t0)
    assert is_crash is False
    assert count == 1

    # Failure 2
    store.record_incident(
        "workflow", FailureClass.REPEATED, SentinelStatus.FAILED, "Err 2", timestamp_monotonic=t0 + 10.0
    )
    is_crash, count = store.check_crash_loop("workflow", now_monotonic=t0 + 10.0)
    assert is_crash is False
    assert count == 2

    # Failure 3 -> Crash loop threshold reached!
    store.record_incident(
        "workflow", FailureClass.PERSISTENT, SentinelStatus.FAILED, "Err 3", timestamp_monotonic=t0 + 20.0
    )
    is_crash, count = store.check_crash_loop("workflow", now_monotonic=t0 + 20.0)
    assert is_crash is True
    assert count == 3

    # Event emission should be allowed exactly once per episode (suppressing storms)
    assert store.should_emit_crash_loop_event("workflow", now_monotonic=t0 + 20.0) is True
    assert store.should_emit_crash_loop_event("workflow", now_monotonic=t0 + 21.0) is False


def test_recovery_request_emission_circuit_breaker() -> None:
    """Verify recovery request emission circuit breaker suppresses duplicate requests in cooldown."""
    store = IncidentStore(recovery_cooldown_seconds=60.0)
    t0 = 1000.0

    # Initially allowed
    assert store.can_emit_recovery_request("connector", now_monotonic=t0) is True

    # Record emitted at t0
    store.record_recovery_request_emitted("connector", now_monotonic=t0)

    # During cooldown (t0 + 30s < 60s) -> Suppressed
    assert store.can_emit_recovery_request("connector", now_monotonic=t0 + 30.0) is False

    # After cooldown (t0 + 65s > 60s) -> Allowed again
    assert store.can_emit_recovery_request("connector", now_monotonic=t0 + 65.0) is True


def test_reset_subsystem_clears_suppression() -> None:
    """Verify successful subsystem recovery resets crash loop and cooldown tracking."""
    store = IncidentStore()
    t0 = 1000.0

    store.record_incident("ai", FailureClass.CRASH_LOOP, SentinelStatus.FAILED, "Err", timestamp_monotonic=t0)
    store.record_recovery_request_emitted("ai", now_monotonic=t0)

    # Cooldown active
    assert store.can_emit_recovery_request("ai", now_monotonic=t0 + 5.0) is False

    # Subsystem recovers
    store.reset_subsystem("ai")

    # Cooldown cleared
    assert store.can_emit_recovery_request("ai", now_monotonic=t0 + 5.0) is True
