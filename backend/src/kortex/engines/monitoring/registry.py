"""MetricRegistry managing registered metric primitives and enforcing cardinality limits.

Guarantees collision-safe series keys, strict label whitelisting, and hard
bounds on metric names and active series.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from kortex.engines.monitoring.constants import (
    ALLOWED_LABEL_KEYS,
    MAX_ACTIVE_SERIES,
    MAX_LABEL_VALUE_LENGTH,
    MAX_LABELS_PER_SERIES,
    MAX_METRIC_NAMES,
    METRIC_NAME_REGEX,
)
from kortex.engines.monitoring.metrics import Counter, Gauge, Histogram, Timer
from kortex.engines.monitoring.models import MetricType, MetricValue

logger = logging.getLogger("kortex.engines.monitoring.registry")


class MetricRegistry:
    """Registry container enforcing cardinality limits and metric validation."""

    def __init__(
        self,
        max_metric_names: int = MAX_METRIC_NAMES,
        max_active_series: int = MAX_ACTIVE_SERIES,
    ) -> None:
        self._max_metric_names = max_metric_names
        self._max_active_series = max_active_series

        self._metrics: dict[str, Any] = {}
        self._metric_names: set[str] = set()
        self._lock = threading.Lock()

        # Rejection tracking counters
        self.cardinality_rejections_total = 0
        self.name_rejections_total = 0

    @property
    def active_series_count(self) -> int:
        with self._lock:
            return len(self._metrics)

    @property
    def metric_names_count(self) -> int:
        with self._lock:
            return len(self._metric_names)

    def _validate_name(self, name: str) -> str:
        """Validate canonical metric name grammar."""
        if not METRIC_NAME_REGEX.match(name):
            self.name_rejections_total += 1
            raise ValueError(
                f"Metric name '{name}' violates naming grammar: must match '[a-z][a-z0-9_]*(\\.[a-z0-9_]+)*'"
            )
        return name

    def _validate_and_filter_labels(self, labels: dict[str, str] | None) -> dict[str, str]:
        """Validate label keys against whitelist and ensure value lengths <= 64."""
        if not labels:
            return {}

        if len(labels) > MAX_LABELS_PER_SERIES:
            self.cardinality_rejections_total += 1
            raise ValueError(f"Metric series exceeds maximum allowed labels ({len(labels)} > {MAX_LABELS_PER_SERIES})")

        clean_labels: dict[str, str] = {}
        for key, val in labels.items():
            if key not in ALLOWED_LABEL_KEYS:
                logger.debug("Omitted unwhitelisted label key '%s'", key)
                continue

            str_val = str(val).strip()
            if len(str_val) > MAX_LABEL_VALUE_LENGTH:
                self.cardinality_rejections_total += 1
                raise ValueError(
                    f"Label value for '{key}' exceeds maximum length {MAX_LABEL_VALUE_LENGTH}: length={len(str_val)}"
                )

            clean_labels[key] = str_val

        return clean_labels

    @staticmethod
    def series_key(name: str, labels: dict[str, str]) -> str:
        """Construct deterministic, collision-safe series key with sorted labels."""
        if not labels:
            return name
        sorted_items = sorted(labels.items())
        label_part = ",".join(f"{k}={v}" for k, v in sorted_items)
        return f"{name}{{{label_part}}}"

    def _get_or_create(
        self,
        name: str,
        metric_type: MetricType,
        labels: dict[str, str] | None,
        factory: Any,
    ) -> Any:
        clean_name = self._validate_name(name)
        clean_labels = self._validate_and_filter_labels(labels)
        key = self.series_key(clean_name, clean_labels)

        with self._lock:
            existing = self._metrics.get(key)
            if existing is not None:
                if existing.metric_type != metric_type:
                    raise TypeError(
                        f"Metric '{key}' already registered with type '{existing.metric_type}', "
                        f"cannot re-register as '{metric_type}'"
                    )
                return existing

            # Check cardinality caps before registering new series
            if len(self._metrics) >= self._max_active_series:
                self.cardinality_rejections_total += 1
                raise ValueError(
                    f"Maximum active metric series limit reached ({self._max_active_series}); rejected '{key}'"
                )

            if clean_name not in self._metric_names and len(self._metric_names) >= self._max_metric_names:
                self.cardinality_rejections_total += 1
                raise ValueError(
                    f"Maximum metric names limit reached ({self._max_metric_names}); rejected '{clean_name}'"
                )

            # Register
            metric = factory(clean_name, clean_labels)
            self._metrics[key] = metric
            self._metric_names.add(clean_name)
            return metric

    def counter(self, name: str, labels: dict[str, str] | None = None) -> Counter:
        return self._get_or_create(name, MetricType.COUNTER, labels, Counter)  # type: ignore[no-any-return]

    def gauge(self, name: str, labels: dict[str, str] | None = None) -> Gauge:
        return self._get_or_create(name, MetricType.GAUGE, labels, Gauge)  # type: ignore[no-any-return]

    def histogram(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        max_samples: int = 1000,
    ) -> Histogram:
        def _factory(n: str, lbls: dict[str, str]) -> Histogram:
            return Histogram(name=n, labels=lbls, max_samples=max_samples)

        return self._get_or_create(name, MetricType.HISTOGRAM, labels, _factory)  # type: ignore[no-any-return]

    def timer(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        max_samples: int = 1000,
    ) -> Timer:
        def _factory(n: str, lbls: dict[str, str]) -> Timer:
            return Timer(name=n, labels=lbls, max_samples=max_samples)

        return self._get_or_create(name, MetricType.TIMER, labels, _factory)  # type: ignore[no-any-return]

    def get_metric(self, key: str) -> Any | None:
        """Lookup an existing metric by series key."""
        with self._lock:
            return self._metrics.get(key)

    def get_all_metrics(self, subsystem: str | None = None) -> list[MetricValue]:
        """Return snapshots of all registered metrics, optionally filtered by subsystem."""
        with self._lock:
            metrics_list = list(self._metrics.values())

        results: list[MetricValue] = []
        for m in metrics_list:
            if subsystem is not None:
                sub = m.labels.get("subsystem")
                if sub != subsystem:
                    continue
            results.append(m.snapshot())

        # Sort alphabetically by name and series key for deterministic presentation
        results.sort(key=lambda mv: self.series_key(mv.name, mv.labels))
        return results

    def reset(self) -> None:
        """Reset all metric primitives and clear registry."""
        with self._lock:
            for m in self._metrics.values():
                m.reset()
            self._metrics.clear()
            self._metric_names.clear()
            self.cardinality_rejections_total = 0
            self.name_rejections_total = 0
