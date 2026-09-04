"""IEngineDiagnostics implementation for the KORTEX Monitoring Engine.

Provides self-observability and operational telemetry for the monitoring
subsystem without recursive self-collection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kortex.engines.monitoring.constants import MONITORING_CAPABILITIES

if TYPE_CHECKING:
    from kortex.engines.monitoring.engine import MonitoringEngine

VERSION = "1.0.0"


class MonitoringDiagnostics:
    """Diagnostics adapter for Monitoring Engine conforming to IEngineDiagnostics."""

    def __init__(self, engine: MonitoringEngine) -> None:
        self._engine = engine

    def status(self) -> str:
        """Return operational state string."""
        return self._engine.state.value

    def version(self) -> str:
        """Return engine version string."""
        return VERSION

    def capabilities(self) -> list[str]:
        """Return list of registered capabilities."""
        return list(MONITORING_CAPABILITIES)

    def health(self) -> dict[str, Any]:
        """Return operational health status dictionary."""
        is_running = self._engine.state.value == "RUNNING"
        collector = self._engine.collector
        registry = self._engine.registry

        return {
            "status": "HEALTHY" if is_running else self._engine.state.value,
            "healthy": is_running,
            "engines_monitored_count": len(collector.last_engines_polled),
            "active_series_count": registry.active_series_count,
            "collection_cycles_total": collector.collection_cycles_total,
            "last_collection_duration_ms": collector.last_collection_duration_ms,
        }

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and telemetry counters."""
        collector = self._engine.collector
        registry = self._engine.registry
        ts = self._engine.timeseries_buffer

        return {
            "collection_cycles_total": collector.collection_cycles_total,
            "engine_timeouts_total": collector.engine_timeouts_total,
            "engine_failures_total": collector.engine_failures_total,
            "active_series": registry.active_series_count,
            "metric_names_count": registry.metric_names_count,
            "cardinality_rejections_total": registry.cardinality_rejections_total,
            "name_rejections_total": registry.name_rejections_total,
            "points_buffered_total": ts.points_total,
            "last_collection_duration_ms": collector.last_collection_duration_ms,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics."""
        collector = self._engine.collector
        sys_telemetry = collector.last_system_telemetry

        return {
            "engine": self._engine.name,
            "version": self.version(),
            "status": self.status(),
            "system_resources": sys_telemetry,
            "polled_engines": collector.last_engines_polled,
            "active_alerts_count": len(self._engine.active_alerts),
            "background_tasks_count": len(self._engine.background_tasks),
        }
