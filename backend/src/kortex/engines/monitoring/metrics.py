"""Thread-safe metric primitives for the KORTEX Monitoring Engine.

Implements Counter, Gauge, Histogram (sliding-window reservoir with
linear-rank interpolation), and Timer.
"""

from __future__ import annotations

import collections
import contextlib
import datetime
import math
import threading
import time
from typing import TYPE_CHECKING

from kortex.engines.monitoring.constants import HISTOGRAM_RESERVOIR_SIZE
from kortex.engines.monitoring.models import (
    HistogramSnapshot,
    MetricType,
    MetricValue,
)

if TYPE_CHECKING:
    from collections.abc import Generator


def _utc_now_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.datetime.now(datetime.UTC).isoformat()


class Counter:
    """Thread-safe monotonically increasing numerical counter."""

    def __init__(self, name: str, labels: dict[str, str] | None = None) -> None:
        self._name = name
        self._labels = dict(labels or {})
        self._value: float = 0.0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def metric_type(self) -> MetricType:
        return MetricType.COUNTER

    @property
    def labels(self) -> dict[str, str]:
        return dict(self._labels)

    @property
    def value(self) -> float:
        with self._lock:
            return self._value

    def inc(self, amount: float = 1.0) -> None:
        """Increment counter by non-negative finite amount."""
        if not math.isfinite(amount):
            raise ValueError(f"Counter increment must be finite, got {amount}")
        if amount < 0.0:
            raise ValueError(f"Counter increment must be non-negative, got {amount}")

        with self._lock:
            self._value += amount

    def reset(self) -> None:
        """Reset counter to 0."""
        with self._lock:
            self._value = 0.0

    def snapshot(self, timestamp: str | None = None) -> MetricValue:
        """Return immutable point-in-time snapshot."""
        with self._lock:
            val = self._value
        return MetricValue(
            name=self._name,
            type=MetricType.COUNTER,
            labels=self.labels,
            value=val,
            timestamp=timestamp or _utc_now_iso(),
        )


class Gauge:
    """Thread-safe gauge representing an instantaneous numeric value."""

    def __init__(self, name: str, labels: dict[str, str] | None = None) -> None:
        self._name = name
        self._labels = dict(labels or {})
        self._value: float = 0.0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def metric_type(self) -> MetricType:
        return MetricType.GAUGE

    @property
    def labels(self) -> dict[str, str]:
        return dict(self._labels)

    @property
    def value(self) -> float:
        with self._lock:
            return self._value

    def set(self, value: float) -> None:
        """Set gauge to finite numeric value."""
        if not math.isfinite(value):
            raise ValueError(f"Gauge value must be finite, got {value}")

        with self._lock:
            self._value = float(value)

    def inc(self, amount: float = 1.0) -> None:
        """Increment gauge value."""
        if not math.isfinite(amount):
            raise ValueError(f"Gauge increment must be finite, got {amount}")

        with self._lock:
            self._value += float(amount)

    def dec(self, amount: float = 1.0) -> None:
        """Decrement gauge value."""
        if not math.isfinite(amount):
            raise ValueError(f"Gauge decrement must be finite, got {amount}")

        with self._lock:
            self._value -= float(amount)

    def reset(self) -> None:
        """Reset gauge to 0."""
        with self._lock:
            self._value = 0.0

    def snapshot(self, timestamp: str | None = None) -> MetricValue:
        """Return immutable point-in-time snapshot."""
        with self._lock:
            val = self._value
        return MetricValue(
            name=self._name,
            type=MetricType.GAUGE,
            labels=self.labels,
            value=val,
            timestamp=timestamp or _utc_now_iso(),
        )


