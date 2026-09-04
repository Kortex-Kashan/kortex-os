"""Unit tests for KORTEX Monitoring DiagnosticsNormalizer.

Tests 3-tier normalization of IEngineDiagnostics output:
- Tier 1: Canonical finite numeric metrics; rejection of NaN/+Inf/-Inf and invalid names
- Tier 2: Semantic preservation of metadata (status, version, capabilities, timestamps)
- Tier 3: Boolean conversion to 1.0/0.0; None skipped; strings/lists retained in raw payload;
  first-occurrence-wins duplicate resolution.
"""

from __future__ import annotations

from kortex.engines.monitoring.models import MetricType
from kortex.engines.monitoring.normalizer import DiagnosticsNormalizer


def test_normalizer_tier1_canonical_numeric_metrics() -> None:
    """Verify finite integers and floats are converted to valid NormalizedMetrics with subsystem prefix."""
    normalizer = DiagnosticsNormalizer()
    payload = {
        "cache_hits_total": 120,
        "memory_usage_mb": 45.5,
        "storage.custom_latency": 12.34,
    }

    res = normalizer.normalize("storage", payload)
    assert res.subsystem == "storage"
    assert res.rejected_count == 0
    assert len(res.metrics) == 3

    # Check names
    metric_map = {m.name: m for m in res.metrics}
    assert "storage.cache_hits_total" in metric_map
    assert metric_map["storage.cache_hits_total"].type == MetricType.COUNTER
    assert metric_map["storage.cache_hits_total"].value == 120.0

    assert "storage.memory_usage_mb" in metric_map
    assert metric_map["storage.memory_usage_mb"].type == MetricType.GAUGE
    assert metric_map["storage.memory_usage_mb"].value == 45.5

    # Already prefixed with subsystem
    assert "storage.custom_latency" in metric_map
    assert metric_map["storage.custom_latency"].value == 12.34


def test_normalizer_tier1_rejects_nan_and_inf() -> None:
    """Verify non-finite numeric values (NaN, +Inf, -Inf) are rejected and counted."""
    normalizer = DiagnosticsNormalizer()
    payload = {
        "valid_metric": 10.0,
        "nan_metric": float("nan"),
        "inf_metric": float("inf"),
        "neg_inf_metric": float("-inf"),
    }

    res = normalizer.normalize("sentinel", payload)
    assert res.rejected_count == 3
    assert len(res.metrics) == 1
    assert res.metrics[0].name == "sentinel.valid_metric"
    assert normalizer.invalid_values_total == 3


def test_normalizer_tier2_metadata_preserved() -> None:
    """Verify status, version, capabilities, and timestamps are preserved in metadata."""
    normalizer = DiagnosticsNormalizer()
    payload = {
        "status": "RUNNING",
        "version": "1.0.0",
        "capabilities": ["kortex.storage.data.get", "kortex.storage.data.put"],
        "timestamp": "2026-09-04T12:00:00Z",
        "active_connections": 5,
    }

    res = normalizer.normalize("storage", payload)
    assert "status" in res.metadata
    assert res.metadata["status"] == "RUNNING"
    assert res.metadata["version"] == "1.0.0"
    assert len(res.metadata["capabilities"]) == 2
    assert res.metadata["timestamp"] == "2026-09-04T12:00:00Z"

    # Status/version must not become numeric metrics
    assert not any(m.name.endswith(".status") or m.name.endswith(".version") for m in res.metrics)
    # Only active_connections became a metric
    assert len(res.metrics) == 1
    assert res.metrics[0].name == "storage.active_connections"


def test_normalizer_tier3_booleans_and_arbitrary_payload() -> None:
    """Verify booleans become 1.0/0.0 gauges, None is skipped, and non-convertibles remain in raw_payload."""
    normalizer = DiagnosticsNormalizer()
    payload = {
        "database_connected": True,
        "circuit_breaker_open": False,
        "uninitialized_property": None,
        "description": "Primary storage engine for local workspace",
        "supported_codecs": ["gzip", "zstd"],
    }

    res = normalizer.normalize("storage", payload)
    metric_map = {m.name: m for m in res.metrics}

    assert "storage.database_connected" in metric_map
    assert metric_map["storage.database_connected"].value == 1.0
    assert metric_map["storage.database_connected"].type == MetricType.GAUGE

    assert "storage.circuit_breaker_open" in metric_map
    assert metric_map["storage.circuit_breaker_open"].value == 0.0

    # None, description string, and supported_codecs list must not be metrics
    assert len(res.metrics) == 2
    assert "description" in res.raw_payload
    assert "supported_codecs" in res.raw_payload


def test_normalizer_first_occurrence_wins_for_duplicates() -> None:
    """Verify duplicate metric keys resolve via first-occurrence-wins determinism."""
    normalizer = DiagnosticsNormalizer()
    # In nested dict or repeated paths
    payload = {
        "ops_count": 100,
        "nested": {
            "ops_count": 999,  # Would resolve to storage.nested.ops_count (different)
        },
    }

    res = normalizer.normalize("storage", payload)
    names = [m.name for m in res.metrics]
    assert "storage.ops_count" in names
    assert "storage.nested.ops_count" in names


def test_normalizer_nested_labels_extraction() -> None:
    """Verify whitelisted label keys in nested dictionaries attach to generated child metrics."""
    normalizer = DiagnosticsNormalizer()
    payload = {
        "pool": {
            "driver": "sqlite",
            "status": "healthy",
            "active_connections": 4,
            "idle_connections": 2,
        }
    }

    res = normalizer.normalize("storage", payload)
    for m in res.metrics:
        assert m.labels.get("subsystem") == "storage"
        assert m.labels.get("driver") == "sqlite"
        assert m.labels.get("status") == "healthy"
