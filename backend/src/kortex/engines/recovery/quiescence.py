"""KORTEX Recovery Engine workload quiescence and maintenance lock manager.

Phase 7 — Production Hardening — Recovery Engine.
Coordinates kernel maintenance mode, in-flight workload drain,
database connection pool disposal, and filesystem maintenance locks.
"""

from __future__ import annotations

import asyncio
import datetime
import inspect
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from kortex.engines.recovery.constants import (
    DEFAULT_QUIESCENCE_TIMEOUT_SECONDS,
    DEFAULT_RECOVERY_LOCK_FILE,
)
from kortex.engines.recovery.exceptions import (
    RecoveryConcurrencyError,
    RecoveryQuiescenceTimeoutError,
    RecoveryStorageError,
)

if TYPE_CHECKING:
    from kortex.core.db import DatabaseEngineManager
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.recovery.quiescence")


class RecoveryQuiescenceManager:
    """Coordinates workload quiescence, database connection draining, and maintenance locks."""

    def __init__(
        self,
        lock_file_path: str | Path = DEFAULT_RECOVERY_LOCK_FILE,
        timeout_seconds: float = DEFAULT_QUIESCENCE_TIMEOUT_SECONDS,
    ) -> None:
        self._lock_file = Path(lock_file_path).resolve()
        self._timeout_seconds = timeout_seconds
        self._is_maintenance_acquired = False

    @property
    def lock_file(self) -> Path:
        return self._lock_file

    def is_locked(self) -> bool:
        """True if maintenance lock file exists."""
        return self._lock_file.is_file()

    def acquire_maintenance_lock(self, recovery_id: str) -> None:
        """Acquire filesystem-level maintenance lock for this recovery operation."""
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        if self._lock_file.is_file():
            try:
                data = json.loads(self._lock_file.read_text(encoding="utf-8"))
                holder_id = data.get("recovery_id", "unknown")
                holder_pid = data.get("pid", 0)
                raise RecoveryConcurrencyError(
                    f"Recovery maintenance lock is already held by operation '{holder_id}' (PID {holder_pid})."
                )
            except (json.JSONDecodeError, OSError):
                raise RecoveryConcurrencyError(
                    "Recovery maintenance lock is already held by another operation."
                ) from None

        lock_data = {
            "recovery_id": recovery_id,
            "pid": os.getpid(),
            "acquired_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

        try:
            self._lock_file.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
            self._is_maintenance_acquired = True
            logger.info("Acquired recovery maintenance lock for '%s'.", recovery_id)
        except OSError as exc:
            raise RecoveryStorageError(f"Failed to write maintenance lock file: {exc}") from exc

    def release_maintenance_lock(self) -> None:
        """Release filesystem maintenance lock."""
        if self._lock_file.is_file():
            try:
                self._lock_file.unlink(missing_ok=True)
                self._is_maintenance_acquired = False
                logger.info("Released recovery maintenance lock.")
            except OSError as exc:
                logger.warning("Failed to remove maintenance lock file '%s': %s", self._lock_file, exc)

    async def enter_quiescence(
        self,
        kernel: Kernel | None,
        db_manager: DatabaseEngineManager,
    ) -> None:
        """Quiesce active workloads and disconnect database engine pool."""
        logger.info("Entering recovery quiescence (timeout=%.1fs)...", self._timeout_seconds)

        # 1. Enable Kernel maintenance mode if supported
        if kernel is not None and hasattr(kernel, "set_maintenance_mode"):
            try:
                kernel.set_maintenance_mode(True)
            except Exception as exc:
                logger.warning("Kernel does not support set_maintenance_mode or failed: %s", exc)

        # 2. Wait for active workloads to drain
        # In desktop KORTEX, we await a short settlement window
        settlement_delay = min(0.5, self._timeout_seconds)
        await asyncio.sleep(settlement_delay)

        # 3. Disconnect database manager (closes all pool connections and OS handles)
        try:
            if hasattr(db_manager, "disconnect"):
                res = db_manager.disconnect()
                if inspect.isawaitable(res):
                    await asyncio.wait_for(res, timeout=self._timeout_seconds)
            logger.info("DatabaseEngineManager connection pool disposed cleanly.")
        except TimeoutError as exc:
            self.release_maintenance_lock()
            if kernel is not None and hasattr(kernel, "set_maintenance_mode"):
                kernel.set_maintenance_mode(False)
            raise RecoveryQuiescenceTimeoutError(
                f"Quiescence timeout expired ({self._timeout_seconds}s) while disconnecting database engine pool."
            ) from exc

    async def exit_quiescence(
        self,
        kernel: Kernel | None,
        db_manager: DatabaseEngineManager,
    ) -> None:
        """Reconnect database engine pool and restore normal operations."""
        logger.info("Exiting recovery quiescence...")

        # Reconnect database pool
        try:
            if hasattr(db_manager, "connect"):
                res = db_manager.connect()
                if inspect.isawaitable(res):
                    await res
            logger.info("DatabaseEngineManager connection pool re-established.")
        except Exception as exc:
            logger.error("Failed to re-establish database connection pool on quiescence exit: %s", exc)
            raise

        # Release kernel maintenance mode
        if kernel is not None and hasattr(kernel, "set_maintenance_mode"):
            try:
                kernel.set_maintenance_mode(False)
            except Exception as exc:
                logger.warning("Failed to reset Kernel maintenance mode: %s", exc)

        # Release lock file
        self.release_maintenance_lock()