class Histogram:
    """Thread-safe sliding-window reservoir histogram.

    Calculates approximate statistical percentiles (p50, p90, p95, p99)
    using linear-rank interpolation over a bounded reservoir of up to 1000
    observations. Sorting and percentile computation occur outside the write
    lock to maintain high concurrency.
    """

    def __init__(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        max_samples: int = HISTOGRAM_RESERVOIR_SIZE,
    ) -> None:
        self._name = name
        self._labels = dict(labels or {})
        self._max_samples = max_samples
        self._reservoir: collections.deque[float] = collections.deque(maxlen=max_samples)
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def metric_type(self) -> MetricType:
        return MetricType.HISTOGRAM

    @property
    def labels(self) -> dict[str, str]:
        return dict(self._labels)

    def record(self, value: float) -> None:
        """Record an observation in the sliding-window reservoir."""
        if not math.isfinite(value):
            raise ValueError(f"Histogram sample must be finite, got {value}")

        with self._lock:
            self._reservoir.append(float(value))

    def reset(self) -> None:
        """Clear all sample observations."""
        with self._lock:
            self._reservoir.clear()

    def get_snapshot(self) -> HistogramSnapshot:
        """Calculate and return statistical summary over retained reservoir."""
        # 1. Obtain stable snapshot under write lock
        with self._lock:
            samples = list(self._reservoir)

        n = len(samples)
        if n == 0:
            return HistogramSnapshot(
                count=0,
                sum=0.0,
                min=None,
                max=None,
                avg=0.0,
                p50=None,
                p90=None,
                p95=None,
                p99=None,
            )

        # 2. Sort and calculate percentiles outside the write lock
        sorted_samples = sorted(samples)
        total = sum(sorted_samples)
        s_min = sorted_samples[0]
        s_max = sorted_samples[-1]
        avg = total / n

        if n == 1:
            return HistogramSnapshot(
                count=1,
                sum=total,
                min=s_min,
                max=s_max,
                avg=avg,
                p50=s_min,
                p90=s_min,
                p95=s_min,
                p99=s_min,
            )

        def _linear_rank_percentile(p: float) -> float:
            rank = (p / 100.0) * (n - 1)
            k = math.floor(rank)
            d = rank - k
            if k + 1 < n:
                return sorted_samples[k] + d * (sorted_samples[k + 1] - sorted_samples[k])
            return sorted_samples[k]

        return HistogramSnapshot(
            count=n,
            sum=total,
            min=s_min,
            max=s_max,
            avg=round(avg, 4),
            p50=round(_linear_rank_percentile(50.0), 4),
            p90=round(_linear_rank_percentile(90.0), 4),
            p95=round(_linear_rank_percentile(95.0), 4),
            p99=round(_linear_rank_percentile(99.0), 4),
        )

    def snapshot(self, timestamp: str | None = None) -> MetricValue:
        """Return immutable point-in-time snapshot."""
        return MetricValue(
            name=self._name,
            type=MetricType.HISTOGRAM,
            labels=self.labels,
            value=None,
            histogram=self.get_snapshot(),
            timestamp=timestamp or _utc_now_iso(),
        )


class Timer:
    """Execution duration timer backed by an underlying Histogram."""

    def __init__(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        max_samples: int = HISTOGRAM_RESERVOIR_SIZE,
    ) -> None:
        self._name = name
        self._labels = dict(labels or {})
        self._histogram = Histogram(name=name, labels=labels, max_samples=max_samples)

    @property
    def name(self) -> str:
        return self._name

    @property
    def metric_type(self) -> MetricType:
        return MetricType.TIMER

    @property
    def labels(self) -> dict[str, str]:
        return dict(self._labels)

    def record(self, duration_ms: float) -> None:
        """Record duration sample in milliseconds."""
        self._histogram.record(duration_ms)

    def reset(self) -> None:
        """Reset timer observations."""
        self._histogram.reset()

    @contextlib.contextmanager
    def time(self) -> Generator[None, None, None]:
        """Context manager measuring execution block in milliseconds."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.record(elapsed_ms)

    def snapshot(self, timestamp: str | None = None) -> MetricValue:
        """Return immutable point-in-time snapshot."""
        return MetricValue(
            name=self._name,
            type=MetricType.TIMER,
            labels=self.labels,
            value=None,
            histogram=self._histogram.get_snapshot(),
            timestamp=timestamp or _utc_now_iso(),
        )
