"""Unit tests for Recovery Engine write-ahead journaling, phase transitions, and crash safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.engines.recovery.constants import RecoveryJournalPhase
from kortex.engines.recovery.exceptions import RecoveryStorageError
from kortex.engines.recovery.journal import RecoveryJournalManager
from kortex.engines.recovery.models import (
    ChecksumsMetadata,
    RecoveryJournalEntry,
    RollbackState,
    TargetIdentity,
    VerificationState,
)


def create_sample_entry(recovery_id: str = "rec-001", backup_id: str = "bck-001") -> RecoveryJournalEntry:
    return RecoveryJournalEntry(
        recovery_id=recovery_id,
        backup_id=backup_id,
        target_identity=TargetIdentity(
            instance_id="inst-1",
            database_path="storage_data/kortex_local.db",
            storage_root="storage_data",
        ),
        created_at="2026-09-05T00:00:00Z",
        updated_at="2026-09-05T00:00:00Z",
        current_phase=RecoveryJournalPhase.CREATED,
        rollback_state=RollbackState(safety_checkpoint_id="bck-safe-1"),
        verification_state=VerificationState(),
        checksums=ChecksumsMetadata(artifact_sha256="abc123hash"),
    )


def test_journal_creation_and_atomic_persistence(tmp_path: Path) -> None:
    """Verify durable journal creation via atomic file replacement and fsync."""
    journal_file = tmp_path / ".recovery" / "journal.json"
    mgr = RecoveryJournalManager(journal_file_path=journal_file)

    entry = create_sample_entry(recovery_id="rec-jrn-001")
    mgr.write_journal(entry)

    assert journal_file.exists()
    loaded = mgr.load_journal()
    assert loaded is not None
    assert loaded.recovery_id == "rec-jrn-001"
    assert loaded.current_phase == RecoveryJournalPhase.CREATED


def test_journal_phase_progression(tmp_path: Path) -> None:
    """Verify progression through lifecycle phases."""
    journal_file = tmp_path / ".recovery" / "journal.json"
    mgr = RecoveryJournalManager(journal_file_path=journal_file)

    entry = create_sample_entry(recovery_id="rec-jrn-002")
    mgr.write_journal(entry)

    phases = [
        (RecoveryJournalPhase.CHECKPOINT_CREATED, "created_checkpoint"),
        (RecoveryJournalPhase.ARTIFACT_VALIDATED, "validated_artifact"),
        (RecoveryJournalPhase.STAGING, "started_staging"),
        (RecoveryJournalPhase.STAGED, "completed_staging"),
        (RecoveryJournalPhase.PRE_SWAP, "quiesced_system"),
        (RecoveryJournalPhase.STORAGE_SWAP_COMPLETE, "swapped_storage"),
        (RecoveryJournalPhase.DATABASE_SWAP_COMPLETE, "swapped_database"),
        (RecoveryJournalPhase.RECONNECTING, "reconnected_db"),
        (RecoveryJournalPhase.VERIFYING, "verified_readiness"),
        (RecoveryJournalPhase.COMMITTED, "recovery_committed"),
    ]

    for phase, op in phases:
        updated = mgr.record_phase(phase, operation=op)
        assert updated.current_phase == phase
        assert op in updated.completed_operations


def test_journal_record_phase_without_active_journal(tmp_path: Path) -> None:
    """Verify recording phase when no active journal exists raises RecoveryStorageError."""
    journal_file = tmp_path / ".recovery" / "journal.json"
    mgr = RecoveryJournalManager(journal_file_path=journal_file)

    with pytest.raises(RecoveryStorageError, match="No active recovery journal found"):
        mgr.record_phase(RecoveryJournalPhase.COMMITTED)


def test_journal_archive_and_delete(tmp_path: Path) -> None:
    """Verify archiving and deleting active journal file."""
    journal_file = tmp_path / ".recovery" / "journal.json"
    mgr = RecoveryJournalManager(journal_file_path=journal_file)

    entry = create_sample_entry(recovery_id="rec-arch-001")
    mgr.write_journal(entry)
    assert journal_file.exists()

    archived_path = mgr.archive_journal("completed")
    assert archived_path is not None
    assert archived_path.exists()
    assert not journal_file.exists()

    # Re-write and test delete
    mgr.write_journal(entry)
    assert journal_file.exists()
    deleted = mgr.delete_journal()
    assert deleted is True
    assert not journal_file.exists()


def test_crash_sweep_quarantine_corrupted_journal(tmp_path: Path) -> None:
    """Verify corrupted journal is quarantined and returns None on load."""
    journal_dir = tmp_path / ".recovery"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / "journal.json"

    # Write corrupted JSON content
    journal_file.write_text("{{corrupted-json-invalid", encoding="utf-8")

    mgr = RecoveryJournalManager(journal_file_path=journal_file)
    loaded = mgr.load_journal()
    assert loaded is None

    # Corrupt journal should have been renamed/quarantined
    assert not journal_file.exists()
    quarantined = list(journal_dir.glob("journal.json.corrupt.*"))
    assert len(quarantined) == 1
