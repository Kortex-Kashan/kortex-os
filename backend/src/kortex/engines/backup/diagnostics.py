"""IEngineDiagnostics implementation for the KORTEX Backup Engine.

Phase 7 — Production Hardening — Backup Engine.
Provides standardized self-observability and operational telemetry
conforming to the IEngineDiagnostics protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kortex.engines.backup.constants import (
    CAPABILITY_BACKUP_CREATE,
    CAPABILITY_BACKUP_DELETE,
    CAPABILITY_BACKUP_DIAGNOSTICS_GET,
    CAPABILITY_BACKUP_GET,
    CAPABILITY_BACKUP_LIST,
    CAPABILITY_BACKUP_VERIFY,
    CURRENT_ENGINE_VERSION,
)
from kortex.engines.storage.interfaces import IEngineDiagnostics

if TYPE_CHECKING:
    from kortex.engines.backup.engine import BackupEngine


class BackupDiagnosticsAdapter(IEngineDiagnostics):
    """Conforms the Backup Engine to the standardized IEngineDiagnostics interface."""

    def __init__(self, engine: BackupEngine) -> None:
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
            CAPABILITY_BACKUP_CREATE,
            CAPABILITY_BACKUP_LIST,
            CAPABILITY_BACKUP_GET,
            CAPABILITY_BACKUP_VERIFY,
            CAPABILITY_BACKUP_DELETE,
            CAPABILITY_BACKUP_DIAGNOSTICS_GET,
        ]

    def health(self) -> dict[str, Any]:
        """Return operational health status dictionary."""
        is_ready = self._engine.state.value in ("READY", "RUNNING")
        crypto_ready = self._engine.crypto_manager.is_key_available

        return {
            "status": "HEALTHY" if (is_ready and crypto_ready) else "DEGRADED",
            "healthy": is_ready and crypto_ready,
            "engine_state": self._engine.state.value,
            "encryption_key_available": crypto_ready,
            "backup_directory": str(self._engine.repository.backup_directory),
            "total_backups": self._engine.total_backups_count,
            "last_error": self._engine.last_error_message,
        }

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and operation counters."""
        return {
            "backups_created_total": self._engine.backups_created_count,
            "backups_failed_total": self._engine.backups_failed_count,
            "backups_verified_total": self._engine.backups_verified_count,
            "backups_pruned_total": self._engine.backups_pruned_count,
            "last_backup_duration_seconds": self._engine.last_backup_duration_seconds,
            "cumulative_storage_bytes": self._engine.cumulative_storage_bytes,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics and system environment details."""
        diag = self._engine.get_diagnostics()
        return diag.model_dump(mode="json")
