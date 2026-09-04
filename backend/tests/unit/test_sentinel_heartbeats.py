"""Unit tests for Sentinel Heartbeat and Watchdog Manager.

Covers:
- Heartbeat source registration and validation
- Duplicate registration rejection vs explicit replacement
- Unregister behavior and alert elimination
- Monotonic clock age calculations
- Warning and failure threshold evaluations (2x and 3x)
- Startup grace immunity
- Shutdown immunity
- Reset on subsystem restart
"""

from __future__ import annotations

import time

import pytest

from kortex.engines.sentinel.heartbeats import HeartbeatManager
from kortex.engines.sentinel.interfaces import IHeartbeatSource
from kortex.engines.sentinel.models import CheckStatus


class DummyWorker(IHeartbeatSource):
    """Test stub implementing IHeartbeatSource protocol."""

    def __init__(self, source_id: str, interval: float) -> None:
        self._source_id = source_id
        self._interval = interval

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def expected_interval_seconds(self) -> float:
        return self._interval


def test_heartbeat_registration_and_validation() -> None:
    """Verify heartbeat registration with valid and invalid parameters."""
    hm = HeartbeatManager(warning_multiplier=2.0, failure_multiplier=3.0)

    # Valid registration
    hm.register_source("worker.outbox", expected_interval_seconds=15.0, owner="workflow")
    assert hm.has_source("worker.outbox")
    assert hm.registered_count == 1

    # Empty source_id rejected
    with pytest.raises(ValueError, match="must not be empty"):
        hm.register_source("", expected_interval_seconds=10.0)

    # Non-positive interval rejected
    with pytest.raises(ValueError, match="strictly positive"):
        hm.register_source("worker.bad", expected_interval_seconds=0.0)

    with pytest.raises(ValueError, match="strictly positive"):
        hm.register_source("worker.bad2", expected_interval_seconds=-5.0)


def test_duplicate_registration_and_replacement() -> None:
    """Verify duplicate registration fails without replace=True, succeeds with replace=True."""
    hm = HeartbeatManager()
    hm.register_source("worker.queue", expected_interval_seconds=10.0)

    # Duplicate without replace raises ValueError
    with pytest.raises(ValueError, match="already registered"):
        hm.register_source("worker.queue", expected_interval_seconds=10.0, replace=False)

    # Duplicate with replace=True updates registration
    hm.register_source("worker.queue", expected_interval_seconds=20.0, replace=True)
    assert hm.has_source("worker.queue")
    assert hm.registered_count == 1


def test_heartbeat_protocol_registration() -> None:
    """Verify registration using IHeartbeatSource instance."""
    hm = HeartbeatManager()
    worker = DummyWorker(source_id="worker.protocol", interval=5.0)

    hm.register_source(worker, owner="test")
    assert hm.has_source("worker.protocol")


def test_heartbeat_unregister() -> None:
    """Verify unregister removes the source and eliminates failure alerts."""
    hm = HeartbeatManager()
    hm.register_source("worker.ephemeral", expected_interval_seconds=5.0)
    assert hm.has_source("worker.ephemeral")

    # Unregister existing
    assert hm.unregister_source("worker.ephemeral") is True
    assert hm.has_source("worker.ephemeral") is False

    # Unregister unknown returns False
    assert hm.unregister_source("worker.ephemeral") is False

    # Evaluation produces zero probes for unregistered source
    probes = hm.evaluate_all(now_monotonic=time.monotonic() + 100.0)
    assert len(probes) == 0


def test_heartbeat_ping_recording() -> None:
    """Verify heartbeat ping updates the last monotonic timestamp and count."""
    hm = HeartbeatManager()
    hm.register_source("worker.task", expected_interval_seconds=10.0)

    t0 = 1000.0
    t1 = 1005.0

    hm.record_heartbeat("worker.task", timestamp_monotonic=t0)
    hm.record_heartbeat("worker.task", timestamp_monotonic=t1)

    # Ping on unknown source returns False
    assert hm.record_heartbeat("unknown.worker") is False


def test_heartbeat_threshold_evaluations() -> None:
    """Verify warning (2x) and failure (3x) threshold calculations using injected monotonic time."""
    hm = HeartbeatManager(warning_multiplier=2.0, failure_multiplier=3.0)
    t_base = 1000.0
    hm.register_source("worker.tick", expected_interval_seconds=10.0, timestamp_monotonic=t_base)
    hm.record_heartbeat("worker.tick", timestamp_monotonic=t_base)

    # 1. Nominal: age = 5s (< 20s warning) -> PASS
    probes_ok = hm.evaluate_all(now_monotonic=t_base + 5.0)
    assert len(probes_ok) == 1
    assert probes_ok[0].status == CheckStatus.PASS

    # 2. Warning: age = 22s (>= 20s warning, < 30s failure) -> WARN
    probes_warn = hm.evaluate_all(now_monotonic=t_base + 22.0)
    assert len(probes_warn) == 1
    assert probes_warn[0].status == CheckStatus.WARN

    # 3. Failure: age = 35s (>= 30s failure) -> FAIL
    probes_fail = hm.evaluate_all(now_monotonic=t_base + 35.0)
    assert len(probes_fail) == 1
    assert probes_fail[0].status == CheckStatus.FAIL


def test_heartbeat_startup_grace_immunity() -> None:
    """Verify newly registered source without initial pings is immune during startup grace."""
    hm = HeartbeatManager()
    t_start = 1000.0
    hm.register_source("worker.new", expected_interval_seconds=10.0, timestamp_monotonic=t_start)

    # Before fail threshold during is_starting -> PASS (startup grace)
    probes_starting = hm.evaluate_all(now_monotonic=t_start + 15.0, is_starting=True)
    assert len(probes_starting) == 1
    assert probes_starting[0].status == CheckStatus.PASS


def test_heartbeat_shutdown_immunity() -> None:
    """Verify heartbeat lapses are suppressed when system is stopping."""
    hm = HeartbeatManager()
    t_base = 1000.0
    hm.register_source("worker.stopping", expected_interval_seconds=5.0, timestamp_monotonic=t_base)
    hm.record_heartbeat("worker.stopping", timestamp_monotonic=t_base)

    # Huge elapsed time (100s), but is_stopping is True -> PASS (suppressed)
    probes_stop = hm.evaluate_all(now_monotonic=t_base + 100.0, is_stopping=True)
    assert len(probes_stop) == 1
    assert probes_stop[0].status == CheckStatus.PASS


def test_heartbeat_restart_reset() -> None:
    """Verify reset_source reinitializes timer on subsystem restart."""
    hm = HeartbeatManager()
    t0 = 1000.0
    t_later = 2000.0
    hm.register_source("worker.restartable", expected_interval_seconds=10.0, timestamp_monotonic=t0)
    hm.record_heartbeat("worker.restartable", timestamp_monotonic=t0)

    # Subsystem restarts at t_later
    assert hm.reset_source("worker.restartable", timestamp_monotonic=t_later) is True
    assert hm.reset_source("unknown", timestamp_monotonic=t_later) is False

    # Check age at t_later + 2s -> PASS
    probes = hm.evaluate_all(now_monotonic=t_later + 2.0)
    assert probes[0].status == CheckStatus.PASS
