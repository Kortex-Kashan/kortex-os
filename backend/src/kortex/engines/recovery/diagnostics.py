"""IEngineDiagnostics implementation for the KORTEX Recovery Engine.

Phase 7 — Production Hardening — Recovery Engine.
Provides standardized self-observability and operational telemetry
conforming to the IEngineDiagnostics protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kortex.engines.recovery.constants import (
    CAPABILITY_RECOVERY_CREATE,
    CAPABILITY_RECOVERY_DELETE,
    CAPABILITY_RECOVERY_DIAGNOSTICS_GET,
    CAPABILITY_RECOVERY_GET,
    CAPABILITY_RECOVERY_LIST,
    CAPABILITY_RECOVERY_VERIFY,
    CURRENT_ENGINE_VERSION,
    RECOVERY_ENGINE_NAME,
)
from kortex.engines.storage.interfaces import IEngineDiagnostics

if TYPE_CHECKING:
    from kortex.engines.recovery.engine import RecoveryEngine


class RecoveryDiagnosticsAdapter(IEngineDiagnostics):
    """Conforms the Recovery Engine to the standardized IEngineDiagnostics interface."""

    def __init__(self, engine: RecoveryEngine) -> None:
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
            CAPABILITY_RECOVERY_CREATE,
            CAPABILITY_RECOVERY_LIST,
            CAPABILITY_RECOVERY_GET,
            CAPABILITY_RECOVERY_VERIFY,
            CAPABILITY_RECOVERY_DELETE,
            CAPABILITY_RECOVERY_DIAGNOSTICS_GET,
        ]

    def health(self) -> dict[str, Any]:
        """Return operational health status dictionary."""
        is_ready = self._engine.state.value in ("READY", "RUNNING")
        crypto_ready = self._engine.crypto_manager.is_key_available

        return {
            "status": "HEALTHY" if (is_ready and crypto_ready) else "DEGRADED",
            "healthy": is_ready and crypto_ready,
            "engine": RECOVERY_ENGINE_NAME,
            "engine_state": self._engine.state.value,
            "crypto_key_available": crypto_ready,
            "active_operation": self._engine.active_operation,
            "recoveries_completed": self._engine.recoveries_completed_count,
            "recoveries_failed": self._engine.recoveries_failed_count,
            "recoveries_rolled_back": self._engine.recoveries_rolled_back_count,
        }

    def metrics(self) -> dict[str, Any]:
        """Return operational runtime metrics."""
        return {
            "recoveries_attempted": self._engine.recoveries_attempted_count,
            "recoveries_completed": self._engine.recoveries_completed_count,
            "recoveries_failed": self._engine.recoveries_failed_count,
            "recoveries_rolled_back": self._engine.recoveries_rolled_back_count,
            "last_recovery_duration_seconds": self._engine.last_recovery_duration_seconds,
            "last_recovery_timestamp": self._engine.last_recovery_timestamp,
            "last_error_message": self._engine.last_error_message,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical self-diagnostics dictionary."""
        diag = self._engine.get_diagnostics()
        return diag.model_dump(mode="json")
