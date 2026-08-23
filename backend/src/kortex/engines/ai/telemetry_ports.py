"""Tier 3 External Observability Interface for KORTEX AI Orchestration Engine.

Governed by Milestone 9.5 architecture specification:
docs/architecture/ai_engine_m9_production_runtime_spec.md

Defines the pluggable boundary for external metrics, counters, and histograms
without importing third-party monitoring SDKs (e.g., Prometheus, OpenTelemetry).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class MetricRecord:
    """Immutable recorded gauge metric value with metadata tags."""

    name: str
    value: float
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HistogramRecord:
    """Immutable recorded histogram sample value with metadata tags."""

    name: str
    value: float
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CounterRecord:
    """Immutable recorded monotonic counter increment with metadata tags."""

    name: str
    value: int
    tags: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ITelemetryExporter(Protocol):
    """Protocol boundary for external telemetry and observability export."""

    def record_metric(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a point-in-time numerical gauge metric."""
        ...

    def record_histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a histogram sample (e.g. latency in milliseconds)."""
        ...

    def record_counter(self, name: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        """Increment a monotonic counter metric."""
        ...


class InMemoryTelemetryExporter(ITelemetryExporter):
    """Thread-safe reference in-memory fake for testing telemetry export boundaries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: list[MetricRecord] = []
        self._histograms: list[HistogramRecord] = []
        self._counters: list[CounterRecord] = []

    @property
    def metrics(self) -> list[MetricRecord]:
        """Snapshot copy of recorded gauge metrics."""
        with self._lock:
            return list(self._metrics)

    @property
    def histograms(self) -> list[HistogramRecord]:
        """Snapshot copy of recorded histograms."""
        with self._lock:
            return list(self._histograms)

    @property
    def counters(self) -> list[CounterRecord]:
        """Snapshot copy of recorded counters."""
        with self._lock:
            return list(self._counters)

    def record_metric(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record gauge metric safely into memory."""
        with self._lock:
            self._metrics.append(MetricRecord(name=name, value=value, tags=dict(tags or {})))

    def record_histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record histogram observation safely into memory."""
        with self._lock:
            self._histograms.append(HistogramRecord(name=name, value=value, tags=dict(tags or {})))

    def record_counter(self, name: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        """Record counter increment safely into memory."""
        with self._lock:
            self._counters.append(CounterRecord(name=name, value=value, tags=dict(tags or {})))

    def get_counter_value(self, name: str) -> int:
        """Sum total of counter increments recorded for a given metric name."""
        with self._lock:
            return sum(c.value for c in self._counters if c.name == name)

    def clear(self) -> None:
        """Reset all recorded telemetry."""
        with self._lock:
            self._metrics.clear()
            self._histograms.clear()
            self._counters.clear()


__all__ = [
    "CounterRecord",
    "HistogramRecord",
    "ITelemetryExporter",
    "InMemoryTelemetryExporter",
    "MetricRecord",
]
