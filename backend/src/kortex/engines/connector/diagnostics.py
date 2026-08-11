"""Connector Engine Diagnostics implementation for KORTEX OS Connector Engine.

This module implements ConnectorDiagnostics, conforming strictly to the IEngineDiagnostics
protocol for runtime operational health, observable state metrics, technical snapshot,
and capability reporting.
"""

from __future__ import annotations

from typing import Any

from kortex.engines.connector.interfaces import (
    IConnectorDriverRegistry,
    IConnectorProfileManager,
    IEngineDiagnostics,
    IRateLimiter,
)

CANONICAL_CAPABILITIES: list[str] = [
    "kortex.connector.action.execute",
    "kortex.connector.driver.register",
    "kortex.connector.driver.list",
    "kortex.connector.profile.get",
]


class ConnectorDiagnostics(IEngineDiagnostics):
    """Standardized diagnostics provider for the Connector Engine."""

    def __init__(
        self,
        registry: IConnectorDriverRegistry,
        profile_manager: IConnectorProfileManager | None = None,
        rate_limiter: IRateLimiter | None = None,
    ) -> None:
        """Initialize ConnectorDiagnostics.

        Args:
            registry: Synchronous IConnectorDriverRegistry instance.
            profile_manager: Optional IConnectorProfileManager instance.
            rate_limiter: Optional IRateLimiter instance.
        """
        self._registry = registry
        self._profile_manager = profile_manager
        self._rate_limiter = rate_limiter

    def health(self) -> dict[str, Any]:
        """Return operational health status and component diagnostic checks.

        Returns:
            Dictionary containing deterministic health state ('healthy', 'degraded', or 'unhealthy').
        """
        registry_status = "healthy"
        driver_count = 0

        try:
            drivers = self._registry.list_drivers()
            driver_count = len(drivers)
        except Exception:
            registry_status = "unhealthy"

        # Synchronous health inspection for profile_manager
        profile_mgr_status = "not_configured"
        if self._profile_manager is not None:
            try:
                check_fn = getattr(self._profile_manager, "check_health", None)
                if callable(check_fn):
                    is_ok = check_fn()
                else:
                    is_ok = getattr(self._profile_manager, "is_healthy", True)
                profile_mgr_status = "healthy" if is_ok else "degraded"
            except Exception:
                profile_mgr_status = "degraded"

        # Synchronous health inspection for rate_limiter
        rate_limiter_status = "not_configured"
        if self._rate_limiter is not None:
            try:
                check_fn = getattr(self._rate_limiter, "check_health", None)
                if callable(check_fn):
                    is_ok = check_fn()
                else:
                    is_ok = getattr(self._rate_limiter, "is_healthy", True)
                rate_limiter_status = "healthy" if is_ok else "degraded"
            except Exception:
                rate_limiter_status = "degraded"

        if registry_status == "unhealthy":
            overall_status = "unhealthy"
        elif profile_mgr_status == "degraded" or rate_limiter_status == "degraded":
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        return {
            "status": overall_status,
            "components": {
                "registry": {
                    "status": registry_status,
                    "registered_driver_count": driver_count,
                },
                "profile_manager": {
                    "status": profile_mgr_status,
                },
                "rate_limiter": {
                    "status": rate_limiter_status,
                },
            },
        }

    def metrics(self) -> dict[str, Any]:
        """Return directly observable synchronous engine state metrics.

        Note:
            Because IConnectorProfileManager methods (e.g. list_profiles) are asynchronous,
            profile execution and count metrics (total_profile_count, active_profile_count,
            inactive_profile_count) are deferred to the Milestone 8 async ConnectorEngine facade.

        Returns:
            Dictionary of observable runtime metrics.
        """
        driver_count = 0
        try:
            driver_count = len(self._registry.list_drivers())
        except Exception:
            driver_count = 0

        return {
            "registered_driver_count": driver_count,
            "profile_manager_configured": self._profile_manager is not None,
            "rate_limiter_configured": self._rate_limiter is not None,
            "async_profile_metrics_deferred": True,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return technical diagnostics snapshot containing sanitized primitive data structures.

        Returns:
            Sanitized diagnostic dictionary containing zero credentials, secret handles, or tokens.
        """
        sanitized_drivers: list[dict[str, Any]] = []
        try:
            driver_metadatas = self._registry.list_drivers()
            for meta in driver_metadatas:
                sanitized_drivers.append(
                    {
                        "driver_id": meta.driver_id,
                        "display_name": meta.display_name,
                        "version": meta.version,
                        "description": meta.description,
                        "supported_actions": [action.value for action in meta.supported_actions],
                    }
                )
        except Exception:
            sanitized_drivers = []

        return {
            "engine_version": self.version(),
            "status": self.status(),
            "registered_driver_count": len(sanitized_drivers),
            "registered_drivers": sanitized_drivers,
            "profile_manager_configured": self._profile_manager is not None,
            "rate_limiter_configured": self._rate_limiter is not None,
            "capabilities": self.capabilities(),
        }

    def status(self) -> str:
        """Return current engine state string."""
        return "RUNNING"

    def version(self) -> str:
        """Return engine semantic version string."""
        return "1.0.0"

    def capabilities(self) -> list[str]:
        """Return canonical capability strings declared by the engine."""
        return list(CANONICAL_CAPABILITIES)


__all__ = ["CANONICAL_CAPABILITIES", "ConnectorDiagnostics"]
