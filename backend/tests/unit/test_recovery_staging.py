"""Unit tests for Recovery Engine staging manager, sandbox isolation, and resource checks."""

from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from kortex.engines.recovery.exceptions import (
    RecoveryInsufficientDiskSpaceError,
    RecoverySecurityError,
)
from kortex.engines.recovery.staging import RecoveryStagingManager


def test_staging_workspace_lifecycle(tmp_path: Path) -> None:
    """Verify creation and cleanup of isolated staging workspace."""
    staging_root = tmp_path / "staging_root"
    mgr = RecoveryStagingManager(staging_base_dir=staging_root)

    workspace = mgr.get_recovery_workspace(recovery_id="rec-lifecycle-001")
    workspace.mkdir(parents=True, exist_ok=True)
    assert workspace.exists()
    assert "rec-lifecycle-001" in str(workspace)

    # Subdirectories for components
    (workspace / "database").mkdir()
    (workspace / "storage").mkdir()
    assert (workspace / "database").exists()
    assert (workspace / "storage").exists()

    # Clean up workspace
    mgr.cleanup_workspace("rec-lifecycle-001")
    assert not workspace.exists()


def test_resource_preflight_sufficient_space(tmp_path: Path) -> None:
    """Verify resource preflight passes when sufficient disk space exists."""
    staging_root = tmp_path / "staging"
    mgr = RecoveryStagingManager(staging_base_dir=staging_root)

    # Mock 10GB free space
    with patch("shutil.disk_usage") as mock_usage:
        mock_usage.return_value = (100 * 1024**3, 90 * 1024**3, 10 * 1024**3)
        # Payload size small -> should succeed
        req, avail = mgr.preflight_disk_capacity(
            artifact_size=10 * 1024 * 1024,
            uncompressed_payload_size=20 * 1024 * 1024,
            extracted_db_size=5 * 1024 * 1024,
            extracted_storage_size=15 * 1024 * 1024,
            live_db_size=5 * 1024 * 1024,
            live_storage_size=15 * 1024 * 1024,
            target_volume_dir=staging_root,
        )
        assert avail >= req


def test_resource_preflight_insufficient_space(tmp_path: Path) -> None:
    """Verify resource preflight fails closed when disk space is below required limit."""
    staging_root = tmp_path / "staging"
    mgr = RecoveryStagingManager(staging_base_dir=staging_root)

    # Mock 100MB free space
    with patch("shutil.disk_usage") as mock_usage:
        mock_usage.return_value = (100 * 1024**3, 99 * 1024**3, 100 * 1024 * 1024)
        # Required bytes will be > 500MB safety margin alone
        with pytest.raises(RecoveryInsufficientDiskSpaceError, match=r"Insufficient disk space"):
            mgr.preflight_disk_capacity(
                artifact_size=100 * 1024 * 1024,
                uncompressed_payload_size=200 * 1024 * 1024,
                extracted_db_size=50 * 1024 * 1024,
                extracted_storage_size=150 * 1024 * 1024,
                live_db_size=50 * 1024 * 1024,
                live_storage_size=150 * 1024 * 1024,
                target_volume_dir=staging_root,
            )


def test_safe_archive_extraction(tmp_path: Path) -> None:
    """Verify extraction of benign archive into staging workspace."""
    staging_root = tmp_path / "staging"
    mgr = RecoveryStagingManager(staging_base_dir=staging_root)
    workspace = mgr.get_recovery_workspace("rec-safe-001")
    dest_dir = workspace / "extracted"

    # Build benign ZIP archive on disk
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", b'{"version": 1}')
        zf.writestr("database/kortex_snapshot.db", b"SQLite format 3\x00")
        zf.writestr("storage/documents/doc1.txt", b"Hello Kortex")

    extracted_files = mgr.extract_zip_safely(zip_path, dest_dir)
    assert len(extracted_files) == 3
    assert (dest_dir / "manifest.json").exists()
    assert (dest_dir / "database" / "kortex_snapshot.db").exists()
    assert (dest_dir / "storage" / "documents" / "doc1.txt").read_bytes() == b"Hello Kortex"


def test_adversarial_zip_traversal_rejection(tmp_path: Path) -> None:
    """Verify extraction rejects entries attempting directory traversal."""
    staging_root = tmp_path / "staging"
    mgr = RecoveryStagingManager(staging_base_dir=staging_root)
    dest_dir = mgr.get_recovery_workspace("rec-trav-001") / "extracted"

    zip_path = tmp_path / "traversal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../../escape.txt", b"evil")

    with pytest.raises(RecoverySecurityError, match=r"Archive entry contains path traversal"):
        mgr.extract_zip_safely(zip_path, dest_dir)


def test_adversarial_zip_absolute_path_rejection(tmp_path: Path) -> None:
    """Verify extraction rejects entries with absolute paths."""
    staging_root = tmp_path / "staging"
    mgr = RecoveryStagingManager(staging_base_dir=staging_root)
    dest_dir = mgr.get_recovery_workspace("rec-abs-001") / "extracted"

    zip_path = tmp_path / "abs.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("/root/escape.txt", b"evil")

    with pytest.raises(RecoverySecurityError, match=r"Archive entry is an absolute path"):
        mgr.extract_zip_safely(zip_path, dest_dir)


def test_adversarial_zip_bomb_file_count_rejection(tmp_path: Path) -> None:
    """Verify extraction rejects archives exceeding maximum file count limit."""
    staging_root = tmp_path / "staging"
    # Set max file count to 3
    mgr = RecoveryStagingManager(staging_base_dir=staging_root, max_file_count=3)
    dest_dir = mgr.get_recovery_workspace("rec-bomb-002") / "extracted"

    zip_path = tmp_path / "bomb_count.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for i in range(5):
            zf.writestr(f"file_{i}.txt", b"payload")

    with pytest.raises(RecoverySecurityError, match=r"Archive entry count .* exceeds maximum limit"):
        mgr.extract_zip_safely(zip_path, dest_dir)
