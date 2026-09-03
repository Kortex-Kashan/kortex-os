"""KORTEX Sentinel Engine — Diagnostics Adapter.

Implements the common IEngineDiagnostics protocol for Sentinel Engine,
providing operational health status, performance metrics, and technical diagnostics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import EngineState
from kortex.engines.sentinel.constants import ENGINE_VERSION, SENTINEL_CAPABILITIES
from kortex.engines.storage.interfaces import IEngineDiagnostics

if TYPE_CHECKING:
    from kortex.engines.sentinel.engine import SentinelEngine


class SentinelDiagnostics(IEngineDiagnostics):
    """Diagnostics adapter for Sentinel Engine conforming to IEngineDiagnostics."""

    def __init__(self, engine: SentinelEngine) -> None:
        self._engine = engine
        self._checks_run: int = 0
        self._deadlock_inspections: int = 0
        self._starvation_events: int = 0
        self._integrity_failures: int = 0
        self._recovery_requests_emitted: int = 0
        self._crash_loops_detected: int = 0

    def record_check_run(self) -> None:
        """Increment count of executed health evaluation cycles."""
        self._checks_run += 1

    def record_deadlock_inspection(self, starvation: bool, deadlock: bool = False) -> None:
        """Record completed deadlock inspection."""
        self._deadlock_inspections += 1
        if starvation:
            self._starvation_events += 1

    def record_integrity_failure(self) -> None:
        """Record an integrity check failure event."""
        self._integrity_failures += 1

    def record_recovery_request(self) -> None:
        """Record an emitted recovery request."""
        self._recovery_requests_emitted += 1

    def record_crash_loop(self) -> None:
        """Record a detected crash loop episode."""
        self._crash_loops_detected += 1

    def health(self) -> dict[str, Any]:
        """Return operational health status and diagnostic checks.

        Conforms to both `healthy` boolean and `status` string conventions.
        """
        is_healthy = self._engine.state in (EngineState.READY, EngineState.RUNNING)
        last_report = self._engine.last_health_report
        return {
            "engine": self._engine.name,
            "status": self._engine.state.value,
            "healthy": is_healthy,
            "active_tasks": len(self._engine.background_tasks),
            "tracked_operations": self._engine.deadlock_detector.tracker.active_count,
            "registered_heartbeats": self._engine.heartbeat_manager.registered_count,
            "last_health_status": last_report.status.value if last_report else "UNKNOWN",
        }

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and monitoring metrics."""
        return {
            "checks_run": self._checks_run,
            "deadlock_inspections": self._deadlock_inspections,
            "starvation_events": self._starvation_events,
            "integrity_failures": self._integrity_failures,
            "recovery_requests_emitted": self._recovery_requests_emitted,
            "crash_loops_detected": self._crash_loops_detected,
            "tracked_operations": self._engine.deadlock_detector.tracker.active_count,
            "registered_heartbeats": self._engine.heartbeat_manager.registered_count,
            "background_tasks_count": len(self._engine.background_tasks),
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics."""
        return {
            "engine": self._engine.name,
            "version": self.version(),
            "state": self._engine.state.value,
            "capabilities": self.capabilities(),
            "config": self._engine.config.model_dump(),
            "metrics": self.metrics(),
        }

    def status(self) -> str:
        """Return current engine state name string."""
        return self._engine.state.value

    def version(self) -> str:
        """Return semantic version string of the engine."""
        return ENGINE_VERSION

    def capabilities(self) -> list[str]:
        """Return list of capability strings registered by Sentinel."""
        return list(SENTINEL_CAPABILITIES)
