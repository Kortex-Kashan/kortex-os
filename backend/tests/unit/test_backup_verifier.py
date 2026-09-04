"""Unit tests for Backup Engine verification subsystem."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kortex.engines.backup.constants import BackupComponentType, BackupScope, BackupState
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.models import (
    ManifestComponentEntry,
    VerifyBackupRequest,
)
from kortex.engines.backup.packager import BackupPackager
from kortex.engines.backup.repository import BackupRepository
from kortex.engines.backup.verifier import BackupVerifier


def _create_test_backup(
    repo: BackupRepository,
    crypto: BackupCryptoManager,
    backup_id: str,
    tmp_path: Path,
) -> tuple[Path, bytes]:
    """Helper to assemble a valid test backup."""
    # Create valid SQLite DB
    db_path = tmp_path / f"{backup_id}.db"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute("CREATE TABLE t (x INT);")
        conn.execute("INSERT INTO t VALUES (1), (2);")
    conn.close()

    db_entry = ManifestComponentEntry(
        name="database",
        component_type=BackupComponentType.DATABASE,
        relative_path="database/kortex_snapshot.db",
        sha256=BackupCryptoManager.compute_sha256(db_path)[0],
        size_bytes=db_path.stat().st_size,
    )

    packager = BackupPackager(crypto)
    tmp_unencrypted = tmp_path / f"{backup_id}_raw.zip"
    tmp_final = repo.resolve_artifact_path(f"{backup_id}.kortex-backup.tmp")
    final_target = repo.resolve_artifact_path(f"{backup_id}.kortex-backup")

    _manifest, meta = packager.assemble_backup(
        backup_id=backup_id,
        instance_id="inst-ver",
        kortex_version="1.0.0",
        scope=BackupScope.FULL_INSTANCE,
        created_at_iso="2026-09-04T12:00:00Z",
        db_snapshot_path=db_path,
        db_manifest_entry=db_entry,
        schema_revision="rev_1",
        storage_files=[],
        storage_checksums=[],
        tmp_unencrypted_zip=tmp_unencrypted,
        tmp_final_path=tmp_final,
        final_target_path=final_target,
    )
    repo.save_metadata(meta)
    return final_target, crypto._key  # type: ignore[return-value]


def test_verifier_valid_backup(tmp_path: Path) -> None:
    """Verify clean pass on valid backup."""
    key = b"\x88" * 32
    crypto = BackupCryptoManager(key=key, key_id="v-key")
    repo = BackupRepository(tmp_path / "backups")

    _art_path, _ = _create_test_backup(repo, crypto, "b-valid", tmp_path)

    verifier = BackupVerifier()
    res = verifier.verify_artifact(
        request=VerifyBackupRequest(backup_id="b-valid"),
        repository=repo,
        encryption_key=key,
    )

    assert res.is_valid is True
    assert res.checksum_verified is True
    assert res.encryption_verified is True
    assert res.schema_compatible is True
    assert res.state == BackupState.VALID


def test_verifier_missing_key(tmp_path: Path) -> None:
    """Verify verification fails when decryption key is missing."""
    key = b"\x88" * 32
    crypto = BackupCryptoManager(key=key, key_id="v-key")
    repo = BackupRepository(tmp_path / "backups")

    _art_path, _ = _create_test_backup(repo, crypto, "b-nokey", tmp_path)

    verifier = BackupVerifier()
    res = verifier.verify_artifact(
        request=VerifyBackupRequest(backup_id="b-nokey"),
        repository=repo,
        encryption_key=None,
    )

    assert res.is_valid is False
    assert "Cannot verify encrypted backup" in (res.error_message or "")


def test_verifier_tampered_payload(tmp_path: Path) -> None:
    """Verify verification detects tampered ciphertext."""
    key = b"\x88" * 32
    crypto = BackupCryptoManager(key=key, key_id="v-key")
    repo = BackupRepository(tmp_path / "backups")

    art_path, _ = _create_test_backup(repo, crypto, "b-tamper", tmp_path)

    # Tamper payload
    data = bytearray(art_path.read_bytes())
    data[20] ^= 0x55
    art_path.write_bytes(data)

    verifier = BackupVerifier()
    res = verifier.verify_artifact(
        request=VerifyBackupRequest(backup_id="b-tamper"),
        repository=repo,
        encryption_key=key,
    )

    assert res.is_valid is False
    assert res.state == BackupState.FAILED


def test_verifier_nonexistent_backup(tmp_path: Path) -> None:
    """Verify handling when backup does not exist."""
    repo = BackupRepository(tmp_path / "backups")
    verifier = BackupVerifier()

    res = verifier.verify_artifact(
        request=VerifyBackupRequest(backup_id="nonexistent"),
        repository=repo,
        encryption_key=b"\x01" * 32,
    )
    assert res.is_valid is False
    assert "Artifact file not found" in (res.error_message or "")
