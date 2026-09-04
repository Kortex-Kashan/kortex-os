"""Unit tests for Backup Engine filesystem repository and sandboxing."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from kortex.engines.backup.constants import BackupScope, BackupState
from kortex.engines.backup.exceptions import BackupNotFoundError, BackupPathSecurityError
from kortex.engines.backup.models import (
    BackupMetadata,
    DeleteBackupRequest,
    GetBackupRequest,
    ListBackupsRequest,
)
from kortex.engines.backup.repository import BackupRepository


def test_repository_path_sandboxing(tmp_path: Path) -> None:
    """Verify repository blocks path traversal and path escape attempts."""
    repo = BackupRepository(tmp_path / "backups")

    # Safe relative filename resolves cleanly
    safe_path = repo.resolve_artifact_path("kortex_backup_123.kortex-backup")
    assert safe_path.parent == (tmp_path / "backups").resolve()

    # Traversal attempts must fail closed
    with pytest.raises(BackupPathSecurityError, match="path traversal prohibited"):
        repo.resolve_artifact_path("../escape.kortex-backup")

    with pytest.raises(BackupPathSecurityError, match="path traversal prohibited"):
        repo.resolve_artifact_path("/etc/passwd")

    with pytest.raises(BackupPathSecurityError, match="path traversal prohibited"):
        repo.resolve_artifact_path("sub\\traversal")


def test_repository_save_and_get_metadata(tmp_path: Path) -> None:
    """Verify atomic saving and loading of sidecar metadata."""
    repo = BackupRepository(tmp_path / "backups")

    meta = BackupMetadata(
        backup_id="b-test-repo",
        state=BackupState.VALID,
        created_at="2026-09-04T12:00:00Z",
        finalized_at="2026-09-04T12:01:00Z",
        scope=BackupScope.FULL_INSTANCE,
        filename="b-test-repo.kortex-backup",
        file_size_bytes=2048,
        sha256="sha-12345",
        is_encrypted=True,
    )
    repo.save_metadata(meta)

    # Verify sidecar file exists
    meta_file = repo.resolve_artifact_path("b-test-repo.meta.json")
    assert meta_file.is_file()

    # Retrieve through get_backup
    resp = repo.get_backup(GetBackupRequest(backup_id="b-test-repo"))
    assert resp.backup.backup_id == "b-test-repo"
    assert resp.backup.sha256 == "sha-12345"


def test_repository_list_and_delete(tmp_path: Path) -> None:
    """Verify listing and deleting backups."""
    repo = BackupRepository(tmp_path / "backups")

    # Create dummy artifact and metadata
    art_path = repo.resolve_artifact_path("b-del.kortex-backup")
    art_path.write_bytes(b"dummy artifact")

    meta = BackupMetadata(
        backup_id="b-del",
        state=BackupState.VALID,
        created_at="2026-09-04T12:00:00Z",
        finalized_at="2026-09-04T12:01:00Z",
        scope=BackupScope.FULL_INSTANCE,
        filename="b-del.kortex-backup",
        file_size_bytes=14,
        sha256="sha-dummy",
        is_encrypted=True,
    )
    repo.save_metadata(meta)

    list_resp = repo.list_backups(ListBackupsRequest(limit=10))
    assert list_resp.total_count == 1
    assert list_resp.backups[0].backup_id == "b-del"

    # Delete
    del_resp = repo.delete_backup(DeleteBackupRequest(backup_id="b-del"))
    assert del_resp.deleted is True
    assert not art_path.exists()
    assert not repo.resolve_artifact_path("b-del.meta.json").exists()

    # Second delete raises BackupNotFoundError
    with pytest.raises(BackupNotFoundError):
        repo.delete_backup(DeleteBackupRequest(backup_id="b-del"))


def test_repository_cleanup_orphaned_temporaries(tmp_path: Path) -> None:
    """Verify sweeping of stale temporary files."""
    repo = BackupRepository(tmp_path / "backups")

    stale_tmp = repo.backup_directory / "stale.kortex-backup.tmp"
    stale_tmp.write_bytes(b"abandoned")

    # Set mtime to 2 hours ago
    two_hours_ago = time.time() - 7200
    import os

    os.utime(stale_tmp, (two_hours_ago, two_hours_ago))

    fresh_tmp = repo.backup_directory / "fresh.kortex-backup.tmp"
    fresh_tmp.write_bytes(b"active")

    cleaned = repo.cleanup_orphaned_temporaries(max_age_seconds=3600)
    assert cleaned == 1
    assert not stale_tmp.exists()
    assert fresh_tmp.exists()
