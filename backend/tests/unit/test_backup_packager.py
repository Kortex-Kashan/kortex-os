"""Unit tests for Backup Engine archive packaging and atomic assembly."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from kortex.engines.backup.constants import BackupComponentType, BackupScope, BackupState
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.exceptions import BackupStorageError
from kortex.engines.backup.models import ChecksumManifestEntry, ManifestComponentEntry
from kortex.engines.backup.packager import BackupPackager


def test_packager_assemble_backup(tmp_path: Path) -> None:
    """Verify packager creates valid, encrypted, and atomic .kortex-backup container."""
    key = b"\x77" * 32
    crypto = BackupCryptoManager(key=key, key_id="packager-test-key")
    packager = BackupPackager(crypto)

    # Setup dummy database snapshot
    db_file = tmp_path / "mock_db.db"
    db_file.write_bytes(b"SQLite format 3\x00mock-db-content")

    db_entry = ManifestComponentEntry(
        name="database",
        component_type=BackupComponentType.DATABASE,
        relative_path="database/kortex_snapshot.db",
        sha256="db_sha_123",
        size_bytes=db_file.stat().st_size,
    )

    # Setup dummy storage files
    f1 = tmp_path / "file1.txt"
    f1.write_bytes(b"hello file 1")
    storage_files = [(f1, "storage/file1.txt")]
    storage_checksums = [
        ChecksumManifestEntry(path="storage/file1.txt", sha256="f1_sha", size_bytes=len(b"hello file 1"))
    ]

    tmp_unencrypted = tmp_path / "raw.zip"
    tmp_final = tmp_path / "test.kortex-backup.tmp"
    final_target = tmp_path / "backups" / "test.kortex-backup"

    manifest, meta = packager.assemble_backup(
        backup_id="b-pack-1",
        instance_id="inst-pack",
        kortex_version="1.0.0",
        scope=BackupScope.FULL_INSTANCE,
        created_at_iso="2026-09-04T12:00:00Z",
        db_snapshot_path=db_file,
        db_manifest_entry=db_entry,
        schema_revision="rev_42",
        storage_files=storage_files,
        storage_checksums=storage_checksums,
        tmp_unencrypted_zip=tmp_unencrypted,
        tmp_final_path=tmp_final,
        final_target_path=final_target,
    )

    # Verify atomic finalization
    assert final_target.is_file()
    assert not tmp_unencrypted.exists()
    assert not tmp_final.exists()

    assert manifest.backup_id == "b-pack-1"
    assert manifest.state == BackupState.VALID
    assert manifest.encryption is not None
    assert manifest.encryption.key_id == "packager-test-key"

    assert meta.backup_id == "b-pack-1"
    assert meta.is_encrypted is True
    assert meta.file_size_bytes == final_target.stat().st_size

    # Decrypt and inspect internal ZIP structure
    raw_sealed = final_target.read_bytes()
    decrypted_zip = crypto.decrypt_bytes(raw_sealed, manifest.encryption)

    zf = zipfile.ZipFile(io.BytesIO(decrypted_zip))
    namelist = zf.namelist()

    assert "manifest.json" in namelist
    assert "checksums.json" in namelist
    assert "database/kortex_snapshot.db" in namelist
    assert "storage/file1.txt" in namelist

    # Verify manifest content
    mf_data = json.loads(zf.read("manifest.json").decode("utf-8"))
    assert mf_data["database_schema_revision"] == "rev_42"
    assert len(mf_data["components"]) == 2


def test_packager_fails_on_unreadable_storage_file(tmp_path: Path) -> None:
    """Verify packager strictly fails closed if a discovered file cannot be read."""
    key = b"\x77" * 32
    crypto = BackupCryptoManager(key=key, key_id="packager-test-key")
    packager = BackupPackager(crypto)

    db_file = tmp_path / "mock_db.db"
    db_file.write_bytes(b"mock db")
    db_entry = ManifestComponentEntry(
        name="database",
        component_type=BackupComponentType.DATABASE,
        relative_path="database/kortex_snapshot.db",
        sha256="db_sha",
        size_bytes=len(b"mock db"),
    )

    non_existent = tmp_path / "missing_file.txt"
    storage_files = [(non_existent, "storage/missing_file.txt")]
    storage_checksums = [ChecksumManifestEntry(path="storage/missing_file.txt", sha256="none", size_bytes=0)]

    with pytest.raises(BackupStorageError, match="Failed to read storage file during archive packaging"):
        packager.assemble_backup(
            backup_id="b-fail-1",
            instance_id="inst-pack",
            kortex_version="1.0.0",
            scope=BackupScope.FULL_INSTANCE,
            created_at_iso="2026-09-04T12:00:00Z",
            db_snapshot_path=db_file,
            db_manifest_entry=db_entry,
            schema_revision=None,
            storage_files=storage_files,
            storage_checksums=storage_checksums,
            tmp_unencrypted_zip=tmp_path / "raw.zip",
            tmp_final_path=tmp_path / "fail.tmp",
            final_target_path=tmp_path / "fail.kortex-backup",
        )
