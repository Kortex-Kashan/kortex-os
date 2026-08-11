"""Unit tests for Connector Engine diagnostics provider (Milestone 7).

Target: 100% test pass rate, 100% line coverage for diagnostics.py.
"""

from __future__ import annotations

import pytest

from kortex.engines.connector.diagnostics import (
    CANONICAL_CAPABILITIES,
    ConnectorDiagnostics,
)
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.interfaces import IEngineDiagnostics
from kortex.engines.connector.profiles import ConnectorProfileManager
from kortex.engines.connector.rate_limiter import TokenBucketRateLimiter
from kortex.engines.connector.registry import ConnectorDriverRegistry


def test_protocol_compliance() -> None:
    """Test that ConnectorDiagnostics satisfies IEngineDiagnostics protocol."""
    reg = ConnectorDriverRegistry()
    diag = ConnectorDiagnostics(registry=reg)
    assert isinstance(diag, IEngineDiagnostics)


def test_diagnostics_basic_properties() -> None:
    """Test status, version, and capabilities methods."""
    reg = ConnectorDriverRegistry()
    diag = ConnectorDiagnostics(registry=reg)

    assert diag.status() == "RUNNING"
    assert diag.version() == "1.0.0"

    caps = diag.capabilities()
    assert caps == CANONICAL_CAPABILITIES
    assert "kortex.connector.action.execute" in caps
    assert "kortex.connector.driver.register" in caps
    assert "kortex.connector.driver.list" in caps
    assert "kortex.connector.profile.get" in caps


def test_health_checks_operational() -> None:
    """Test health() reporting for healthy registry and optional dependencies."""
    reg = ConnectorDriverRegistry()
    driver = DummyConnectorDriver()
    reg.register_driver(driver)

    pm = ConnectorProfileManager()
    limiter = TokenBucketRateLimiter()

    diag = ConnectorDiagnostics(registry=reg, profile_manager=pm, rate_limiter=limiter)

    health_info = diag.health()
    assert health_info["status"] == "healthy"
    assert health_info["components"]["registry"]["status"] == "healthy"
    assert health_info["components"]["registry"]["registered_driver_count"] == 1
    assert health_info["components"]["profile_manager"]["status"] == "healthy"
    assert health_info["components"]["rate_limiter"]["status"] == "healthy"


def test_health_checks_without_optional_components() -> None:
    """Test health() reporting when optional components are None."""
    reg = ConnectorDriverRegistry()
    diag = ConnectorDiagnostics(registry=reg, profile_manager=None, rate_limiter=None)

    health_info = diag.health()
    assert health_info["status"] == "healthy"
    assert health_info["components"]["registry"]["status"] == "healthy"
    assert health_info["components"]["profile_manager"]["status"] == "not_configured"
    assert health_info["components"]["rate_limiter"]["status"] == "not_configured"


def test_health_checks_degraded_state() -> None:
    """Test health() returning 'degraded' when an optional component health check fails or raises exception."""
    reg = ConnectorDriverRegistry()

    class DegradedProfileManager:
        def check_health(self) -> bool:
            return False

    class ExceptionProfileManager:
        def check_health(self) -> bool:
            raise RuntimeError("Profile store unavailable")

    class ExceptionRateLimiter:
        def check_health(self) -> bool:
            raise RuntimeError("Rate limiter storage failure")

    # Test profile manager degraded return False
    diag1 = ConnectorDiagnostics(registry=reg, profile_manager=DegradedProfileManager())  # type: ignore[arg-type]
    health1 = diag1.health()
    assert health1["status"] == "degraded"
    assert health1["components"]["profile_manager"]["status"] == "degraded"

    # Test profile manager exception degraded
    diag_ex_pm = ConnectorDiagnostics(registry=reg, profile_manager=ExceptionProfileManager())  # type: ignore[arg-type]
    health_ex_pm = diag_ex_pm.health()
    assert health_ex_pm["status"] == "degraded"
    assert health_ex_pm["components"]["profile_manager"]["status"] == "degraded"

    # Test rate limiter exception degraded
    diag2 = ConnectorDiagnostics(registry=reg, rate_limiter=ExceptionRateLimiter())  # type: ignore[arg-type]
    health2 = diag2.health()
    assert health2["status"] == "degraded"
    assert health2["components"]["rate_limiter"]["status"] == "degraded"


