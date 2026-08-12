"""Unit tests for Connector Engine Diagnostics Metrics State and Recording API.

Tests strictly verify Sub-Milestone 10.5.1 metrics state accumulation, recording API,
bounded profile cardinality, latency aggregation, security sanitization, and
IEngineDiagnostics contract output boundaries.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from kortex.engines.connector.diagnostics import (
    ALLOWED_ERROR_CATEGORIES,
    CANONICAL_CAPABILITIES,
    ConnectorDiagnostics,
)
from kortex.engines.connector.drivers.http_driver import HttpRestConnectorDriver
from kortex.engines.connector.registry import ConnectorDriverRegistry


@pytest.fixture
def registry() -> ConnectorDriverRegistry:
    reg = ConnectorDriverRegistry()
    driver = HttpRestConnectorDriver()
    reg.register_driver(driver)
    return reg


@pytest.fixture
def diagnostics(registry: ConnectorDriverRegistry) -> ConnectorDiagnostics:
    return ConnectorDiagnostics(registry=registry)


def test_initial_zero_state(diagnostics: ConnectorDiagnostics) -> None:
    """1. Verify initial zero state for metrics."""
    m = diagnostics.metrics()
    assert m["total_executions"] == 0
    assert m["successful_executions"] == 0
    assert m["failed_executions"] == 0
    assert m["success_rate_percentage"] == 100.0
    assert m["total_latency_ms"] == 0.0
    assert m["average_latency_ms"] == 0.0
    assert m["min_latency_ms"] is None
    assert m["max_latency_ms"] is None
    assert m["retry_count"] == 0
    assert m["rate_limit_rejections"] == 0
    assert m["authentication_failures"] == 0
    assert m["driver_failures"] == 0
    assert m["cancellation_count"] == 0
    assert m["per_driver_executions"] == {}
    assert m["per_action_type_executions"] == {}
    assert m["per_profile_executions"] == {}
    assert m["http_status_codes"] == {}
    assert m["error_categories"] == {
        "rate_limit": 0,
        "authentication": 0,
        "driver_not_found": 0,
        "driver_execution": 0,
        "cancelled": 0,
        "http_4xx": 0,
        "http_5xx": 0,
        "unknown_error": 0,
    }


def test_single_success_execution(diagnostics: ConnectorDiagnostics) -> None:
    """2. Verify single successful execution recording."""
    diagnostics.record_execution(
        is_success=True,
        latency_ms=45.5,
        driver_id="connector-http-rest",
        action_type="FETCH",
        profile_id="prof-1",
    )
    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["successful_executions"] == 1
    assert m["failed_executions"] == 0
    assert m["success_rate_percentage"] == 100.0
    assert m["total_latency_ms"] == 45.5
    assert m["average_latency_ms"] == 45.5
    assert m["min_latency_ms"] == 45.5
    assert m["max_latency_ms"] == 45.5
    assert m["per_driver_executions"] == {"connector-http-rest": 1}
    assert m["per_action_type_executions"] == {"FETCH": 1}
    assert m["per_profile_executions"] == {"prof-1": 1}


def test_single_failure_execution(diagnostics: ConnectorDiagnostics) -> None:
    """3. Verify single failed execution recording (failed executions contribute latency)."""
    diagnostics.record_execution(
        is_success=False,
        latency_ms=120.0,
        driver_id="connector-http-rest",
        action_type="PUSH",
        profile_id="prof-2",
    )
    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["successful_executions"] == 0
    assert m["failed_executions"] == 1
    assert m["success_rate_percentage"] == 0.0
    assert m["total_latency_ms"] == 120.0
    assert m["average_latency_ms"] == 120.0
    assert m["min_latency_ms"] == 120.0
    assert m["max_latency_ms"] == 120.0
    assert m["per_driver_executions"] == {"connector-http-rest": 1}
    assert m["per_action_type_executions"] == {"PUSH": 1}
    assert m["per_profile_executions"] == {"prof-2": 1}


def test_multiple_execution_accumulation(diagnostics: ConnectorDiagnostics) -> None:
    """4. Verify accumulation across multiple executions."""
    diagnostics.record_execution(is_success=True, latency_ms=10.0, driver_id="d1", action_type="FETCH", profile_id="p1")
    diagnostics.record_execution(is_success=True, latency_ms=20.0, driver_id="d1", action_type="FETCH", profile_id="p1")
    diagnostics.record_execution(is_success=False, latency_ms=30.0, driver_id="d2", action_type="SEND", profile_id="p2")

    m = diagnostics.metrics()
    assert m["total_executions"] == 3
    assert m["successful_executions"] == 2
    assert m["failed_executions"] == 1
    assert m["success_rate_percentage"] == 66.67
    assert m["per_driver_executions"] == {"d1": 2, "d2": 1}
    assert m["per_action_type_executions"] == {"FETCH": 2, "SEND": 1}
    assert m["per_profile_executions"] == {"p1": 2, "p2": 1}


def test_latency_sum_and_range(diagnostics: ConnectorDiagnostics) -> None:
    """5. Verify latency sum, min, and max tracking."""
    diagnostics.record_execution(is_success=True, latency_ms=15.0)
    diagnostics.record_execution(is_success=True, latency_ms=5.0)
    diagnostics.record_execution(is_success=False, latency_ms=100.0)

    m = diagnostics.metrics()
    assert m["total_latency_ms"] == 120.0
    assert m["min_latency_ms"] == 5.0
    assert m["max_latency_ms"] == 100.0


def test_average_latency_calculation(diagnostics: ConnectorDiagnostics) -> None:
    """6. Verify average latency calculation accuracy."""
    diagnostics.record_execution(is_success=True, latency_ms=10.0)
    diagnostics.record_execution(is_success=True, latency_ms=20.0)

    m = diagnostics.metrics()
    assert m["average_latency_ms"] == 15.0


def test_min_max_latency_extremes(diagnostics: ConnectorDiagnostics) -> None:
    """7. Verify min and max latency with edge case negative value clamping."""
    diagnostics.record_execution(is_success=True, latency_ms=-5.0)  # Clamped to 0.0
    diagnostics.record_execution(is_success=True, latency_ms=50.0)

    m = diagnostics.metrics()
    assert m["min_latency_ms"] == 0.0
    assert m["max_latency_ms"] == 50.0


def test_per_driver_counts(diagnostics: ConnectorDiagnostics) -> None:
    """8. Verify per-driver execution breakdown counting."""
    diagnostics.record_execution(is_success=True, latency_ms=1.0, driver_id="driver-a")
    diagnostics.record_execution(is_success=True, latency_ms=1.0, driver_id="driver-a")
    diagnostics.record_execution(is_success=True, latency_ms=1.0, driver_id="driver-b")

    m = diagnostics.metrics()
    assert m["per_driver_executions"] == {"driver-a": 2, "driver-b": 1}


def test_per_action_counts(diagnostics: ConnectorDiagnostics) -> None:
    """9. Verify per-action-type execution breakdown counting."""
    diagnostics.record_execution(is_success=True, latency_ms=1.0, action_type="FETCH")
    diagnostics.record_execution(is_success=True, latency_ms=1.0, action_type="fetch")  # Normalized to uppercase
    diagnostics.record_execution(is_success=True, latency_ms=1.0, action_type="PUSH")

    m = diagnostics.metrics()
    assert m["per_action_type_executions"] == {"FETCH": 2, "PUSH": 1}


def test_bounded_profile_cardinality_and_existing_keys(diagnostics: ConnectorDiagnostics) -> None:
    """10. Verify bounded profile cardinality (max 1000 distinct profile keys)."""
    for i in range(1000):
        diagnostics.record_execution(is_success=True, latency_ms=1.0, profile_id=f"prof-{i}")

    m = diagnostics.metrics()
    assert len(m["per_profile_executions"]) == 1000
    assert "__other__" not in m["per_profile_executions"]

    # Increment an EXISTING profile after 1000 cap reached
    diagnostics.record_execution(is_success=True, latency_ms=1.0, profile_id="prof-0")
    m = diagnostics.metrics()
    assert m["per_profile_executions"]["prof-0"] == 2
    assert len(m["per_profile_executions"]) == 1000


def test_profile_cardinality_overflow_bucket(diagnostics: ConnectorDiagnostics) -> None:
    """11. Verify __other__ overflow bucket for new profiles after 1000 cap."""
    for i in range(1000):
        diagnostics.record_execution(is_success=True, latency_ms=1.0, profile_id=f"p-{i}")

    # Add 5 NEW profiles after cap
    for i in range(1000, 1005):
        diagnostics.record_execution(is_success=True, latency_ms=1.0, profile_id=f"p-{i}")

    m = diagnostics.metrics()
    # 1000 unique + 1 "__other__" = 1001 entries max
    assert len(m["per_profile_executions"]) == 1001
    assert m["per_profile_executions"]["__other__"] == 5


def test_retry_counter(diagnostics: ConnectorDiagnostics) -> None:
    """12. Verify retry counter API."""
    diagnostics.record_retry(1)
    diagnostics.record_retry(2)
    diagnostics.record_retry(0)  # Ignored
    diagnostics.record_retry(-1)  # Ignored

    m = diagnostics.metrics()
    assert m["retry_count"] == 3


def test_rate_limit_rejection_counter(diagnostics: ConnectorDiagnostics) -> None:
    """13. Verify rate-limit rejection counter API."""
    diagnostics.record_rate_limit_rejection()
    diagnostics.record_rate_limit_rejection()

    m = diagnostics.metrics()
    assert m["rate_limit_rejections"] == 2


def test_authentication_failure_counter(diagnostics: ConnectorDiagnostics) -> None:
    """14. Verify authentication failure counter API."""
    diagnostics.record_authentication_failure()

    m = diagnostics.metrics()
    assert m["authentication_failures"] == 1


def test_driver_failure_counter(diagnostics: ConnectorDiagnostics) -> None:
    """15. Verify driver failure counter API."""
    diagnostics.record_driver_failure()

    m = diagnostics.metrics()
    assert m["driver_failures"] == 1


def test_cancellation_counter(diagnostics: ConnectorDiagnostics) -> None:
    """16. Verify cancellation counter API."""
    diagnostics.record_cancellation()

    m = diagnostics.metrics()
    assert m["cancellation_count"] == 1


def test_http_status_counter(diagnostics: ConnectorDiagnostics) -> None:
    """17. Verify HTTP status counter API including type safety."""
    diagnostics.record_http_status(200)
    diagnostics.record_http_status(200)
    diagnostics.record_http_status(404)
    diagnostics.record_http_status("500")  # String numeric convertable to int
    diagnostics.record_http_status("invalid")  # Non-numeric string ignored
    diagnostics.record_http_status(True)  # Boolean ignored
    diagnostics.record_http_status(False)  # Boolean ignored
    diagnostics.record_http_status(999)  # Out of range HTTP code ignored

    m = diagnostics.metrics()
    assert m["http_status_codes"] == {200: 2, 404: 1, 500: 1}


def test_error_categories(diagnostics: ConnectorDiagnostics) -> None:
    """18. Verify bounded error categories recording."""
    diagnostics.record_error_category("rate_limit")
    diagnostics.record_error_category("http_5xx")
    diagnostics.record_error_category("custom_unrecognized_error")  # Falls back to unknown_error

    m = diagnostics.metrics()
    assert m["error_categories"]["rate_limit"] == 1
    assert m["error_categories"]["http_5xx"] == 1
    assert m["error_categories"]["unknown_error"] == 1


def test_zero_execution_metrics_formatting(diagnostics: ConnectorDiagnostics) -> None:
    """19. Verify metrics formatting when zero executions have occurred."""
    m = diagnostics.metrics()
    assert m["total_executions"] == 0
    assert m["average_latency_ms"] == 0.0
    assert m["min_latency_ms"] is None
    assert m["max_latency_ms"] is None
    assert m["success_rate_percentage"] == 100.0


def test_security_sanitization_in_metrics_and_diagnostics(diagnostics: ConnectorDiagnostics) -> None:
    """20. Verify zero credentials, tokens, secret handles, or raw headers exist in output."""
    diagnostics.record_execution(
        is_success=True,
        latency_ms=10.0,
        driver_id="connector-http-rest",
        action_type="FETCH",
        profile_id="prof-1",
    )
    diagnostics.record_error_category("authentication")

    m_dump = json.dumps(diagnostics.metrics())
    d_dump = json.dumps(diagnostics.diagnostics())
    h_dump = json.dumps(diagnostics.health())

    for dump in (m_dump, d_dump, h_dump):
        assert "secret" not in dump.lower()
        assert "token" not in dump.lower()
        assert "authorization" not in dump.lower()
        assert "password" not in dump.lower()


def test_health_metrics_diagnostics_output_boundaries(diagnostics: ConnectorDiagnostics) -> None:
    """21. Verify distinct output contracts for health(), metrics(), and diagnostics()."""
    diagnostics.record_execution(is_success=True, latency_ms=10.0)

    # 1. health() contract: Lightweight, contains overall status, components, and summary
    h = diagnostics.health()
    assert h["status"] == "healthy"
    assert "components" in h
    assert "summary" in h
    assert h["summary"] == {"total_executions": 1, "successful_executions": 1, "failed_executions": 0}
    # health MUST NOT contain full breakdowns
    assert "per_driver_executions" not in h

    # 2. metrics() contract: Full breakdown
    m = diagnostics.metrics()
    assert "total_executions" in m
    assert "per_driver_executions" in m
    assert "error_categories" in m

    # 3. diagnostics() contract: Technical snapshot + embedded metrics
    d = diagnostics.diagnostics()
    assert d["engine_version"] == "1.0.0"
    assert d["status"] == "RUNNING"
    assert "capabilities" in d
    assert "metrics" in d
    assert d["metrics"]["total_executions"] == 1
    assert len(d["registered_drivers"]) == 1
    assert d["registered_drivers"][0]["driver_id"] == "connector-http-rest"


def test_no_stage_specific_method_alters_execution_outcome_counters(diagnostics: ConnectorDiagnostics) -> None:
    """22. Verify that stage-specific methods DO NOT increment top-level outcome counters."""
    diagnostics.record_retry(3)
    diagnostics.record_rate_limit_rejection()
    diagnostics.record_authentication_failure()
    diagnostics.record_driver_failure()
    diagnostics.record_cancellation()
    diagnostics.record_http_status(500)
    diagnostics.record_error_category("http_5xx")

    m = diagnostics.metrics()
    # Top-level execution counters MUST remain 0 until record_execution() is explicitly called!
    assert m["total_executions"] == 0
    assert m["successful_executions"] == 0
    assert m["failed_executions"] == 0
    assert m["total_latency_ms"] == 0.0

    # Fact counters updated correctly
    assert m["retry_count"] == 3
    assert m["rate_limit_rejections"] == 1
    assert m["authentication_failures"] == 1
    assert m["driver_failures"] == 1
    assert m["cancellation_count"] == 1
    assert m["http_status_codes"] == {500: 1}
    assert m["error_categories"]["http_5xx"] == 1


def test_health_component_branches_is_healthy_and_exceptions() -> None:
    """Verify health() status computation when components use is_healthy or raise exceptions."""
    # 1. Component with is_healthy attribute instead of check_health()
    mock_pm_attr = MagicMock(spec=["is_healthy"])
    mock_pm_attr.is_healthy = True
    mock_rl_attr = MagicMock(spec=["is_healthy"])
    mock_rl_attr.is_healthy = False

    mock_reg = MagicMock()
    mock_reg.list_drivers.return_value = []

    diag = ConnectorDiagnostics(registry=mock_reg, profile_manager=mock_pm_attr, rate_limiter=mock_rl_attr)
    h = diag.health()
    assert h["status"] == "degraded"
    assert h["components"]["profile_manager"]["status"] == "healthy"
    assert h["components"]["rate_limiter"]["status"] == "degraded"

    # 2. Component raising exception in health check
    mock_pm_err = MagicMock()
    mock_pm_err.check_health.side_effect = Exception("Profile manager error")
    mock_rl_err = MagicMock()
    mock_rl_err.check_health.side_effect = Exception("Rate limiter error")

    diag2 = ConnectorDiagnostics(registry=mock_reg, profile_manager=mock_pm_err, rate_limiter=mock_rl_err)
    h2 = diag2.health()
    assert h2["status"] == "degraded"
    assert h2["components"]["profile_manager"]["status"] == "degraded"
    assert h2["components"]["rate_limiter"]["status"] == "degraded"

    # 3. Registry raising exception during health()
    mock_reg_err = MagicMock()
    mock_reg_err.list_drivers.side_effect = Exception("Registry error")
    diag3 = ConnectorDiagnostics(registry=mock_reg_err)
    h3 = diag3.health()
    assert h3["status"] == "unhealthy"
    assert h3["components"]["registry"]["status"] == "unhealthy"


def test_diagnostics_exception_handling_in_driver_list() -> None:
    """Verify diagnostics() handles exception in registry.list_drivers gracefully."""
    mock_reg = MagicMock()
    mock_reg.list_drivers.side_effect = Exception("Registry error")
    diag = ConnectorDiagnostics(registry=mock_reg)
    d = diag.diagnostics()
    assert d["registered_driver_count"] == 0
    assert d["registered_drivers"] == []
    m = diag.metrics()
    assert m["registered_driver_count"] == 0
