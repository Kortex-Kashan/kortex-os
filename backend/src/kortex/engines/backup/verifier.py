"""KORTEX Backup Engine verification subsystem.

Phase 7 — Production Hardening — Backup Engine.
Performs structural, cryptographic, checksum, and database integrity
verification on .kortex-backup archives.
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from kortex.engines.backup.constants import (
    BACKUP_EXTENSION,
    CURRENT_BACKUP_FORMAT_VERSION,
    BackupState,
)
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.interfaces import IBackupRepository, IBackupVerifier
from kortex.engines.backup.models import (
    BackupManifest,
    ChecksumManifest,
    VerifyBackupRequest,
    VerifyBackupResponse,
)

logger = logging.getLogger("kortex.engines.backup.verifier")


class BackupVerifier(IBackupVerifier):
    """Verifies the integrity, authenticity, and structure of backup artifacts."""

    def verify_artifact(
        self,
        request: VerifyBackupRequest,
        repository: IBackupRepository,
        encryption_key: bytes | None,
    ) -> VerifyBackupResponse:
        """Verify structural completeness, file checksums, and envelope authentication."""
        backup_id = request.backup_id
        meta = repository.get_metadata(backup_id) if hasattr(repository, "get_metadata") else None
        filename = meta.filename if meta else f"{backup_id}{BACKUP_EXTENSION}"

        try:
            artifact_path = repository.resolve_artifact_path(filename)
        except Exception as exc:
            return VerifyBackupResponse(
                backup_id=backup_id,
                is_valid=False,
                state=BackupState.FAILED,
                format_version=CURRENT_BACKUP_FORMAT_VERSION,
                checksum_verified=False,
                encryption_verified=False,
                schema_compatible=False,
                error_message=f"Path resolution failed: {exc}",
            )

        if not artifact_path.is_file():
            return VerifyBackupResponse(
                backup_id=backup_id,
                is_valid=False,
                state=BackupState.FAILED,
                format_version=CURRENT_BACKUP_FORMAT_VERSION,
                checksum_verified=False,
                encryption_verified=False,
                schema_compatible=False,
                error_message=f"Artifact file not found: '{artifact_path}'.",
            )

        # 1. Digest check against sidecar
        actual_sha, _ = BackupCryptoManager.compute_sha256(artifact_path)
        if meta and meta.sha256 and actual_sha != meta.sha256:
            return VerifyBackupResponse(
                backup_id=backup_id,
                is_valid=False,
                state=BackupState.FAILED,
                format_version=CURRENT_BACKUP_FORMAT_VERSION,
                checksum_verified=False,
                encryption_verified=False,
                schema_compatible=False,
                error_message=f"Artifact digest mismatch: expected {meta.sha256}, got {actual_sha}.",
            )

        # Read payload bytes
        try:
            with artifact_path.open("rb") as f:
                raw_bytes = f.read()
        except OSError as exc:
            return VerifyBackupResponse(
                backup_id=backup_id,
                is_valid=False,
                state=BackupState.FAILED,
                format_version=CURRENT_BACKUP_FORMAT_VERSION,
                checksum_verified=False,
                encryption_verified=False,
                schema_compatible=False,
                error_message=f"Failed to read artifact: {exc}",
            )

        # 2. Determine if payload is encrypted
        is_encrypted = meta.is_encrypted if meta is not None else not raw_bytes.startswith(b"PK\x03\x04")

        unencrypted_zip_bytes: bytes
        encryption_verified = False

        if is_encrypted:
            if not encryption_key or len(encryption_key) != 32:
                return VerifyBackupResponse(
                    backup_id=backup_id,
                    is_valid=False,
                    state=BackupState.VALID,  # The backup may be valid, but cannot be verified without key
                    format_version=CURRENT_BACKUP_FORMAT_VERSION,
                    checksum_verified=False,
                    encryption_verified=False,
                    schema_compatible=False,
                    error_message="Cannot verify encrypted backup: valid 32-byte decryption key not provided.",
                )

            crypto = BackupCryptoManager(key=encryption_key, encryption_required=True)
            try:
                # To decrypt, parse sealed bytes: nonce(12) + ciphertext + tag(16)
                nonce = raw_bytes[:12]
                ciphertext = raw_bytes[12:-16]
                tag = raw_bytes[-16:]
                key_id = meta.key_id if meta and meta.key_id else "kortex-master-key"
                associated_data = f"kortex-backup-v1:{key_id}".encode()

                unencrypted_zip_bytes = crypto._crypto.decrypt_aes_gcm(
                    nonce=nonce,
                    ciphertext=ciphertext,
                    tag=tag,
                    key=encryption_key,
                    associated_data=associated_data,
                )
                encryption_verified = True
            except Exception as exc:
                return VerifyBackupResponse(
                    backup_id=backup_id,
                    is_valid=False,
                    state=BackupState.FAILED,
                    format_version=CURRENT_BACKUP_FORMAT_VERSION,
                    checksum_verified=False,
                    encryption_verified=False,
                    schema_compatible=False,
                    error_message=f"Cryptographic authentication failed: {exc}",
                )
        else:
            unencrypted_zip_bytes = raw_bytes
            encryption_verified = True

        # 3. Inspect ZIP structure
        try:
            zf = zipfile.ZipFile(io.BytesIO(unencrypted_zip_bytes))
        except zipfile.BadZipFile as exc:
            return VerifyBackupResponse(
                backup_id=backup_id,
                is_valid=False,
                state=BackupState.FAILED,
                format_version=CURRENT_BACKUP_FORMAT_VERSION,
                checksum_verified=False,
                encryption_verified=encryption_verified,
                schema_compatible=False,
                error_message=f"Corrupted ZIP container: {exc}",
            )

        with zf:
            namelist = zf.namelist()

            # Path safety checks inside ZIP
            for entry_name in namelist:
                if entry_name.startswith("/") or entry_name.startswith("\\") or ".." in entry_name:
                    return VerifyBackupResponse(
                        backup_id=backup_id,
                        is_valid=False,
                        state=BackupState.FAILED,
                        format_version=CURRENT_BACKUP_FORMAT_VERSION,
                        checksum_verified=False,
                        encryption_verified=encryption_verified,
                        schema_compatible=False,
                        error_message=f"Unsafe path in ZIP entry: '{entry_name}'.",
                    )

            if "manifest.json" not in namelist:
                return VerifyBackupResponse(
                    backup_id=backup_id,
                    is_valid=False,
                    state=BackupState.FAILED,
                    format_version=CURRENT_BACKUP_FORMAT_VERSION,
                    checksum_verified=False,
                    encryption_verified=encryption_verified,
                    schema_compatible=False,
                    error_message="Missing required 'manifest.json' in archive.",
                )

            if "checksums.json" not in namelist:
                return VerifyBackupResponse(
                    backup_id=backup_id,
                    is_valid=False,
                    state=BackupState.FAILED,
                    format_version=CURRENT_BACKUP_FORMAT_VERSION,
                    checksum_verified=False,
                    encryption_verified=encryption_verified,
                    schema_compatible=False,
                    error_message="Missing required 'checksums.json' in archive.",
                )

            # Read and parse manifest
            try:
                manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
                manifest = BackupManifest.model_validate(manifest_data)
            except Exception as exc:
                return VerifyBackupResponse(
                    backup_id=backup_id,
                    is_valid=False,
                    state=BackupState.FAILED,
                    format_version=CURRENT_BACKUP_FORMAT_VERSION,
                    checksum_verified=False,
                    encryption_verified=encryption_verified,
                    schema_compatible=False,
                    error_message=f"Invalid manifest.json: {exc}",
                )

            if manifest.format_version > CURRENT_BACKUP_FORMAT_VERSION:
                error_msg = (
                    f"Unsupported format version {manifest.format_version} "
                    f"(supported <= {CURRENT_BACKUP_FORMAT_VERSION})."
                )
                return VerifyBackupResponse(
                    backup_id=backup_id,
                    is_valid=False,
                    state=BackupState.FAILED,
                    format_version=manifest.format_version,
                    checksum_verified=False,
                    encryption_verified=encryption_verified,
                    schema_compatible=False,
                    error_message=error_msg,
                )

            # Read and verify checksums
            try:
                checksums_data = json.loads(zf.read("checksums.json").decode("utf-8"))
                checksum_manifest = ChecksumManifest.model_validate(checksums_data)
            except Exception as exc:
                return VerifyBackupResponse(
                    backup_id=backup_id,
                    is_valid=False,
                    state=BackupState.FAILED,
                    format_version=manifest.format_version,
                    checksum_verified=False,
                    encryption_verified=encryption_verified,
                    schema_compatible=False,
                    error_message=f"Invalid checksums.json: {exc}",
                )

            import hashlib

            for entry in checksum_manifest.entries:
                if entry.path not in namelist:
                    return VerifyBackupResponse(
                        backup_id=backup_id,
                        is_valid=False,
                        state=BackupState.FAILED,
                        format_version=manifest.format_version,
                        checksum_verified=False,
                        encryption_verified=encryption_verified,
                        schema_compatible=False,
                        error_message=f"Checksum manifest lists missing file '{entry.path}'.",
                    )

                entry_bytes = zf.read(entry.path)
                entry_sha = hashlib.sha256(entry_bytes).hexdigest()
                if entry_sha != entry.sha256:
                    error_msg = f"Checksum mismatch for '{entry.path}': expected {entry.sha256}, got {entry_sha}."
                    return VerifyBackupResponse(
                        backup_id=backup_id,
                        is_valid=False,
                        state=BackupState.FAILED,
                        format_version=manifest.format_version,
                        checksum_verified=False,
                        encryption_verified=encryption_verified,
                        schema_compatible=False,
                        error_message=error_msg,
                    )

            # Check database snapshot integrity if present
            if "database/kortex_snapshot.db" in namelist:
                db_bytes = zf.read("database/kortex_snapshot.db")
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
                    tmp_db.write(db_bytes)
                    tmp_db_path = Path(tmp_db.name)

                try:
                    conn = sqlite3.connect(tmp_db_path)
                    try:
                        cur = conn.cursor()
                        cur.execute("PRAGMA integrity_check;")
                        rows = cur.fetchall()
                        if not rows or rows[0][0] != "ok":
                            errors = "; ".join(r[0] for r in rows)
                            return VerifyBackupResponse(
                                backup_id=backup_id,
                                is_valid=False,
                                state=BackupState.FAILED,
                                format_version=manifest.format_version,
                                checksum_verified=True,
                                encryption_verified=encryption_verified,
                                schema_compatible=False,
                                error_message=f"Database snapshot corrupted: {errors}",
                            )
                    finally:
                        conn.close()
                finally:
                    tmp_db_path.unlink(missing_ok=True)

        return VerifyBackupResponse(
            backup_id=backup_id,
            is_valid=True,
            state=BackupState.VALID,
            format_version=manifest.format_version,
            checksum_verified=True,
            encryption_verified=encryption_verified,
            schema_compatible=True,
            error_message=None,
        )
