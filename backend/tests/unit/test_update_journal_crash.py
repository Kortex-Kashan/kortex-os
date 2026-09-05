"""Unit tests for Update Engine write-ahead journaling, atomic persistence, and crash matrix evaluation.

Phase 7 — Production Hardening — Update Engine.
Verifies crash recovery behavior across all lifecycle phases, journal durability, and history rotation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.engines.update.constants import UpdateJournalPhase, UpdateState
from kortex.engines.update.exceptions import UpdateOperatorActionRequiredError
from kortex.engines.update.journal import UpdateJournalManager
from kortex.engines.update.models import (
    UpdateManifest,
    UpdateManifestDatabase,
    UpdateManifestPackage,
    UpdateManifestVersion,
)


def create_sample_manifest(update_id: str = "upd-jrn-001") -> UpdateManifest:
    return UpdateManifest(
        manifest_id=f"mf-{update_id}",
        created_at="2026-09-05T00:00:00Z",
        expires_at="2026-09-12T00:00:00Z",
        key_id="k1",
        signature="s1",
        version=UpdateManifestVersion(target_version="0.2.0", min_supported_version="0.1.0"),
        package=UpdateManifestPackage(
            filename="upd.zip", sha256="abc", size_bytes=10, uncompressed_bytes=20, file_count=1
        ),
        database=UpdateManifestDatabase(requires_migration=False),
    )


def test_atomic_journal_creation_and_phase_progression(tmp_path: Path) -> None:
    """Verify journal is created via atomic fsync and records phase transitions."""
    update_dir = tmp_path / ".update"
    journal = UpdateJournalManager(update_base_dir=update_dir)
    manifest = create_sample_manifest()

    record = journal.create_journal(
        update_id="upd-jrn-001",
        manifest=manifest,
        current_version="0.1.0",
        staging_dir=str(tmp_path / "staging"),
    )
    assert record.update_id == "upd-jrn-001"
    assert record.current_phase == UpdateJournalPhase.CREATED
    assert (update_dir / "journal.json").is_file()

    # Record phase ARTIFACT_VERIFIED
    journal.record_phase(UpdateJournalPhase.ARTIFACT_VERIFIED)
    loaded = journal.load_active_record()
    assert loaded is not None
    assert loaded.current_phase == UpdateJournalPhase.ARTIFACT_VERIFIED

    # Record phase CHECKPOINT_CREATED with safety_checkpoint_id
    journal.record_phase(
        UpdateJournalPhase.CHECKPOINT_CREATED,
        safety_checkpoint_id="bck-safe-123",
    )
    loaded = journal.load_active_record()
    assert loaded is not None
    assert loaded.current_phase == UpdateJournalPhase.CHECKPOINT_CREATED
    assert loaded.safety_checkpoint_id == "bck-safe-123"


def test_crash_matrix_pre_mutation_phases(tmp_path: Path) -> None:
    """Verify crash recovery evaluation for pre-mutation phases (safe local cleanup)."""
    update_dir = tmp_path / ".update"
    journal = UpdateJournalManager(update_base_dir=update_dir)
    manifest = create_sample_manifest()

    for phase in [
        UpdateJournalPhase.CREATED,
        UpdateJournalPhase.MANIFEST_VERIFIED,
        UpdateJournalPhase.ARTIFACT_ACQUIRED,
        UpdateJournalPhase.ARTIFACT_VERIFIED,
        UpdateJournalPhase.STAGED,
    ]:
        journal.create_journal("upd-pre", manifest, "0.1.0")
        journal.record_phase(phase)
        state, action, chk = journal.evaluate_crash_recovery()
        assert state == UpdateState.FAILED
        assert action == "PURGE_STAGING"
        assert chk is None


def test_crash_matrix_checkpoint_and_quiesced_phases(tmp_path: Path) -> None:
    """Verify crash recovery evaluation for checkpoint and quiesced phases."""
    update_dir = tmp_path / ".update"
    journal = UpdateJournalManager(update_base_dir=update_dir)
    manifest = create_sample_manifest()

    # Case: CHECKPOINT_CREATED
    journal.create_journal("upd-chk", manifest, "0.1.0")
    journal.record_phase(UpdateJournalPhase.CHECKPOINT_CREATED, safety_checkpoint_id="bck-safe-1")
    state, action, chk = journal.evaluate_crash_recovery()
    assert state == UpdateState.FAILED
    assert action == "PURGE_STAGING"
    assert chk == "bck-safe-1"

    # Case: QUIESCED
    journal.record_phase(UpdateJournalPhase.QUIESCED)
    state, action, chk = journal.evaluate_crash_recovery()
    assert state == UpdateState.ROLLBACK_REQUIRED
    assert action == "RESTORE_CHECKPOINT"
    assert chk == "bck-safe-1"


def test_crash_matrix_post_migration_and_operator_phases(tmp_path: Path) -> None:
    """Verify crash recovery evaluation for post-migration and operator intervention phases."""
    update_dir = tmp_path / ".update"
    journal = UpdateJournalManager(update_base_dir=update_dir)
    manifest = create_sample_manifest()

    journal.create_journal("upd-post", manifest, "0.1.0")
    journal.record_phase(UpdateJournalPhase.SCHEMA_MIGRATED, safety_checkpoint_id="bck-safe-1")
    state, action, _chk = journal.evaluate_crash_recovery()
    assert state == UpdateState.ROLLBACK_REQUIRED
    assert action == "RESTORE_CHECKPOINT"

    # Case: FAILED_NEEDS_OPERATOR
    journal.record_phase(UpdateJournalPhase.FAILED_NEEDS_OPERATOR)
    state, action, _chk = journal.evaluate_crash_recovery()
    assert state == UpdateState.FAILED_NEEDS_OPERATOR
    assert action == "OPERATOR_INTERVENTION"


def test_crash_matrix_post_swap_verify_runtime(tmp_path: Path) -> None:
    """Verify crash recovery evaluation for FILES_SWAPPED/VERIFIED phases when the filesystem
    swap already completed and a restart has occurred (filesystem_applied=True,
    runtime_activated=False): the crash matrix must direct the startup sweep to resume
    verification (VERIFY_RUNTIME) rather than invoke Recovery, since the swap already
    succeeded and only runtime confirmation remains.
    """
    update_dir = tmp_path / ".update"
    journal = UpdateJournalManager(update_base_dir=update_dir)
    manifest = create_sample_manifest()

    journal.create_journal("upd-swap", manifest, "0.1.0")
    journal.record_phase(
        UpdateJournalPhase.FILES_SWAPPED,
        safety_checkpoint_id="bck-safe-1",
        filesystem_applied=True,
        runtime_activated=False,
    )
    state, action, chk = journal.evaluate_crash_recovery()
    assert state == UpdateState.VERIFYING
    assert action == "VERIFY_RUNTIME"
    assert chk == "bck-safe-1"

    journal.record_phase(UpdateJournalPhase.VERIFIED)
    state, action, chk = journal.evaluate_crash_recovery()
    assert state == UpdateState.VERIFYING
    assert action == "VERIFY_RUNTIME"


def test_crash_matrix_post_swap_incomplete_requires_rollback(tmp_path: Path) -> None:
    """If a crash occurs mid-swap and filesystem_applied was never confirmed True, the
    startup sweep must NOT assume a safe restart state -- it must request Recovery-backed
    restoration rather than silently resuming as if the swap had succeeded.
    """
    update_dir = tmp_path / ".update"
    journal = UpdateJournalManager(update_base_dir=update_dir)
    manifest = create_sample_manifest()

    journal.create_journal("upd-swap-partial", manifest, "0.1.0")
    journal.record_phase(
        UpdateJournalPhase.FILES_SWAPPED,
        safety_checkpoint_id="bck-safe-2",
        filesystem_applied=False,
    )
    state, action, chk = journal.evaluate_crash_recovery()
    assert state == UpdateState.ROLLBACK_REQUIRED
    assert action == "RESTORE_CHECKPOINT"
    assert chk == "bck-safe-2"


def test_crash_matrix_rollback_phases(tmp_path: Path) -> None:
    """Verify crash recovery evaluation for mid-rollback and post-rollback phases."""
    update_dir = tmp_path / ".update"
    journal = UpdateJournalManager(update_base_dir=update_dir)
    manifest = create_sample_manifest()

    journal.create_journal("upd-rb", manifest, "0.1.0")
    journal.record_phase(UpdateJournalPhase.ROLLING_BACK, safety_checkpoint_id="bck-safe-3")
    state, action, chk = journal.evaluate_crash_recovery()
    assert state == UpdateState.ROLLING_BACK
    assert action == "RESUME_ROLLBACK"
    assert chk == "bck-safe-3"

    journal.record_phase(UpdateJournalPhase.ROLLED_BACK)
    state, action, chk = journal.evaluate_crash_recovery()
    assert state == UpdateState.ROLLED_BACK
    assert action == "CLEANUP_ROLLED_BACK"


def test_crash_matrix_committed_phase_archives(tmp_path: Path) -> None:
    """A crash after COMMITTED (e.g. before the archive step ran) must be resolved as
    already-completed and archived, not treated as unresolved or requiring rollback.
    """
    update_dir = tmp_path / ".update"
    journal = UpdateJournalManager(update_base_dir=update_dir)
    manifest = create_sample_manifest()

    journal.create_journal("upd-committed", manifest, "0.1.0")
    journal.record_phase(UpdateJournalPhase.COMMITTED, safety_checkpoint_id="bck-safe-4")
    state, action, chk = journal.evaluate_crash_recovery()
    assert state == UpdateState.COMPLETED
    assert action == "ARCHIVE"
    assert chk == "bck-safe-4"


def test_corrupt_journal_fails_closed(tmp_path: Path) -> None:
    """A corrupt or unparseable journal.json must fail closed with
    UpdateOperatorActionRequiredError rather than silently discarding unresolved state.
    """
    update_dir = tmp_path / ".update"
    update_dir.mkdir(parents=True)
    (update_dir / "journal.json").write_text("{not valid json!!!", encoding="utf-8")

    journal = UpdateJournalManager(update_base_dir=update_dir)
    with pytest.raises(UpdateOperatorActionRequiredError):
        journal.load_active_record()

    with pytest.raises(UpdateOperatorActionRequiredError):
        journal.evaluate_crash_recovery()


def test_archive_journal_and_history_rotation(tmp_path: Path) -> None:
    """Verify completed journal is archived into bounded history.json."""
    update_dir = tmp_path / ".update"
    journal = UpdateJournalManager(update_base_dir=update_dir)
    manifest = create_sample_manifest()

    journal.create_journal("upd-hist-01", manifest, "0.1.0")
    journal.record_phase(UpdateJournalPhase.COMMITTED)

    journal.archive_journal("COMPLETED")
    assert not (update_dir / "journal.json").exists()
    assert (update_dir / "history.json").is_file()

    history = journal.load_history()
    assert len(history) == 1
    assert history[0].update_id == "upd-hist-01"
    assert history[0].status == "COMPLETED"
