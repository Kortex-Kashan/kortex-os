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

ALLOWED_ERROR_CATEGORIES: frozenset[str] = frozenset({
    "rate_limit",
    "authentication",
    "driver_not_found",
    "driver_execution",
    "cancelled",
    "http_4xx",
    "http_5xx",
    "unknown_error",
})


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

        # --- Metric State ---
        # Core execution metrics (owned exclusively by ConnectorEngine.execute_action)
        self._total_executions: int = 0
        self._successful_executions: int = 0
        self._failed_executions: int = 0
        self._total_latency_ms: float = 0.0
        self._min_latency_ms: float | None = None
        self._max_latency_ms: float | None = None
        self._per_driver_executions: dict[str, int] = {}
        self._per_action_type_executions: dict[str, int] = {}

        # Stage and attempt fact metrics
        self._retry_count: int = 0
        self._rate_limit_rejections: int = 0
        self._authentication_failures: int = 0
        self._driver_failures: int = 0
        self._cancellation_count: int = 0
        self._per_profile_executions: dict[str, int] = {}
        self._http_status_codes: dict[int, int] = {}
        self._error_categories: dict[str, int] = {
            "rate_limit": 0,
            "authentication": 0,
            "driver_not_found": 0,
            "driver_execution": 0,
            "cancelled": 0,
            "http_4xx": 0,
            "http_5xx": 0,
            "unknown_error": 0,
        }

    # -- Recording API --------------------------------------------------------

    def record_execution(
        self,
        is_success: bool,
        latency_ms: float,
        driver_id: str | None = None,
        action_type: str | None = None,
        profile_id: str | None = None,
    ) -> None:
        """Record exactly ONE top-level execution outcome and its latency.

        This method has the single unambiguous responsibility of recording top-level
        execution outcome metrics (total_executions, successful_executions /
        failed_executions, total_latency_ms, min_latency_ms, max_latency_ms,
        per_driver_executions, per_action_type_executions, and per_profile_executions).

        MUST be called ONLY by ConnectorEngine.execute_action().
        Stage-specific methods (e.g. record_rate_limit_rejection) MUST NOT call this
        method or alter top-level outcome counters directly.
        """
        self._total_executions += 1
        if is_success:
            self._successful_executions += 1
        else:
            self._failed_executions += 1

        lat = max(0.0, float(latency_ms))
        self._total_latency_ms += lat
        if self._min_latency_ms is None or lat < self._min_latency_ms:
            self._min_latency_ms = lat
        if self._max_latency_ms is None or lat > self._max_latency_ms:
            self._max_latency_ms = lat

        if driver_id and isinstance(driver_id, str) and driver_id.strip():
            d_key = driver_id.strip()
            self._per_driver_executions[d_key] = self._per_driver_executions.get(d_key, 0) + 1

        if action_type:
            a_key = str(action_type).strip().upper()
            self._per_action_type_executions[a_key] = (
                self._per_action_type_executions.get(a_key, 0) + 1
            )

        if profile_id and isinstance(profile_id, str) and profile_id.strip():
            p_key = profile_id.strip()
            if p_key in self._per_profile_executions:
                self._per_profile_executions[p_key] += 1
            elif len(self._per_profile_executions) < 1000:
                self._per_profile_executions[p_key] = 1
            else:
                self._per_profile_executions["__other__"] = (
                    self._per_profile_executions.get("__other__", 0) + 1
                )

    def record_retry(self, count: int = 1) -> None:
        """Record driver/network retry attempts performed."""
        if count > 0:
            self._retry_count += int(count)

    def record_rate_limit_rejection(self) -> None:
        """Record Stage 3 rate limit token acquisition failure."""
        self._rate_limit_rejections += 1

    def record_authentication_failure(self) -> None:
        """Record Stage 2 secret handle resolution failure."""
        self._authentication_failures += 1

    def record_driver_failure(self) -> None:
        """Record Stage 4 driver lookup or driver execution error."""
        self._driver_failures += 1

    def record_cancellation(self) -> None:
        """Record task cancellation via asyncio.CancelledError."""
        self._cancellation_count += 1

    def record_http_status(self, status_code: Any) -> None:
        """Record an observed numeric HTTP status code.

        Only records when status_code is a valid integer (or integer string)
        and NOT a boolean. Does not fabricate status codes for generic driver errors.
        """
        if isinstance(status_code, bool):
            return
        code_int: int | None = None
        if isinstance(status_code, int):
            code_int = status_code
        elif isinstance(status_code, str) and status_code.strip().isdigit():
            code_int = int(status_code.strip())

        if code_int is not None and 100 <= code_int <= 599:
            self._http_status_codes[code_int] = self._http_status_codes.get(code_int, 0) + 1

    def record_error_category(self, category: str) -> None:
        """Record a structured high-level error category."""
        cat_key = category.strip().lower() if isinstance(category, str) else "unknown_error"
        if cat_key in ALLOWED_ERROR_CATEGORIES:
            self._error_categories[cat_key] += 1
        else:
            self._error_categories["unknown_error"] += 1

    # -- IEngineDiagnostics Implementation -----------------------------------

    def health(self) -> dict[str, Any]:
        """Return operational health status and component diagnostic checks.

        Returns:
            Dictionary containing health state ('healthy', 'degraded', or 'unhealthy')
            and lightweight summary metrics.
        """
        registry_status = "healthy"
        driver_count = 0

        try:
            drivers = self._registry.list_drivers()
            driver_count = len(drivers)
        except Exception:
            registry_status = "unhealthy"

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
            "summary": {
                "total_executions": self._total_executions,
                "successful_executions": self._successful_executions,
                "failed_executions": self._failed_executions,
            },
        }

    def metrics(self) -> dict[str, Any]:
        """Return directly observable synchronous engine state metrics.

        Returns:
            Dictionary of complete runtime operational metrics.
        """
        driver_count = 0
        try:
            driver_count = len(self._registry.list_drivers())
        except Exception:
            driver_count = 0

        avg_lat = (
            round(self._total_latency_ms / self._total_executions, 2)
            if self._total_executions > 0
            else 0.0
        )
        success_rate = (
            round((self._successful_executions / self._total_executions) * 100.0, 2)
            if self._total_executions > 0
            else 100.0
        )

        return {
            "registered_driver_count": driver_count,
            "profile_manager_configured": self._profile_manager is not None,
            "rate_limiter_configured": self._rate_limiter is not None,
            "async_profile_metrics_deferred": True,
            "total_executions": self._total_executions,
            "successful_executions": self._successful_executions,
            "failed_executions": self._failed_executions,
            "success_rate_percentage": success_rate,
            "total_latency_ms": round(self._total_latency_ms, 3),
            "average_latency_ms": avg_lat,
            "min_latency_ms": round(self._min_latency_ms, 3) if self._min_latency_ms is not None else None,
            "max_latency_ms": round(self._max_latency_ms, 3) if self._max_latency_ms is not None else None,
            "retry_count": self._retry_count,
            "rate_limit_rejections": self._rate_limit_rejections,
            "authentication_failures": self._authentication_failures,
            "driver_failures": self._driver_failures,
            "cancellation_count": self._cancellation_count,
            "per_driver_executions": dict(self._per_driver_executions),
            "per_action_type_executions": dict(self._per_action_type_executions),
            "per_profile_executions": dict(self._per_profile_executions),
            "http_status_codes": dict(self._http_status_codes),
            "error_categories": dict(self._error_categories),
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
            "metrics": self.metrics(),
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


__all__ = ["ALLOWED_ERROR_CATEGORIES", "CANONICAL_CAPABILITIES", "ConnectorDiagnostics"]
