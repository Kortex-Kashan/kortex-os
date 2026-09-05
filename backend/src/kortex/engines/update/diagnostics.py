"""IEngineDiagnostics implementation for the KORTEX Update Engine.

Phase 7 — Production Hardening — Update Engine.
Provides standardized self-observability and operational telemetry
conforming to the IEngineDiagnostics protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kortex.engines.storage.interfaces import IEngineDiagnostics
from kortex.engines.update.constants import (
    CAPABILITY_UPDATE_APPLY,
    CAPABILITY_UPDATE_CANCEL,
    CAPABILITY_UPDATE_CHECK,
    CAPABILITY_UPDATE_DIAGNOSTICS_GET,
    CAPABILITY_UPDATE_GET,
    CAPABILITY_UPDATE_STAGE,
    CURRENT_ENGINE_VERSION,
    UPDATE_ENGINE_NAME,
)

if TYPE_CHECKING:
    from kortex.engines.update.engine import UpdateEngine


class UpdateDiagnosticsAdapter(IEngineDiagnostics):
    """Conforms the Update Engine to the standardized IEngineDiagnostics interface."""

    def __init__(self, engine: UpdateEngine) -> None:
        self._engine = engine

    def status(self) -> str:
        """Return operational state string."""
        return self._engine.state.value

    def version(self) -> str:
        """Return engine version string."""
        return CURRENT_ENGINE_VERSION

    def capabilities(self) -> list[str]:
        """Return list of capability identifiers registered by this engine."""
        return [
            CAPABILITY_UPDATE_CHECK,
            CAPABILITY_UPDATE_STAGE,
            CAPABILITY_UPDATE_APPLY,
            CAPABILITY_UPDATE_GET,
            CAPABILITY_UPDATE_CANCEL,
            CAPABILITY_UPDATE_DIAGNOSTICS_GET,
        ]

    def health(self) -> dict[str, Any]:
        """Return operational health status dictionary."""
        is_ready = self._engine.state.value in ("READY", "RUNNING")
        is_maintenance = self._engine.quiescence_manager.is_maintenance_locked()

        status_str = "HEALTHY"
        if not is_ready:
            status_str = "DEGRADED"
        elif is_maintenance:
            status_str = "MAINTENANCE"

        return {
            "status": status_str,
            "healthy": is_ready and not is_maintenance,
            "engine": UPDATE_ENGINE_NAME,
            "engine_state": self._engine.state.value,
            "current_version": self._engine.current_version,
            "maintenance_locked": is_maintenance,
            "active_operation": self._engine.active_operation,
            "updates_completed": self._engine.updates_completed_count,
            "updates_failed": self._engine.updates_failed_count,
            "updates_rolled_back": self._engine.updates_rolled_back_count,
        }

    def metrics(self) -> dict[str, Any]:
        """Return operational runtime metrics."""
        return {
            "updates_attempted": self._engine.updates_attempted_count,
            "updates_completed": self._engine.updates_completed_count,
            "updates_failed": self._engine.updates_failed_count,
            "updates_rolled_back": self._engine.updates_rolled_back_count,
            "last_update_duration_seconds": self._engine.last_update_duration_seconds,
            "last_update_timestamp": self._engine.last_update_timestamp,
            "last_error_message": self._engine.last_error_message,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical self-diagnostics dictionary."""
        return {
            "engine": UPDATE_ENGINE_NAME,
            "version": CURRENT_ENGINE_VERSION,
            "state": self._engine.state.value,
            "active_operation": self._engine.active_operation,
            "maintenance_locked": self._engine.quiescence_manager.is_maintenance_locked(),
            "has_active_journal": self._engine.journal_manager.has_active_journal(),
            "update_directory": str(self._engine.config.update_directory),
            "staging_directory": str(self._engine.config.staging_directory),
            "metrics": self.metrics(),
            "health": self.health(),
        }
