"""Unit tests for KORTEX Monitoring Engine metric primitives.

Tests Counter, Gauge, Histogram (with linear rank interpolation and sliding-window
reservoir semantics), and Timer.
"""

from __future__ import annotations

import concurrent.futures
import math
import time

import pytest

from kortex.engines.monitoring.metrics import Counter, Gauge, Histogram, Timer
from kortex.engines.monitoring.models import MetricType


def test_counter_monotonic_and_validation() -> None:
    """Verify Counter increments, rejects negative/non-finite amounts, and snapshots."""
    c = Counter("test.events_total", labels={"subsystem": "test"})
    assert c.name == "test.events_total"
    assert c.metric_type == MetricType.COUNTER
    assert c.labels == {"subsystem": "test"}
    assert c.value == 0.0

    c.inc(5.0)
    assert c.value == 5.0

    c.inc()
    assert c.value == 6.0

    with pytest.raises(ValueError, match="non-negative"):
        c.inc(-1.0)

    with pytest.raises(ValueError, match="finite"):
        c.inc(float("nan"))

    with pytest.raises(ValueError, match="finite"):
        c.inc(float("inf"))

    snap = c.snapshot()
    assert snap.name == "test.events_total"
    assert snap.value == 6.0
    assert snap.labels == {"subsystem": "test"}

    c.reset()
    assert c.value == 0.0


def test_gauge_operations_and_validation() -> None:
    """Verify Gauge set, inc, dec, non-finite rejections, and snapshots."""
    g = Gauge("test.memory_bytes", labels={"subsystem": "test"})
    assert g.name == "test.memory_bytes"
    assert g.metric_type == MetricType.GAUGE
    assert g.value == 0.0

    g.set(1024.0)
    assert g.value == 1024.0

    g.inc(512.0)
    assert g.value == 1536.0

    g.dec(256.0)
    assert g.value == 1280.0

    with pytest.raises(ValueError, match="finite"):
        g.set(float("nan"))

    with pytest.raises(ValueError, match="finite"):
        g.set(float("inf"))

    with pytest.raises(ValueError, match="finite"):
        g.inc(float("-inf"))

    snap = g.snapshot()
    assert snap.value == 1280.0

    g.reset()
    assert g.value == 0.0


def test_histogram_reservoir_and_edge_cases() -> None:
    """Verify Histogram empty, single sample, small sample, duplicate, and reservoir bounds."""
    h = Histogram("test.latency_ms", max_samples=100)

    # 1. Zero observations
    snap0 = h.get_snapshot()
    assert snap0.count == 0
    assert snap0.sum == 0.0
    assert snap0.min is None
    assert snap0.max is None
    assert snap0.avg == 0.0
    assert snap0.p50 is None
    assert snap0.p90 is None
    assert snap0.p95 is None
    assert snap0.p99 is None

    # 2. Single observation
    h.record(42.0)
    snap1 = h.get_snapshot()
    assert snap1.count == 1
    assert snap1.sum == 42.0
    assert snap1.min == 42.0
    assert snap1.max == 42.0
    assert snap1.avg == 42.0
    assert snap1.p50 == 42.0
    assert snap1.p90 == 42.0
    assert snap1.p95 == 42.0
    assert snap1.p99 == 42.0

    # 3. Non-finite values rejected
    with pytest.raises(ValueError, match="finite"):
        h.record(float("nan"))

    with pytest.raises(ValueError, match="finite"):
        h.record(float("inf"))

    # 4. Reservoir sliding window bound
    h.reset()
    for i in range(150):
        h.record(float(i))

    snap_bound = h.get_snapshot()
    # Retains only the last 100 samples (50..149)
    assert snap_bound.count == 100
    assert snap_bound.min == 50.0
    assert snap_bound.max == 149.0


def test_histogram_linear_rank_percentile_precision() -> None:
    """Verify linear rank interpolation on a 100-sample distribution (1 to 100)."""
    h = Histogram("test.distribution_ms", max_samples=1000)
    for v in range(1, 101):
        h.record(float(v))

    snap = h.get_snapshot()
    assert snap.count == 100
    assert snap.min == 1.0
    assert snap.max == 100.0
    assert math.isclose(snap.avg, 50.5, rel_tol=1e-3)
    # p50: rank = 0.5 * 99 = 49.5 -> s[49]=50, s[50]=51 -> 50.5
    assert math.isclose(snap.p50 or 0.0, 50.5, abs_tol=0.1)
    # p90: rank = 0.9 * 99 = 89.1 -> s[89]=90, s[90]=91 -> 90.1
    assert math.isclose(snap.p90 or 0.0, 90.1, abs_tol=0.1)
    # p95: rank = 0.95 * 99 = 94.05 -> s[94]=95, s[95]=96 -> 95.05
    assert math.isclose(snap.p95 or 0.0, 95.05, abs_tol=0.1)
    # p99: rank = 0.99 * 99 = 98.01 -> s[98]=99, s[99]=100 -> 99.01
    assert math.isclose(snap.p99 or 0.0, 99.01, abs_tol=0.1)


def test_histogram_duplicate_values() -> None:
    """Verify histogram with identical samples."""
    h = Histogram("test.duplicates", max_samples=100)
    for _ in range(50):
        h.record(10.0)

    snap = h.get_snapshot()
    assert snap.count == 50
    assert snap.min == 10.0
    assert snap.max == 10.0
    assert snap.avg == 10.0
    assert snap.p50 == 10.0
    assert snap.p90 == 10.0
    assert snap.p95 == 10.0
    assert snap.p99 == 10.0


def test_histogram_concurrent_writes() -> None:
    """Verify thread-safe concurrent recording into a Histogram."""
    h = Histogram("test.concurrent", max_samples=1000)

    def _worker(start: int) -> None:
        for i in range(100):
            h.record(float(start + i))

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_worker, i * 100) for i in range(5)]
        concurrent.futures.wait(futures)

    snap = h.get_snapshot()
    assert snap.count == 500
    assert snap.min == 0.0
    assert snap.max == 499.0


def test_timer_context_manager() -> None:
    """Verify Timer measures elapsed execution duration in milliseconds."""
    t = Timer("test.op_duration_ms")
    assert t.name == "test.op_duration_ms"
    assert t.metric_type == MetricType.TIMER

    with t.time():
        time.sleep(0.01)  # ~10ms

    snap = t.snapshot()
    assert snap.histogram is not None
    assert snap.histogram.count == 1
    assert snap.histogram.min is not None and snap.histogram.min >= 8.0  # Allow slight timing tolerance
