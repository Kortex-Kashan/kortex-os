"""KORTEX Update Engine quiescence coordinator and maintenance lock management.

Phase 7 — Production Hardening — Update Engine.
Guarantees strict mutual exclusion between Update, Backup, and Recovery operations,
and cleanly drains database connection pools before live mutation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kortex.engines.update.constants import (
    DEFAULT_QUIESCENCE_TIMEOUT_SECONDS,
)
from kortex.engines.update.exceptions import (
    UpdateConcurrencyError,
    UpdateQuiescenceError,
)

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger(__name__)


class UpdateQuiescenceManager:
    """Coordinates cross-engine mutual exclusion, maintenance locks, and database quiescence."""

    def __init__(
        self,
        lock_file_path: Path | str = "storage_data/.update/maintenance.lock",
        recovery_lock_path: Path | str = "storage_data/.recovery/maintenance.lock",
        backup_lock_path: Path | str = "storage_data/backups/backup.lock",
        timeout_seconds: float = DEFAULT_QUIESCENCE_TIMEOUT_SECONDS,
    ) -> None:
        self._lock_file = Path(lock_file_path).resolve()
        self._recovery_lock = Path(recovery_lock_path).resolve()
        self._backup_lock = Path(backup_lock_path).resolve()
        self._timeout_seconds = timeout_seconds
        self._locked = False

    @property
    def lock_file(self) -> Path:
        return self._lock_file

    def is_maintenance_locked(self) -> bool:
        """Check if an update maintenance lock file exists."""
        return self._lock_file.is_file()

    def check_cross_engine_concurrency(self) -> None:
        """Assert that no competing Backup or Recovery operation is in progress."""
        if self._recovery_lock.is_file():
            raise UpdateConcurrencyError(
                f"Conflicting recovery operation in progress: found active recovery lock at '{self._recovery_lock}'"
            )
        if self._backup_lock.is_file():
            raise UpdateConcurrencyError(
                f"Conflicting backup operation in progress: found active backup lock at '{self._backup_lock}'"
            )

    async def acquire_maintenance_lock(self, update_id: str, metadata: dict[str, Any] | None = None) -> None:
        """Acquire the exclusive update maintenance lock with cross-engine mutual exclusion."""
        self.check_cross_engine_concurrency()

        if self._lock_file.is_file():
            # Check if PID in lockfile is still alive
            try:
                with self._lock_file.open("r", encoding="utf-8") as f:
                    lock_data = json.load(f)
                locked_pid = lock_data.get("pid")
                if locked_pid and isinstance(locked_pid, int):
                    # Probe if PID exists on host
                    if self._pid_exists(locked_pid):
                        raise UpdateConcurrencyError(
                            f"Another update operation '{lock_data.get('update_id')}' "
                            f"is actively running under PID {locked_pid}."
                        )
                    logger.warning("Found stale update maintenance lock from dead PID %s; replacing.", locked_pid)
            except Exception as exc:
                if isinstance(exc, UpdateConcurrencyError):
                    raise
                logger.warning("Could not parse existing lock file: %s; overwriting", exc)

        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "update_id": update_id,
            "pid": os.getpid(),
            "acquired_at": str(asyncio.get_event_loop().time()),
            "metadata": metadata or {},
        }
        try:
            with self._lock_file.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self._locked = True
        except OSError as exc:
            raise UpdateQuiescenceError(
                f"Failed to acquire update maintenance lock at '{self._lock_file}': {exc}"
            ) from exc

    def release_maintenance_lock(self) -> None:
        """Release the update maintenance lock file."""
        if self._lock_file.is_file():
            try:
                self._lock_file.unlink()
            except OSError as exc:
                logger.warning("Could not remove update maintenance lock file %s: %s", self._lock_file, exc)
        self._locked = False

    async def drain_and_disconnect_database(self, kernel: Kernel | None) -> None:
        """Drain active transactions and disconnect database connection pools cleanly."""
        if kernel is None or not hasattr(kernel, "db"):
            return

        db_manager = getattr(kernel, "db", None)
        if db_manager is not None and hasattr(db_manager, "disconnect"):
            disconnect_fn = db_manager.disconnect
            if callable(disconnect_fn):
                try:
                    res = disconnect_fn()
                    if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                        await asyncio.wait_for(res, timeout=self._timeout_seconds)
                    logger.info("Successfully drained and disconnected database connections for update.")
                except TimeoutError as exc:
                    raise UpdateQuiescenceError(
                        f"Timed out after {self._timeout_seconds}s waiting for database connection pool to drain."
                    ) from exc
                except Exception as exc:
                    raise UpdateQuiescenceError(f"Error disconnecting database connection pool: {exc}") from exc

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        """Check if process ID exists on the current operating system."""
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
