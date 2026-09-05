"""KORTEX Recovery Engine durable write-ahead journal manager.

Phase 7 — Production Hardening — Recovery Engine.
Provides crash-consistent write-ahead state machine tracking at
storage_data/.recovery/journal.json using atomic file replacement and os.fsync.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import time
from pathlib import Path

from kortex.engines.recovery.constants import (
    DEFAULT_RECOVERY_JOURNAL_FILE,
    JOURNAL_TMP_EXTENSION,
    RecoveryJournalPhase,
)
from kortex.engines.recovery.exceptions import RecoveryStorageError
from kortex.engines.recovery.models import RecoveryJournalEntry

logger = logging.getLogger("kortex.engines.recovery.journal")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class RecoveryJournalManager:
    """Manages the write-ahead recovery journal with fsync durability."""

    def __init__(self, journal_file_path: str | Path = DEFAULT_RECOVERY_JOURNAL_FILE) -> None:
        self._journal_path = Path(journal_file_path).resolve()
        self._journal_dir = self._journal_path.parent

    @property
    def journal_path(self) -> Path:
        return self._journal_path

    @property
    def journal_dir(self) -> Path:
        return self._journal_dir

    def ensure_journal_directory(self) -> None:
        """Ensure parent journal directory exists."""
        self._journal_dir.mkdir(parents=True, exist_ok=True)

    def write_journal(self, entry: RecoveryJournalEntry) -> None:
        """Atomically and durably persist recovery journal entry to disk.

        Ordering: WRITE -> FLUSH -> FSYNC -> ATOMIC REPLACE.
        """
        self.ensure_journal_directory()
        tmp_path = self._journal_path.with_suffix(self._journal_path.suffix + JOURNAL_TMP_EXTENSION)

        data_dict = entry.model_dump(mode="json")
        json_bytes = json.dumps(data_dict, indent=2).encode("utf-8")

        try:
            with tmp_path.open("wb") as f:
                f.write(json_bytes)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, self._journal_path)

            # On POSIX platforms, fsync parent directory to guarantee entry directory persistence
            if os.name != "nt":
                try:
                    dir_fd = os.open(str(self._journal_dir), os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass

        except OSError as exc:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise RecoveryStorageError(
                f"Failed to durably write recovery journal to '{self._journal_path}': {exc}"
            ) from exc

    def load_journal(self) -> RecoveryJournalEntry | None:
        """Load and validate current recovery journal, if present."""
        if not self._journal_path.is_file():
            return None

        try:
            with self._journal_path.open("rb") as f:
                raw_bytes = f.read()
            data = json.loads(raw_bytes.decode("utf-8"))
            return RecoveryJournalEntry.model_validate(data)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            logger.error("Recovery journal at '%s' is corrupted or invalid: %s", self._journal_path, exc)
            # Quarantine corrupted journal
            corrupt_backup = self._journal_path.with_name(f"journal.json.corrupt.{int(time.time())}")
            try:
                os.replace(self._journal_path, corrupt_backup)
                logger.warning("Quarantined corrupted journal to '%s'", corrupt_backup)
            except OSError:
                pass
            return None
        except OSError as exc:
            raise RecoveryStorageError(f"Failed to read recovery journal from '{self._journal_path}': {exc}") from exc

    def record_phase(
        self,
        phase: RecoveryJournalPhase,
        operation: str | None = None,
        error_message: str | None = None,
        operator_notes: str | None = None,
    ) -> RecoveryJournalEntry:
        """Update current phase and append operation with durability guarantees."""
        existing = self.load_journal()
        if existing is None:
            raise RecoveryStorageError("Cannot record phase: No active recovery journal found on disk.")

        completed_ops = list(existing.completed_operations)
        if operation and operation not in completed_ops:
            completed_ops.append(operation)

        updated_dict = existing.model_dump()
        updated_dict["current_phase"] = phase.value
        updated_dict["updated_at"] = _utc_now_iso()
        updated_dict["completed_operations"] = completed_ops
        if error_message:
            updated_dict["error_message"] = error_message
        if operator_notes:
            updated_dict["operator_notes"] = operator_notes

        updated_entry = RecoveryJournalEntry.model_validate(updated_dict)
        self.write_journal(updated_entry)
        return updated_entry

    def update_journal(self, entry: RecoveryJournalEntry) -> None:
        """Persist modified journal entry."""
        self.write_journal(entry)

    def archive_journal(self, status: str = "ARCHIVED") -> Path | None:
        """Move journal to an archived timestamped file."""
        if not self._journal_path.is_file():
            return None

        archive_filename = f"journal.json.{status.lower()}.{int(time.time())}"
        archive_path = self._journal_path.with_name(archive_filename)
        try:
            os.replace(self._journal_path, archive_path)
            logger.info("Archived recovery journal to '%s'", archive_path)
            return archive_path
        except OSError as exc:
            logger.warning("Failed to archive recovery journal '%s': %s", self._journal_path, exc)
            return None

    def delete_journal(self) -> bool:
        """Remove active journal file."""
        if not self._journal_path.is_file():
            return False
        try:
            self._journal_path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            logger.warning("Failed to delete recovery journal '%s': %s", self._journal_path, exc)
            return False
