"""KORTEX Backup Engine archive packaging and atomic finalization subsystem.

Phase 7 — Production Hardening — Backup Engine.
Assembles `.kortex-backup` ZIP containers in a temporary workspace,
computes deterministic checksums, applies AES-256-GCM envelope protection,
and atomically finalizes the artifact into the canonical backup root.
"""

from __future__ import annotations

import datetime
import logging
import os
import zipfile
from pathlib import Path
from typing import Any

from kortex.engines.backup.constants import (
    CURRENT_BACKUP_FORMAT_VERSION,
    CURRENT_ENGINE_VERSION,
    BackupComponentType,
    BackupScope,
    BackupState,
)
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.exceptions import BackupStorageError
from kortex.engines.backup.models import (
    BackupManifest,
    BackupMetadata,
    ChecksumManifest,
    ChecksumManifestEntry,
    CompressionMetadata,
    EncryptionMetadata,
    ManifestComponentEntry,
)

logger = logging.getLogger("kortex.engines.backup.packager")

# Deterministic ZIP entry timestamp to eliminate timestamp jitter
_DETERMINISTIC_ZIP_DATE = (2026, 1, 1, 0, 0, 0)


class BackupPackager:
    """Assembles and finalizes atomic .kortex-backup containers."""

    def __init__(self, crypto_manager: BackupCryptoManager) -> None:
        self._crypto = crypto_manager

    def assemble_backup(
        self,
        backup_id: str,
        instance_id: str,
        kortex_version: str,
        scope: BackupScope,
        created_at_iso: str,
        db_snapshot_path: Path,
        db_manifest_entry: ManifestComponentEntry,
        schema_revision: str | None,
        storage_files: list[tuple[Path, str]],
        storage_checksums: list[ChecksumManifestEntry],
        tmp_unencrypted_zip: Path,
        tmp_final_path: Path,
        final_target_path: Path,
        extra_metadata: dict[str, Any] | None = None,
    ) -> tuple[BackupManifest, BackupMetadata]:
        """Assemble the complete backup container, encrypt, and atomically finalize.

        Returns:
            Tuple of (BackupManifest, BackupMetadata).
        """
        all_checksum_entries: list[ChecksumManifestEntry] = []

        # 1. Add database checksum
        all_checksum_entries.append(
            ChecksumManifestEntry(
                path=db_manifest_entry.relative_path,
                sha256=db_manifest_entry.sha256,
                size_bytes=db_manifest_entry.size_bytes,
            )
        )

        # 2. Add storage checksums
        all_checksum_entries.extend(storage_checksums)

        # 3. Assemble constituent components
        components: list[ManifestComponentEntry] = [db_manifest_entry]

        storage_size = sum(e.size_bytes for e in storage_checksums)
        storage_entry = ManifestComponentEntry(
            name="storage",
            component_type=BackupComponentType.STORAGE,
            relative_path="storage",
            sha256="",  # Aggregate indicator
            size_bytes=storage_size,
            item_count=len(storage_files),
            metadata={"file_count": len(storage_files)},
        )
        components.append(storage_entry)

        # 4. Write raw ZIP container
        tmp_unencrypted_zip.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(tmp_unencrypted_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                # Add database snapshot
                zinfo_db = zipfile.ZipInfo(filename=db_manifest_entry.relative_path, date_time=_DETERMINISTIC_ZIP_DATE)
                zinfo_db.compress_type = zipfile.ZIP_DEFLATED
                with db_snapshot_path.open("rb") as df:
                    zf.writestr(zinfo_db, df.read())

                # Add storage files
                for abs_path, arc_path in storage_files:
                    norm_arc = arc_path.replace("\\", "/")
                    zinfo_f = zipfile.ZipInfo(filename=norm_arc, date_time=_DETERMINISTIC_ZIP_DATE)
                    zinfo_f.compress_type = zipfile.ZIP_DEFLATED
                    try:
                        with abs_path.open("rb") as sf:
                            zf.writestr(zinfo_f, sf.read())
                    except OSError as exc:
                        logger.warning("Could not bundle file '%s': %s", abs_path, exc)

                # Write checksums.json
                checksum_manifest = ChecksumManifest(
                    format_version=CURRENT_BACKUP_FORMAT_VERSION,
                    backup_id=backup_id,
                    created_at=created_at_iso,
                    entries=all_checksum_entries,
                )
                zinfo_cs = zipfile.ZipInfo(filename="checksums.json", date_time=_DETERMINISTIC_ZIP_DATE)
                zinfo_cs.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zinfo_cs, checksum_manifest.model_dump_json(indent=2).encode("utf-8"))

                # Write manifest.json
                # Initial manifest without encryption
                manifest = BackupManifest(
                    format_version=CURRENT_BACKUP_FORMAT_VERSION,
                    backup_id=backup_id,
                    created_at=created_at_iso,
                    engine_version=CURRENT_ENGINE_VERSION,
                    kortex_version=kortex_version,
                    scope=scope,
                    instance_id=instance_id,
                    components=components,
                    encryption=None,
                    compression=CompressionMetadata(algorithm="ZIP_DEFLATED", level=6),
                    state=BackupState.VALID,
                    database_schema_revision=schema_revision,
                    total_size_bytes=0,
                    metadata=extra_metadata or {},
                )
                zinfo_mf = zipfile.ZipInfo(filename="manifest.json", date_time=_DETERMINISTIC_ZIP_DATE)
                zinfo_mf.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zinfo_mf, manifest.model_dump_json(indent=2).encode("utf-8"))

        except Exception as exc:
            if tmp_unencrypted_zip.exists():
                tmp_unencrypted_zip.unlink(missing_ok=True)
            raise BackupStorageError(f"Failed to assemble ZIP archive: {exc}") from exc

        # 5. Encrypt or Finalize
        encryption_meta: EncryptionMetadata | None = None
        if self._crypto.is_key_available:
            encryption_meta = self._crypto.encrypt_file(
                source_path=tmp_unencrypted_zip,
                dest_path=tmp_final_path,
            )
            # Remove raw ZIP from temp
            tmp_unencrypted_zip.unlink(missing_ok=True)
        else:
            # Fallback only if crypto manager allows unencrypted (which fails closed if required)
            tmp_unencrypted_zip.replace(tmp_final_path)

        # 6. Verify and compute final file statistics
        final_sha256, final_size_bytes = BackupCryptoManager.compute_sha256(tmp_final_path)

        # Update manifest with final details
        final_manifest = BackupManifest(
            format_version=CURRENT_BACKUP_FORMAT_VERSION,
            backup_id=backup_id,
            created_at=created_at_iso,
            engine_version=CURRENT_ENGINE_VERSION,
            kortex_version=kortex_version,
            scope=scope,
            instance_id=instance_id,
            components=components,
            encryption=encryption_meta,
            compression=CompressionMetadata(algorithm="ZIP_DEFLATED", level=6),
            state=BackupState.VALID,
            database_schema_revision=schema_revision,
            total_size_bytes=final_size_bytes,
            metadata=extra_metadata or {},
        )

        # 7. Atomic rename to final target
        final_target_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_final_path, final_target_path)

        finalized_at_iso = datetime.datetime.now(datetime.UTC).isoformat()

        # 8. Create sidecar metadata
        metadata = BackupMetadata(
            backup_id=backup_id,
            state=BackupState.VALID,
            created_at=created_at_iso,
            finalized_at=finalized_at_iso,
            scope=scope,
            filename=final_target_path.name,
            file_size_bytes=final_size_bytes,
            sha256=final_sha256,
            is_encrypted=encryption_meta is not None,
            key_id=encryption_meta.key_id if encryption_meta else None,
            database_schema_revision=schema_revision,
            component_counts={
                "database_pages": db_manifest_entry.size_bytes // 4096 if db_manifest_entry.size_bytes else 0,
                "storage_files": len(storage_files),
            },
            error_message=None,
        )

        return final_manifest, metadata
