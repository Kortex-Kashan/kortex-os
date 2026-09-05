"""Unit tests for Recovery Engine multi-tier artifact and staging validator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kortex.engines.backup.constants import BackupState
from kortex.engines.backup.models import BackupMetadata, ChecksumManifest, ChecksumManifestEntry
from kortex.engines.recovery.crypto import RecoveryCryptoManager
from kortex.engines.recovery.database_restorer import DatabaseRestorer
from kortex.engines.recovery.exceptions import (
    RecoveryArtifactCorruptError,
    RecoveryNotFoundError,
    RecoveryValidationError,
)
from kortex.engines.recovery.staging import RecoveryStagingManager
from kortex.engines.recovery.validator import RecoveryValidator


def test_validator_locate_artifact_success(tmp_path: Path) -> None:
    """Verify locator finds valid backup artifact and sidecar metadata."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    art_file = backup_dir / "bck-001.kortex-backup"
    art_file.write_bytes(b"0" * 64)

    meta_file = backup_dir / "bck-001.meta.json"
    meta_file.write_text(
        json.dumps(
            {
                "backup_id": "bck-001",
                "filename": "bck-001.kortex-backup",
                "created_at": "2026-09-05T00:00:00Z",
                "sha256": "fakehash",
                "size_bytes": 64,
                "state": "VALID",
            }
        ),
        encoding="utf-8",
    )

    validator = RecoveryValidator(
        backup_directory=backup_dir,
        storage_root=tmp_path / "storage",
        staging_manager=RecoveryStagingManager(tmp_path / "staging"),
        database_restorer=DatabaseRestorer(),
    )

    path, meta = validator.locate_artifact("bck-001")
    assert path == art_file
    assert meta is not None
    assert meta.backup_id == "bck-001"


def test_validator_locate_artifact_not_found(tmp_path: Path) -> None:
    """Verify locator raises RecoveryNotFoundError for missing backup artifact."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    validator = RecoveryValidator(
        backup_directory=backup_dir,
        storage_root=tmp_path / "storage",
        staging_manager=RecoveryStagingManager(tmp_path / "staging"),
        database_restorer=DatabaseRestorer(),
    )

    with pytest.raises(RecoveryNotFoundError, match="Backup artifact not found"):
        validator.locate_artifact("non-existent-bck")


def test_validator_verify_envelope_truncated(tmp_path: Path) -> None:
    """Verify envelope check rejects truncated file smaller than 28 bytes."""
    short_file = tmp_path / "short.bin"
    short_file.write_bytes(b"too-short")

    validator = RecoveryValidator(
        backup_directory=tmp_path,
        storage_root=tmp_path,
        staging_manager=RecoveryStagingManager(tmp_path),
        database_restorer=DatabaseRestorer(),
    )

    with pytest.raises(RecoveryArtifactCorruptError, match="smaller than minimum encrypted envelope size"):
        validator.verify_envelope(short_file, None)


def test_validator_verify_envelope_sha_mismatch(tmp_path: Path) -> None:
    """Verify envelope check rejects artifact whose SHA-256 does not match sidecar."""
    art_file = tmp_path / "art.bin"
    art_file.write_bytes(b"A" * 64)

    meta = BackupMetadata(
        backup_id="bck-1",
        filename="art.bin",
        created_at="2026-09-05T00:00:00Z",
        sha256="expected-different-sha256",
        size_bytes=64,
        state=BackupState.VALID,
    )

    validator = RecoveryValidator(
        backup_directory=tmp_path,
        storage_root=tmp_path,
        staging_manager=RecoveryStagingManager(tmp_path),
        database_restorer=DatabaseRestorer(),
    )

    with pytest.raises(RecoveryValidationError, match="Outer artifact SHA-256 mismatch"):
        validator.verify_envelope(art_file, meta)


def test_validator_verify_checksums_valid(tmp_path: Path) -> None:
    """Verify checksum verification passes when all file digests match checksums.json."""
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir(parents=True, exist_ok=True)

    sample_file = extracted_root / "data.txt"
    sample_file.write_bytes(b"sample payload")
    sample_sha, _ = RecoveryCryptoManager.compute_sha256(sample_file)

    manifest = ChecksumManifest(
        backup_id="bck-1",
        created_at="2026-09-05T00:00:00Z",
        entries=[
            ChecksumManifestEntry(
                path="data.txt",
                sha256=sample_sha,
                size_bytes=len(b"sample payload"),
            )
        ],
    )

    (extracted_root / "checksums.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )

    validator = RecoveryValidator(
        backup_directory=tmp_path,
        storage_root=tmp_path,
        staging_manager=RecoveryStagingManager(tmp_path),
        database_restorer=DatabaseRestorer(),
    )

    is_valid, count, errors = validator.verify_checksums(extracted_root)
    assert is_valid is True
    assert count == 1
    assert len(errors) == 0


def test_validator_verify_checksums_mismatch(tmp_path: Path) -> None:
    """Verify checksum verification flags mismatched hashes."""
    extracted_root = tmp_path / "extracted"
    extracted_root.mkdir(parents=True, exist_ok=True)

    sample_file = extracted_root / "data.txt"
    sample_file.write_bytes(b"altered payload")

    manifest = ChecksumManifest(
        backup_id="bck-1",
        created_at="2026-09-05T00:00:00Z",
        entries=[
            ChecksumManifestEntry(
                path="data.txt",
                sha256="wrong-expected-sha256",
                size_bytes=len(b"altered payload"),
            )
        ],
    )

    (extracted_root / "checksums.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )

    validator = RecoveryValidator(
        backup_directory=tmp_path,
        storage_root=tmp_path,
        staging_manager=RecoveryStagingManager(tmp_path),
        database_restorer=DatabaseRestorer(),
    )

    is_valid, _count, errors = validator.verify_checksums(extracted_root)
    assert is_valid is False
    assert len(errors) == 1
    assert "Checksum mismatch" in errors[0]
