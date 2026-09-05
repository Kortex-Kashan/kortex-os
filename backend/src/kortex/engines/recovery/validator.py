"""KORTEX Recovery Engine multi-tier artifact and staging validator.

Phase 7 — Production Hardening — Recovery Engine.
Validates cryptographic envelopes, manifest integrity, file checksums,
SQLite page B-trees, schema compatibility, and referential consistency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from pathlib import Path

from kortex.engines.backup.constants import (
    BACKUP_EXTENSION,
    BACKUP_METADATA_EXTENSION,
    CHUNK_SIZE_BYTES,
)
from kortex.engines.backup.models import BackupMetadata, ChecksumManifest
from kortex.engines.recovery.crypto import RecoveryCryptoManager
from kortex.engines.recovery.database_restorer import DatabaseRestorer
from kortex.engines.recovery.exceptions import (
    RecoveryArtifactCorruptError,
    RecoveryNotFoundError,
    RecoveryValidationError,
)
from kortex.engines.recovery.models import (
    VerifyRecoveryRequest,
    VerifyRecoveryResponse,
)
from kortex.engines.recovery.staging import RecoveryStagingManager
from kortex.engines.recovery.storage_restorer import StorageRestorer

logger = logging.getLogger("kortex.engines.recovery.validator")


class RecoveryValidator:
    """Coordinates multi-tier verification for backup artifacts and staged state."""

    def __init__(
        self,
        backup_directory: str | Path,
        storage_root: str | Path,
        staging_manager: RecoveryStagingManager,
        database_restorer: DatabaseRestorer,
        crypto_manager: RecoveryCryptoManager | None = None,
    ) -> None:
        self._backup_dir = Path(backup_directory).resolve()
        self._storage_root = Path(storage_root).resolve()
        self._staging_manager = staging_manager
        self._db_restorer = database_restorer
        self._crypto = crypto_manager or RecoveryCryptoManager()
        self._storage_restorer = StorageRestorer(self._storage_root)

    def locate_artifact(self, backup_id: str) -> tuple[Path, BackupMetadata | None]:
        """Discover target artifact and optional sidecar metadata in backup directory."""
        filename = f"{backup_id}{BACKUP_EXTENSION}"
        artifact_path = self._backup_dir / filename

        if not artifact_path.is_file():
            # Try finding without adding extension
            candidate = self._backup_dir / backup_id
            if candidate.is_file():
                artifact_path = candidate
            else:
                raise RecoveryNotFoundError(
                    f"Backup artifact not found for identifier: '{backup_id}' in '{self._backup_dir}'."
                )

        sidecar_path = artifact_path.with_suffix(BACKUP_METADATA_EXTENSION)
        if not sidecar_path.is_file():
            alt_sidecar = artifact_path.with_name(artifact_path.name + BACKUP_METADATA_EXTENSION)
            if alt_sidecar.is_file():
                sidecar_path = alt_sidecar

        sidecar_meta: BackupMetadata | None = None
        if sidecar_path.is_file():
            try:
                data = json.loads(sidecar_path.read_text(encoding="utf-8"))
                sidecar_meta = BackupMetadata.model_validate(data)
            except Exception as exc:
                logger.warning("Could not read sidecar metadata from '%s': %s", sidecar_path, exc)

        return artifact_path, sidecar_meta

    def verify_envelope(self, artifact_path: Path, sidecar_meta: BackupMetadata | None) -> None:
        """Verify outer artifact SHA-256 matches sidecar metadata and file is not truncated."""
        if not artifact_path.is_file():
            raise RecoveryNotFoundError(f"Artifact does not exist: '{artifact_path}'")

        file_size = artifact_path.stat().st_size
        if file_size < 28:
            raise RecoveryArtifactCorruptError(
                f"Artifact '{artifact_path}' is smaller than minimum encrypted envelope size (28 bytes)."
            )

        if sidecar_meta is not None:
            actual_sha, _ = RecoveryCryptoManager.compute_sha256(artifact_path)
            if actual_sha != sidecar_meta.sha256:
                raise RecoveryValidationError(
                    f"Outer artifact SHA-256 mismatch: expected {sidecar_meta.sha256}, got {actual_sha}."
                )

    @staticmethod
    def verify_checksums(extracted_root: Path) -> tuple[bool, int, list[str]]:
        """Verify all extracted files against checksums.json inventory.

        Returns:
            Tuple of (is_valid, files_verified_count, errors).
        """
        checksums_file = extracted_root / "checksums.json"
        if not checksums_file.is_file():
            return False, 0, ["checksums.json not found in extracted archive."]

        try:
            data = json.loads(checksums_file.read_text(encoding="utf-8"))
            manifest = ChecksumManifest.model_validate(data)
        except Exception as exc:
            return False, 0, [f"Failed to parse checksums.json: {exc}"]

        errors: list[str] = []
        verified_count = 0

        for entry in manifest.entries:
            file_path = extracted_root / entry.path
            if not file_path.is_file():
                errors.append(f"Missing file declared in checksums.json: '{entry.path}'")
                continue

            hasher = hashlib.sha256()
            with file_path.open("rb") as f:
                while chunk := f.read(CHUNK_SIZE_BYTES):
                    hasher.update(chunk)

            actual_sha = hasher.hexdigest()
            if actual_sha != entry.sha256:
                errors.append(f"Checksum mismatch for '{entry.path}': expected {entry.sha256}, got {actual_sha}.")
            else:
                verified_count += 1

        return len(errors) == 0, verified_count, errors

    async def verify_backup(self, request: VerifyRecoveryRequest) -> VerifyRecoveryResponse:
        """Perform non-destructive end-to-end verification in scratch buffer."""
        artifact_path, sidecar_meta = self.locate_artifact(request.backup_id)

        # 1. Envelope check
        try:
            self.verify_envelope(artifact_path, sidecar_meta)
            envelope_ok = True
        except Exception as exc:
            return VerifyRecoveryResponse(
                backup_id=request.backup_id,
                is_valid=False,
                checksum_verified=False,
                encryption_verified=False,
                schema_compatible=False,
                database_integrity_passed=False,
                storage_referential_integrity_passed=False,
                has_sufficient_disk_space=False,
                error_message=f"Envelope verification failed: {exc}",
            )

        # Parse key override if provided
        key_bytes = None
        if request.encryption_key:
            key_bytes = RecoveryCryptoManager.parse_key_bytes(request.encryption_key)

        # 2. Scratch Decryption & Extraction
        with tempfile.TemporaryDirectory(prefix="kortex_verify_") as tmp_dir_str:
            scratch_root = Path(tmp_dir_str)
            raw_zip = scratch_root / "raw.zip"
            extracted_dir = scratch_root / "extracted"

            # Decrypt
            try:
                self._crypto.decrypt_file(
                    source_path=artifact_path,
                    dest_path=raw_zip,
                    key_override=key_bytes,
                )
                crypto_ok = True
            except Exception as exc:
                return VerifyRecoveryResponse(
                    backup_id=request.backup_id,
                    is_valid=False,
                    checksum_verified=False,
                    encryption_verified=False,
                    schema_compatible=False,
                    database_integrity_passed=False,
                    storage_referential_integrity_passed=False,
                    has_sufficient_disk_space=False,
                    error_message=f"Decryption failed: {exc}",
                )

            # Extract safely
            try:
                self._staging_manager.extract_zip_safely(raw_zip, extracted_dir)
            except Exception as exc:
                return VerifyRecoveryResponse(
                    backup_id=request.backup_id,
                    is_valid=False,
                    checksum_verified=False,
                    encryption_verified=crypto_ok,
                    schema_compatible=False,
                    database_integrity_passed=False,
                    storage_referential_integrity_passed=False,
                    has_sufficient_disk_space=False,
                    error_message=f"Archive extraction failed: {exc}",
                )

            # 3. Checksums check
            cs_ok, _file_count, cs_errors = self.verify_checksums(extracted_dir)
            if not cs_ok:
                return VerifyRecoveryResponse(
                    backup_id=request.backup_id,
                    is_valid=False,
                    checksum_verified=False,
                    encryption_verified=crypto_ok,
                    schema_compatible=False,
                    database_integrity_passed=False,
                    storage_referential_integrity_passed=False,
                    has_sufficient_disk_space=False,
                    error_message=f"Checksum verification failed: {'; '.join(cs_errors)}",
                )

            # 4. Database Integrity & Schema Compatibility
            staged_db = extracted_dir / "database" / "kortex_snapshot.db"
            db_valid, db_msg, snap_rev = self._db_restorer.validate_sqlite_file(staged_db)
            if not db_valid:
                return VerifyRecoveryResponse(
                    backup_id=request.backup_id,
                    is_valid=False,
                    checksum_verified=True,
                    encryption_verified=crypto_ok,
                    schema_compatible=False,
                    database_integrity_passed=False,
                    storage_referential_integrity_passed=False,
                    has_sufficient_disk_space=False,
                    error_message=f"Staged database integrity check failed: {db_msg}",
                )

            app_rev = self._db_restorer.get_app_schema_head()
            is_compat, req_migration, compat_msg = self._db_restorer.evaluate_schema_compatibility(snap_rev, app_rev)

            # 5. Referential check
            staged_storage = extracted_dir / "storage"
            ref_ok, missing_refs, warnings = self._storage_restorer.verify_referential_consistency(
                staged_db, staged_storage
            )

            # 6. Disk capacity calculation
            art_size = artifact_path.stat().st_size
            zip_size = raw_zip.stat().st_size
            db_size = staged_db.stat().st_size if staged_db.is_file() else 0
            storage_size = (
                sum(f.stat().st_size for f in staged_storage.rglob("*") if f.is_file())
                if staged_storage.is_dir()
                else 0
            )
            live_db = self._storage_root / "kortex_local.db"
            live_db_size = live_db.stat().st_size if live_db.is_file() else 0
            live_storage_size = (
                sum(f.stat().st_size for f in self._storage_root.rglob("*") if f.is_file())
                if self._storage_root.is_dir()
                else 0
            )

            req_bytes, avail_bytes = 0, 0
            has_space = True
            try:
                req_bytes, avail_bytes = self._staging_manager.preflight_disk_capacity(
                    artifact_size=art_size,
                    uncompressed_payload_size=zip_size,
                    extracted_db_size=db_size,
                    extracted_storage_size=storage_size,
                    live_db_size=live_db_size,
                    live_storage_size=live_storage_size,
                    target_volume_dir=self._storage_root,
                )
            except Exception:
                has_space = False

            is_valid = envelope_ok and crypto_ok and cs_ok and db_valid and is_compat and ref_ok and has_space

            err_msg = None
            if not is_valid:
                if not is_compat:
                    err_msg = f"Schema incompatible: {compat_msg}"
                elif not ref_ok:
                    err_msg = f"Referential consistency failed: missing {len(missing_refs)} files."
                elif not has_space:
                    err_msg = "Insufficient disk space for recovery."

            return VerifyRecoveryResponse(
                backup_id=request.backup_id,
                is_valid=is_valid,
                checksum_verified=cs_ok,
                encryption_verified=crypto_ok,
                schema_compatible=is_compat,
                database_integrity_passed=db_valid,
                storage_referential_integrity_passed=ref_ok,
                required_free_bytes=req_bytes,
                available_free_bytes=avail_bytes,
                has_sufficient_disk_space=has_space,
                schema_revision=snap_rev,
                app_schema_revision=app_rev,
                requires_staged_migration=req_migration,
                error_message=err_msg,
                warnings=warnings,
            )
