"""Unit tests for KORTEX Monitoring TimeSeriesBuffer.

Tests bounded ring buffer retention, query time windows, label matching,
and series capacity enforcement.
"""

from __future__ import annotations

import datetime

from kortex.engines.monitoring.models import TimeSeriesPoint
from kortex.engines.monitoring.timeseries import TimeSeriesBuffer


def test_timeseries_buffer_bounded_capacity() -> None:
    """Verify time series buffer enforces max_points retention per series."""
    buf = TimeSeriesBuffer(max_points=5, max_series=10)
    now = datetime.datetime.now(datetime.UTC)

    for i in range(10):
        t = (now + datetime.timedelta(seconds=i)).isoformat()
        buf.append("test.metric", TimeSeriesPoint(timestamp=t, value=float(i)))

    # Only 5 most recent points retained
    res = buf.query("test.metric", duration_seconds=3600)
    assert len(res.points) == 5
    assert [p.value for p in res.points] == [5.0, 6.0, 7.0, 8.0, 9.0]


def test_timeseries_buffer_query_time_filtering() -> None:
    """Verify points older than duration_seconds are excluded from query results."""
    buf = TimeSeriesBuffer(max_points=100)
    now = datetime.datetime.now(datetime.UTC)

    # 1 point from 2 hours ago
    old_time = (now - datetime.timedelta(hours=2)).isoformat()
    buf.append("system.cpu", TimeSeriesPoint(timestamp=old_time, value=10.0))

    # 2 points within the last 10 minutes
    recent1 = (now - datetime.timedelta(minutes=5)).isoformat()
    recent2 = (now - datetime.timedelta(minutes=1)).isoformat()
    buf.append("system.cpu", TimeSeriesPoint(timestamp=recent1, value=25.0))
    buf.append("system.cpu", TimeSeriesPoint(timestamp=recent2, value=30.0))

    # Query last 1 hour (3600s)
    res = buf.query("system.cpu", duration_seconds=3600)
    assert len(res.points) == 2
    assert [p.value for p in res.points] == [25.0, 30.0]


def test_timeseries_buffer_max_series_ceiling() -> None:
    """Verify buffer stops creating new series buffers once max_series is reached."""
    buf = TimeSeriesBuffer(max_points=10, max_series=3)
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    buf.append("s1", TimeSeriesPoint(timestamp=now_iso, value=1.0))
    buf.append("s2", TimeSeriesPoint(timestamp=now_iso, value=2.0))
    buf.append("s3", TimeSeriesPoint(timestamp=now_iso, value=3.0))
    assert buf.series_count == 3

    # 4th series should be silently dropped to maintain bounded memory ceiling
    buf.append("s4", TimeSeriesPoint(timestamp=now_iso, value=4.0))
    assert buf.series_count == 3
    res = buf.query("s4")
    assert len(res.points) == 0


def test_timeseries_buffer_reset() -> None:
    """Verify reset clears all series and points."""
    buf = TimeSeriesBuffer(max_points=10)
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    buf.append("metric_a", TimeSeriesPoint(timestamp=now_iso, value=1.0))
    buf.append("metric_b", TimeSeriesPoint(timestamp=now_iso, value=2.0))
    assert buf.series_count == 2
    assert buf.points_total == 2

    buf.reset()
    assert buf.series_count == 0
    assert buf.points_total == 0