def test_health_checks_unhealthy_registry() -> None:
    """Test health() returning 'unhealthy' when registry raises an exception."""
    class BrokenRegistry(ConnectorDriverRegistry):
        def list_drivers(self) -> list:
            raise RuntimeError("Database registry failure")

    diag = ConnectorDiagnostics(registry=BrokenRegistry())
    health_info = diag.health()
    assert health_info["status"] == "unhealthy"
    assert health_info["components"]["registry"]["status"] == "unhealthy"


def test_metrics_reporting_and_deferred_contract() -> None:
    """Test metrics() returns observable synchronous state metrics without fabrication."""
    reg = ConnectorDriverRegistry()
    reg.register_driver(DummyConnectorDriver())

    pm = ConnectorProfileManager()
    diag = ConnectorDiagnostics(registry=reg, profile_manager=pm)

    metrics_info = diag.metrics()
    assert metrics_info["registered_driver_count"] == 1
    assert metrics_info["profile_manager_configured"] is True
    assert metrics_info["rate_limiter_configured"] is False
    assert metrics_info["async_profile_metrics_deferred"] is True

    # Assert no fabricated metrics (no execution latency, throughput, error rates)
    assert "average_latency_ms" not in metrics_info
    assert "total_dispatches" not in metrics_info
    assert "error_rate" not in metrics_info
    assert "total_profile_count" not in metrics_info


def test_metrics_fallback_on_exception() -> None:
    """Test metrics() fallback when registry raises an exception."""
    class BrokenRegistry(ConnectorDriverRegistry):
        def list_drivers(self) -> list:
            raise RuntimeError("Registry access error")

    diag = ConnectorDiagnostics(registry=BrokenRegistry())
    metrics_info = diag.metrics()
    assert metrics_info["registered_driver_count"] == 0


def test_diagnostics_sanitized_primitives_and_privacy() -> None:
    """Test diagnostics() returns sanitized primitive data without sensitive leaks."""
    reg = ConnectorDriverRegistry()
    driver = DummyConnectorDriver()
    reg.register_driver(driver)

    diag = ConnectorDiagnostics(registry=reg)
    tech_diag = diag.diagnostics()

    assert tech_diag["engine_version"] == "1.0.0"
    assert tech_diag["status"] == "RUNNING"
    assert tech_diag["registered_driver_count"] == 1

    drivers = tech_diag["registered_drivers"]
    assert len(drivers) == 1
    drv = drivers[0]
    assert drv["driver_id"] == "connector-dummy"
    assert drv["display_name"] == "Reference Dummy Connector Driver"
    assert drv["version"] == "1.0.0"
    assert "SEND" in drv["supported_actions"]

    # Security privacy assertions: No secret handles, credentials, tokens, or raw objects
    diag_str = str(tech_diag)
    assert "secret_handle" not in diag_str
    assert "secret_token" not in diag_str
    assert "password" not in diag_str
    assert "api_key" not in diag_str
    assert "DummyConnectorDriver object" not in diag_str


def test_diagnostics_fallback_on_exception() -> None:
    """Test diagnostics() graceful handling when registry fails."""
    class BrokenRegistry(ConnectorDriverRegistry):
        def list_drivers(self) -> list:
            raise RuntimeError("Registry failure")

    diag = ConnectorDiagnostics(registry=BrokenRegistry())
    tech_diag = diag.diagnostics()

    assert tech_diag["registered_driver_count"] == 0
    assert tech_diag["registered_drivers"] == []
