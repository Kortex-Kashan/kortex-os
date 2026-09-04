"""Unit tests for KORTEX Monitoring Engine MetricRegistry.

Tests cardinality limits, label whitelisting, overlong label values,
collision-safe series keys, deterministic label ordering, and grammar validation.
"""

from __future__ import annotations

import pytest

from kortex.engines.monitoring.constants import (
    MAX_LABEL_VALUE_LENGTH,
    MAX_LABELS_PER_SERIES,
)
from kortex.engines.monitoring.registry import MetricRegistry


def test_metric_name_grammar_validation() -> None:
    """Verify registry enforces lowercase dotted alphanumeric metric name grammar."""
    registry = MetricRegistry()

    # Valid names
    c1 = registry.counter("storage.read_bytes_total")
    assert c1.name == "storage.read_bytes_total"

    g1 = registry.gauge("system.memory.working_set_mb")
    assert g1.name == "system.memory.working_set_mb"

    # Invalid names
    with pytest.raises(ValueError, match="violates naming grammar"):
        registry.counter("Invalid-Name")

    with pytest.raises(ValueError, match="violates naming grammar"):
        registry.counter("123.numeric.start")

    with pytest.raises(ValueError, match="violates naming grammar"):
        registry.counter("UPPERCASE.METRIC")

    with pytest.raises(ValueError, match="violates naming grammar"):
        registry.counter("spaces not allowed")

    assert registry.name_rejections_total == 4


def test_label_whitelisting_and_length_bounds() -> None:
    """Verify label keys are strictly whitelisted and values <= 64 chars."""
    registry = MetricRegistry()

    # Whitelisted labels: subsystem, driver, status, error_type, action_type, severity, entity_type
    valid_labels = {"subsystem": "storage", "status": "active", "driver": "sqlite"}
    c = registry.counter("engine.requests_total", labels=valid_labels)
    assert c.labels == valid_labels

    # Unwhitelisted label keys are omitted
    mixed_labels = {"subsystem": "security", "unwhitelisted_custom_key": "val"}
    c2 = registry.counter("security.auth_total", labels=mixed_labels)
    assert c2.labels == {"subsystem": "security"}
    assert "unwhitelisted_custom_key" not in c2.labels

    # Overlong label value (> 64 characters) rejected
    too_long = "x" * (MAX_LABEL_VALUE_LENGTH + 1)
    with pytest.raises(ValueError, match="exceeds maximum length"):
        registry.counter("storage.operations_total", labels={"subsystem": too_long})

    assert registry.cardinality_rejections_total == 1

    # Too many labels (> 5) rejected
    too_many_labels = {f"k_{i}": f"v_{i}" for i in range(MAX_LABELS_PER_SERIES + 1)}
    with pytest.raises(ValueError, match="maximum allowed labels"):
        registry.counter("storage.ops_total", labels=too_many_labels)

    assert registry.cardinality_rejections_total == 2


def test_series_key_collision_safety_and_ordering() -> None:
    """Verify series keys are deterministic regardless of label insertion order."""
    k1 = MetricRegistry.series_key("test.metric", {"subsystem": "sentinel", "status": "healthy"})
    k2 = MetricRegistry.series_key("test.metric", {"status": "healthy", "subsystem": "sentinel"})
    assert k1 == k2
    assert k1 == "test.metric{status=healthy,subsystem=sentinel}"

    # Verify no labels
    k3 = MetricRegistry.series_key("test.metric", {})
    assert k3 == "test.metric"


def test_max_metric_names_cardinality_cap() -> None:
    """Verify registry rejects new metric names once MAX_METRIC_NAMES reached."""
    registry = MetricRegistry(max_metric_names=5, max_active_series=100)

    for i in range(5):
        registry.counter(f"test.metric_{i}")

    assert registry.metric_names_count == 5

    # Registering 6th distinct name must fail
    with pytest.raises(ValueError, match="Maximum metric names limit reached"):
        registry.counter("test.metric_overflow")

    assert registry.cardinality_rejections_total == 1

    # Re-registering existing name with new labels is allowed within active series limit
    c_existing = registry.counter("test.metric_0", labels={"subsystem": "core"})
    assert c_existing.name == "test.metric_0"


def test_max_active_series_cardinality_cap() -> None:
    """Verify registry rejects new series once MAX_ACTIVE_SERIES reached."""
    registry = MetricRegistry(max_metric_names=10, max_active_series=4)

    registry.counter("test.metric", labels={"status": "s1"})
    registry.counter("test.metric", labels={"status": "s2"})
    registry.counter("test.metric", labels={"status": "s3"})
    registry.counter("test.metric", labels={"status": "s4"})

    assert registry.active_series_count == 4

    # 5th series must fail
    with pytest.raises(ValueError, match="Maximum active metric series limit reached"):
        registry.counter("test.metric", labels={"status": "s5"})

    assert registry.cardinality_rejections_total == 1


def test_metric_type_mismatch_raises_type_error() -> None:
    """Verify re-registering existing series key with conflicting metric type raises TypeError."""
    registry = MetricRegistry()
    registry.counter("service.requests", labels={"subsystem": "api"})

    with pytest.raises(TypeError, match="already registered with type"):
        registry.gauge("service.requests", labels={"subsystem": "api"})


def test_get_all_metrics_filtering_and_determinism() -> None:
    """Verify get_all_metrics returns deterministic sorted snapshots and supports subsystem filtering."""
    registry = MetricRegistry()
    registry.counter("b.metric", labels={"subsystem": "sub_b"})
    registry.counter("a.metric", labels={"subsystem": "sub_a"})
    registry.gauge("c.metric", labels={"subsystem": "sub_a"})

    all_metrics = registry.get_all_metrics()
    assert len(all_metrics) == 3
    # Sorted deterministically
    assert all_metrics[0].name == "a.metric"
    assert all_metrics[1].name == "b.metric"
    assert all_metrics[2].name == "c.metric"

    # Filtered by subsystem
    sub_a_metrics = registry.get_all_metrics(subsystem="sub_a")
    assert len(sub_a_metrics) == 2
    assert all(m.labels.get("subsystem") == "sub_a" for m in sub_a_metrics)
