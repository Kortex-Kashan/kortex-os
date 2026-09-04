"""Unit tests for Backup Engine retention engine and inviolable safety invariants."""

from __future__ import annotations

import datetime
from pathlib import Path

from kortex.engines.backup.constants import BackupScope, BackupState, RetentionPolicyType
from kortex.engines.backup.models import BackupMetadata, RetentionPolicy
from kortex.engines.backup.repository import BackupRepository
from kortex.engines.backup.retention import RetentionEngine


def _create_mock_backup(
    repo: BackupRepository,
    backup_id: str,
    created_at_dt: datetime.datetime,
    size_bytes: int = 1000,
    state: BackupState = BackupState.VALID,
) -> None:
    """Create a mock artifact file and sidecar metadata in repository."""
    art_file = repo.resolve_artifact_path(f"{backup_id}.kortex-backup")
    art_file.write_bytes(b"x" * size_bytes)

    meta = BackupMetadata(
        backup_id=backup_id,
        state=state,
        created_at=created_at_dt.isoformat(),
        finalized_at=created_at_dt.isoformat(),
        scope=BackupScope.FULL_INSTANCE,
        filename=f"{backup_id}.kortex-backup",
        file_size_bytes=size_bytes,
        sha256="sha-" + backup_id,
        is_encrypted=True,
    )
    repo.save_metadata(meta)


def test_retention_never_deletes_last_valid_backup(tmp_path: Path) -> None:
    """INVIOLABLE SAFETY INVARIANT: If count(valid) <= 1, pruning MUST abort."""
    repo = BackupRepository(tmp_path / "backups")
    retention = RetentionEngine()

    now = datetime.datetime.now(datetime.UTC)
    old_time = now - datetime.timedelta(days=100)

    # Only 1 valid backup exists, older than 30 days
    _create_mock_backup(repo, "sole_backup", old_time)

    # Aggressive policy: max_age_days = 1
    policy = RetentionPolicy(
        policy_type=RetentionPolicyType.AGE,
        max_age_days=1,
    )

    pruned = retention.evaluate_and_prune(repo, policy)

    # Must be aborted!
    assert len(pruned) == 0
    assert repo.resolve_artifact_path("sole_backup.kortex-backup").is_file()


def test_retention_count_policy(tmp_path: Path) -> None:
    """Verify count policy prunes oldest backups in excess of max_count."""
    repo = BackupRepository(tmp_path / "backups")
    retention = RetentionEngine()

    now = datetime.datetime.now(datetime.UTC)

    # Create 5 backups from newest to oldest
    for i in range(5):
        t = now - datetime.timedelta(hours=i)
        _create_mock_backup(repo, f"b_{i}", t)

    policy = RetentionPolicy(
        policy_type=RetentionPolicyType.COUNT,
        max_count=3,
    )

    pruned = retention.evaluate_and_prune(repo, policy)

    # The 2 oldest (b_3, b_4) should be pruned
    assert len(pruned) == 2
    assert "b_3" in pruned
    assert "b_4" in pruned

    # Newest 3 remain
    assert repo.resolve_artifact_path("b_0.kortex-backup").is_file()
    assert repo.resolve_artifact_path("b_1.kortex-backup").is_file()
    assert repo.resolve_artifact_path("b_2.kortex-backup").is_file()


def test_retention_protects_active_backup(tmp_path: Path) -> None:
    """Verify active_backup_id is never pruned even if eligible."""
    repo = BackupRepository(tmp_path / "backups")
    retention = RetentionEngine()

    now = datetime.datetime.now(datetime.UTC)

    _create_mock_backup(repo, "b_old", now - datetime.timedelta(days=40))
    _create_mock_backup(repo, "b_active", now - datetime.timedelta(days=50))
    _create_mock_backup(repo, "b_new", now)

    policy = RetentionPolicy(
        policy_type=RetentionPolicyType.AGE,
        max_age_days=30,
    )

    pruned = retention.evaluate_and_prune(repo, policy, active_backup_id="b_active")

    # Only b_old should be pruned; b_active was protected
    assert "b_old" in pruned
    assert "b_active" not in pruned
    assert repo.resolve_artifact_path("b_active.kortex-backup").is_file()


def test_retention_size_policy(tmp_path: Path) -> None:
    """Verify cumulative size pruning."""
    repo = BackupRepository(tmp_path / "backups")
    retention = RetentionEngine()

    now = datetime.datetime.now(datetime.UTC)

    # 3 backups of 1000 bytes each
    _create_mock_backup(repo, "b_new", now, size_bytes=1000)
    _create_mock_backup(repo, "b_mid", now - datetime.timedelta(hours=1), size_bytes=1000)
    _create_mock_backup(repo, "b_old", now - datetime.timedelta(hours=2), size_bytes=1000)

    # Max size = 2500 bytes (enough for 2, not 3)
    policy = RetentionPolicy(
        policy_type=RetentionPolicyType.SIZE,
        max_size_bytes=2500,
    )

    pruned = retention.evaluate_and_prune(repo, policy)
    assert pruned == ["b_old"]
    assert repo.resolve_artifact_path("b_new.kortex-backup").is_file()
    assert repo.resolve_artifact_path("b_mid.kortex-backup").is_file()
