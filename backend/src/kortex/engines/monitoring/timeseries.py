"""Bounded circular time-series buffer for the KORTEX Monitoring Engine.

Maintains rolling in-memory deques (max 360 points at 10-second intervals = 60 minutes)
for real-time dashboard charting.
"""

from __future__ import annotations

import collections
import datetime
import threading

from kortex.engines.monitoring.constants import DEFAULT_RETENTION_POINTS, MAX_ACTIVE_SERIES
from kortex.engines.monitoring.models import TimeSeriesPoint, TimeSeriesQueryResponse
from kortex.engines.monitoring.registry import MetricRegistry


class TimeSeriesBuffer:
    """Thread-safe bounded in-memory time-series store."""

    def __init__(
        self,
        max_points: int = DEFAULT_RETENTION_POINTS,
        max_series: int = MAX_ACTIVE_SERIES,
    ) -> None:
        self._max_points = max_points
        self._max_series = max_series
        self._buffers: dict[str, collections.deque[TimeSeriesPoint]] = {}
        self._lock = threading.Lock()

    @property
    def series_count(self) -> int:
        with self._lock:
            return len(self._buffers)

    @property
    def points_total(self) -> int:
        with self._lock:
            return sum(len(b) for b in self._buffers.values())

    def append(self, series_key: str, point: TimeSeriesPoint) -> None:
        """Append a time-series point to the designated series ring buffer."""
        with self._lock:
            buffer = self._buffers.get(series_key)
            if buffer is None:
                if len(self._buffers) >= self._max_series:
                    # Drop append if series limit reached to guarantee memory ceiling
                    return
                buffer = collections.deque(maxlen=self._max_points)
                self._buffers[series_key] = buffer

            buffer.append(point)

    def query(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
        duration_seconds: int = 3600,
    ) -> TimeSeriesQueryResponse:
        """Query time-series points for a series within duration_seconds."""
        clean_labels = labels or {}
        key = MetricRegistry.series_key(metric_name, clean_labels)

        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=duration_seconds)

        with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                # Also allow exact name match if no labels provided
                matched_points: list[TimeSeriesPoint] = []
                for s_key, s_buf in self._buffers.items():
                    if s_key.startswith(f"{metric_name}{{") or s_key == metric_name:
                        matched_points.extend(s_buf)
                raw_points = matched_points
            else:
                raw_points = list(buf)

        # Filter by cutoff time
        filtered_points: list[TimeSeriesPoint] = []
        for p in raw_points:
            try:
                pt_time = datetime.datetime.fromisoformat(p.timestamp)
                if pt_time.tzinfo is None:
                    pt_time = pt_time.replace(tzinfo=datetime.UTC)
                if pt_time >= cutoff:
                    filtered_points.append(p)
            except Exception:
                # If timestamp parsing fails, include point conservatively
                filtered_points.append(p)

        # Sort points by timestamp
        filtered_points.sort(key=lambda p: p.timestamp)

        return TimeSeriesQueryResponse(
            metric_name=metric_name,
            labels=clean_labels,
            points=filtered_points,
            duration_seconds=duration_seconds,
        )

    def get_latest_point(self, series_key: str) -> TimeSeriesPoint | None:
        """Get the most recent point in a series."""
        with self._lock:
            buf = self._buffers.get(series_key)
            if buf and len(buf) > 0:
                return buf[-1]
            return None

    def reset(self) -> None:
        """Clear all buffers."""
        with self._lock:
            for b in self._buffers.values():
                b.clear()
            self._buffers.clear()
