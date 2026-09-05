"""Unit tests for Update Engine staging management and preflight disk space validation.

Phase 7 — Production Hardening — Update Engine.
Verifies isolated staging workspaces, disk space headroom preflight checks, and staging cleanup.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from kortex.engines.update.exceptions import UpdateDiskSpaceError
from kortex.engines.update.models import (
    UpdateManifest,
    UpdateManifestCompatibility,
    UpdateManifestDatabase,
    UpdateManifestPackage,
    UpdateManifestVersion,
)
from kortex.engines.update.staging import UpdateStagingManager


def create_manifest(package_size: int = 1_000_000, uncompressed: int = 2_000_000) -> UpdateManifest:
    return UpdateManifest(
        manifest_id="mf-stage-test",
        created_at="2026-09-05T00:00:00Z",
        expires_at="2026-09-12T00:00:00Z",
        key_id="k1",
        signature="s1",
        version=UpdateManifestVersion(
            target_version="0.2.0",
            min_supported_version="0.1.0",
            release_channel="stable",
        ),
        compatibility=UpdateManifestCompatibility(
            platforms=["windows", "linux"],
            architectures=["x86_64"],
        ),
        package=UpdateManifestPackage(
            filename="upd.zip",
            sha256="abc123hash",
            size_bytes=package_size,
            uncompressed_bytes=uncompressed,
            file_count=2,
        ),
        database=UpdateManifestDatabase(requires_migration=False),
    )


def test_disk_space_preflight_sufficient(tmp_path: Path) -> None:
    """Verify preflight check passes when available disk space exceeds required budget."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    manifest = create_manifest()

    # Mock 10 GB free space
    with patch("shutil.disk_usage") as mock_usage:
        mock_usage.return_value = (100_000_000_000, 10_000_000_000, 10_000_000_000)
        staging.preflight_disk_space(manifest, live_db_path=tmp_path / "missing.db")


def test_disk_space_preflight_insufficient(tmp_path: Path) -> None:
    """Verify preflight check raises UpdateDiskSpaceError when free space is inadequate."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    manifest = create_manifest()

    # Mock only 100 KB free space
    with patch("shutil.disk_usage") as mock_usage:
        mock_usage.return_value = (100_000_000_000, 99_999_900_000, 100_000)
        with pytest.raises(UpdateDiskSpaceError) as exc_info:
            staging.preflight_disk_space(manifest, live_db_path=tmp_path / "missing.db")
        assert "Insufficient disk space" in str(exc_info.value)


def test_extract_and_purge_staged_archive(tmp_path: Path) -> None:
    """Verify extraction into isolated staging directory and subsequent purge."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    update_id = "upd-test-stage-01"

    # Create dummy zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app/hello.py", "print('hello')\n")
        zf.writestr("config/settings.json", "{}\n")

    zip_file = tmp_path / "package.zip"
    zip_file.write_bytes(buf.getvalue())

    staged_workspace = staging.extract_staged_archive(zip_file, update_id)
    assert staged_workspace.is_dir()
    assert (staged_workspace / "app" / "hello.py").is_file()
    assert (staged_workspace / "config" / "settings.json").is_file()

    # Verify purge
    staging.purge_staging(update_id)
    assert not (tmp_path / "staging" / update_id).exists()

    # Idempotent purge on nonexistent path
    staging.purge_staging("nonexistent-update")
