"""KORTEX Update Engine write-ahead journaling and crash recovery analysis.

Phase 7 — Production Hardening — Update Engine.
Guarantees crash-consistent state transitions using write -> flush -> fsync -> os.replace.
Coordinates the 22-point crash matrix.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from kortex.engines.update.constants import (
    HISTORY_FILENAME,
    JOURNAL_FILENAME,
    MAX_HISTORY_ENTRIES,
    UpdateJournalPhase,
    UpdateState,
)
from kortex.engines.update.exceptions import (
    UpdateError,
    UpdateOperatorActionRequiredError,
)
from kortex.engines.update.models import (
    UpdateHistoryEntry,
    UpdateJournalPhaseRecord,
    UpdateJournalRecord,
    UpdateManifest,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class UpdateJournalManager:
    """Manages the write-ahead journal and crash analysis for Update operations."""

    def __init__(
        self,
        update_base_dir: str | Path = "storage_data/.update",
    ) -> None:
        self._update_base_dir = Path(update_base_dir).resolve()
        self._journal_path = self._update_base_dir / JOURNAL_FILENAME
        self._history_path = self._update_base_dir / HISTORY_FILENAME
        self._active_record: UpdateJournalRecord | None = None
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        self._update_base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def journal_path(self) -> Path:
        return self._journal_path

    @property
    def history_path(self) -> Path:
        return self._history_path

    def has_active_journal(self) -> bool:
        """Check if an unarchived journal file exists on disk."""
        return self._journal_path.is_file()

    def load_active_record(self) -> UpdateJournalRecord | None:
        """Load the active journal record from disk with validation."""
        if not self._journal_path.is_file():
            self._active_record = None
            return None

        try:
            with self._journal_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self._active_record = UpdateJournalRecord.model_validate(data)
            return self._active_record
        except Exception as exc:
            logger.error("Corrupt or unparseable journal at %s: %s", self._journal_path, exc)
            raise UpdateOperatorActionRequiredError(
                f"Active journal at '{self._journal_path}' is corrupt or unparseable: {exc}. "
                f"Operator intervention required to inspect and clear journal."
            ) from exc

    def create_journal(
        self,
        update_id: str,
        manifest: UpdateManifest,
        current_version: str,
        staging_dir: str | None = None,
    ) -> UpdateJournalRecord:
        """Create and atomically commit an initial journal record."""
        now = _utc_now_iso()
        record = UpdateJournalRecord(
            update_id=update_id,
            manifest=manifest,
            current_phase=UpdateJournalPhase.CREATED,
            created_at=now,
            updated_at=now,
            staging_directory=staging_dir,
            target_version=manifest.version.target_version,
            current_version=current_version,
            phases=[
                UpdateJournalPhaseRecord(
                    phase=UpdateJournalPhase.CREATED,
                    timestamp=now,
                    metadata={"initial_version": current_version},
                )
            ],
        )
        self._active_record = record
        self._write_durable_record(record)
        return record

    def record_phase(
        self,
        phase: UpdateJournalPhase,
        metadata: dict[str, Any] | None = None,
        safety_checkpoint_id: str | None = None,
        staging_dir: str | None = None,
        error_message: str | None = None,
        operator_notes: str | None = None,
        rollback_files: list[str] | None = None,
        filesystem_applied: bool | None = None,
        restart_required: bool | None = None,
        runtime_activated: bool | None = None,
    ) -> UpdateJournalRecord:
        """Record a phase transition and atomically persist it to disk."""
        record = self.load_active_record()
        if record is None:
            raise UpdateError("Cannot record phase: no active journal record exists.")

        now = _utc_now_iso()
        record.current_phase = phase
        record.updated_at = now

        if safety_checkpoint_id is not None:
            record.safety_checkpoint_id = safety_checkpoint_id
        if staging_dir is not None:
            record.staging_directory = staging_dir
        if error_message is not None:
            record.error_message = error_message
        if operator_notes is not None:
            record.operator_notes = operator_notes
        if rollback_files is not None:
            record.rollback_files = rollback_files
        if filesystem_applied is not None:
            record.filesystem_applied = filesystem_applied
        if restart_required is not None:
            record.restart_required = restart_required
        if runtime_activated is not None:
            record.runtime_activated = runtime_activated

        record.phases.append(
            UpdateJournalPhaseRecord(
                phase=phase,
                timestamp=now,
                metadata=metadata or {},
            )
        )

        self._active_record = record
        self._write_durable_record(record)
        return record

    def _write_durable_record(self, record: UpdateJournalRecord) -> None:
        """Atomically persist record to disk using write -> flush -> fsync -> os.replace."""
        self._ensure_directory()
        tmp_path = self._update_base_dir / f"{JOURNAL_FILENAME}.tmp.{uuid.uuid4().hex}"
        payload_bytes = json.dumps(record.model_dump(), indent=2, ensure_ascii=False).encode("utf-8")

        with open(tmp_path, "wb") as f:
            f.write(payload_bytes)
            f.flush()
            os.fsync(f.fileno())

        # Atomic replace
        os.replace(tmp_path, self._journal_path)

    def archive_journal(self, status: str) -> None:
        """Archive completed or terminal journal record into history.json and delete active journal."""
        record = self._active_record or self.load_active_record()
        if record is None:
            return

        entry = UpdateHistoryEntry(
            update_id=record.update_id,
            target_version=record.target_version,
            status=status,
            started_at=record.created_at,
            completed_at=_utc_now_iso(),
            safety_checkpoint_id=record.safety_checkpoint_id,
            error_message=record.error_message,
        )
        self._append_history_entry(entry)

        # Remove active journal file
        if self._journal_path.is_file():
            try:
                self._journal_path.unlink()
            except OSError as exc:
                logger.warning("Could not delete completed journal file %s: %s", self._journal_path, exc)
        self._active_record = None

    def _append_history_entry(self, entry: UpdateHistoryEntry) -> None:
        """Append an informational audit entry to history.json (bounded to MAX_HISTORY_ENTRIES)."""
        entries = self.load_history()
        entries.append(entry)
        if len(entries) > MAX_HISTORY_ENTRIES:
            entries = entries[-MAX_HISTORY_ENTRIES:]

        tmp_path = self._update_base_dir / f"{HISTORY_FILENAME}.tmp.{uuid.uuid4().hex}"
        payload = [e.model_dump() for e in entries]
        payload_bytes = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")

        with open(tmp_path, "wb") as f:
            f.write(payload_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self._history_path)

    def load_history(self) -> list[UpdateHistoryEntry]:
        """Load history entries from history.json. Resilient to corruption."""
        if not self._history_path.is_file():
            return []
        try:
            with self._history_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [UpdateHistoryEntry.model_validate(item) for item in data]
            return []
        except Exception as exc:
            logger.warning("Could not read update history at %s: %s; resetting", self._history_path, exc)
            return []

    def evaluate_crash_recovery(self) -> tuple[UpdateState, str, str | None]:
        """Analyze unresolved active journal on startup according to 22-point crash matrix.

        Returns: (inferred_state, recommended_action, checkpoint_id)
        """
        record = self.load_active_record()
        if record is None:
            return UpdateState.IDLE, "NOOP", None

        phase = record.current_phase

        # Case: Already committed prior to crash
        if phase == UpdateJournalPhase.COMMITTED:
            return UpdateState.COMPLETED, "ARCHIVE", record.safety_checkpoint_id

        # Cases 1-7: Pre-mutation phases
        if phase in (
            UpdateJournalPhase.CREATED,
            UpdateJournalPhase.MANIFEST_VERIFIED,
            UpdateJournalPhase.ARTIFACT_ACQUIRED,
            UpdateJournalPhase.ARTIFACT_VERIFIED,
            UpdateJournalPhase.STAGED,
        ):
            return UpdateState.FAILED, "PURGE_STAGING", None

        # Case 8: Checkpoint created, but quiescence/mutation not started
        if phase == UpdateJournalPhase.CHECKPOINT_CREATED:
            return UpdateState.FAILED, "PURGE_STAGING", record.safety_checkpoint_id

        # Case 9-10: Quiesced, but migration not committed
        if phase == UpdateJournalPhase.QUIESCED:
            # Migration may have started or failed mid-flight. Checkpoint MUST be restored!
            return UpdateState.ROLLBACK_REQUIRED, "RESTORE_CHECKPOINT", record.safety_checkpoint_id

        # Cases 11-16: Live database migrated or files partially swapped
        if phase in (
            UpdateJournalPhase.SCHEMA_MIGRATED,
            UpdateJournalPhase.FILES_SWAPPED,
            UpdateJournalPhase.VERIFIED,
        ):
            if record.filesystem_applied and not record.runtime_activated:
                # Files were swapped; restart occurred. Post-update verification required!
                return UpdateState.VERIFYING, "VERIFY_RUNTIME", record.safety_checkpoint_id
            return UpdateState.ROLLBACK_REQUIRED, "RESTORE_CHECKPOINT", record.safety_checkpoint_id

        # Cases 20-21: Mid-rollback
        if phase == UpdateJournalPhase.ROLLING_BACK:
            return UpdateState.ROLLING_BACK, "RESUME_ROLLBACK", record.safety_checkpoint_id

        if phase == UpdateJournalPhase.ROLLED_BACK:
            return UpdateState.ROLLED_BACK, "CLEANUP_ROLLED_BACK", record.safety_checkpoint_id

        # Cases 22+: Unresolvable or FAILED_NEEDS_OPERATOR
        return UpdateState.FAILED_NEEDS_OPERATOR, "OPERATOR_INTERVENTION", record.safety_checkpoint_id
