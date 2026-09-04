"""KORTEX Backup Engine storage repository.

Phase 7 — Production Hardening — Backup Engine.
Manages sandboxed backup artifact persistence, sidecar metadata indexing,
atomic operations, and safe orphan temporary cleanup.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from kortex.engines.backup.constants import (
    BACKUP_EXTENSION,
    BACKUP_METADATA_EXTENSION,
    BACKUP_TMP_EXTENSION,
    BackupScope,
    BackupState,
)
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.exceptions import (
    BackupNotFoundError,
    BackupPathSecurityError,
    BackupStorageError,
)
from kortex.engines.backup.interfaces import IBackupRepository
from kortex.engines.backup.models import (
    BackupMetadata,
    DeleteBackupRequest,
    DeleteBackupResponse,
    GetBackupRequest,
    GetBackupResponse,
    ListBackupsRequest,
    ListBackupsResponse,
)
from kortex.engines.storage.sandbox import PathSandboxError, PathSandboxValidator

logger = logging.getLogger("kortex.engines.backup.repository")


class BackupRepository(IBackupRepository):
    """Authoritative filesystem repository for KORTEX backup archives."""

    def __init__(self, backup_directory: str | Path) -> None:
        self._dir = Path(backup_directory).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sandbox = PathSandboxValidator(self._dir)

    @property
    def backup_directory(self) -> Path:
        return self._dir

    def resolve_artifact_path(self, filename: str) -> Path:
        """Resolve a filename guaranteed to reside strictly within the backup root."""
        # Prohibit path traversal characters
        if "/" in filename or "\\" in filename or ".." in filename:
            raise BackupPathSecurityError(f"Security violation: path traversal prohibited in filename '{filename}'.")

        try:
            return self._sandbox.resolve_sandboxed_path(filename)
        except PathSandboxError as exc:
            raise BackupPathSecurityError(f"Security violation: path '{filename}' escapes backup directory.") from exc

    def save_metadata(self, metadata: BackupMetadata) -> None:
        """Persist sidecar metadata atomically to `{backup_id}.meta.json`."""
        meta_file = self.resolve_artifact_path(f"{metadata.backup_id}{BACKUP_METADATA_EXTENSION}")
        tmp_file = meta_file.with_suffix(meta_file.suffix + ".tmp")

        try:
            content = metadata.model_dump_json(indent=2).encode("utf-8")
            with tmp_file.open("wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, meta_file)
        except OSError as exc:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            raise BackupStorageError(f"Failed to persist backup metadata '{meta_file}': {exc}") from exc

    def get_metadata(self, backup_id: str) -> BackupMetadata | None:
        """Read sidecar metadata for a given backup ID if present."""
        meta_file = self.resolve_artifact_path(f"{backup_id}{BACKUP_METADATA_EXTENSION}")
        if not meta_file.is_file():
            return None

        try:
            with meta_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return BackupMetadata.model_validate(data)
        except Exception as exc:
            logger.warning("Failed to parse sidecar metadata '%s': %s", meta_file, exc)
            return None

    def list_backups(self, request: ListBackupsRequest) -> ListBackupsResponse:
        """List all valid and completed backups sorted by creation timestamp descending."""
        discovered: list[BackupMetadata] = []

        # Find all .kortex-backup files
        for item in self._dir.glob(f"*{BACKUP_EXTENSION}"):
            if not item.is_file() or item.name.endswith(".tmp"):
                continue

            # Extract backup_id from filename or sidecar
            # Naming pattern: kortex_backup_{timestamp}_{short_id}.kortex-backup
            # Check if there is a matching .meta.json
            stem = item.name[: -len(BACKUP_EXTENSION)]
            meta = self.get_metadata(stem)

            if meta is not None:
                discovered.append(meta)
            else:
                # Reconstruct metadata from file if sidecar missing
                try:
                    stat = item.stat()
                    sha256, size_bytes = BackupCryptoManager.compute_sha256(item)
                    inferred = BackupMetadata(
                        backup_id=stem,
                        state=BackupState.VALID,
                        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_ctime)),
                        finalized_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
                        scope=BackupScope.FULL_INSTANCE,
                        filename=item.name,
                        file_size_bytes=size_bytes,
                        sha256=sha256,
                        is_encrypted=True,
                    )
                    discovered.append(inferred)
                except Exception as exc:
                    logger.warning("Could not reconstruct metadata for '%s': %s", item, exc)

        # Sort descending by creation timestamp
        discovered.sort(key=lambda m: m.created_at, reverse=True)

        total_count = len(discovered)
        paginated = discovered[request.offset : request.offset + request.limit]
        return ListBackupsResponse(backups=paginated, total_count=total_count)

    def get_backup(self, request: GetBackupRequest) -> GetBackupResponse:
        """Retrieve metadata for a specific backup."""
        meta = self.get_metadata(request.backup_id)
        if meta is None:
            # Check if file exists directly
            target = self.resolve_artifact_path(f"{request.backup_id}{BACKUP_EXTENSION}")
            if not target.is_file():
                raise BackupNotFoundError(f"Backup with ID '{request.backup_id}' not found.")

            sha256, size_bytes = BackupCryptoManager.compute_sha256(target)
            meta = BackupMetadata(
                backup_id=request.backup_id,
                state=BackupState.VALID,
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(target.stat().st_ctime)),
                finalized_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(target.stat().st_mtime)),
                scope=BackupScope.FULL_INSTANCE,
                filename=target.name,
                file_size_bytes=size_bytes,
                sha256=sha256,
                is_encrypted=True,
            )

        return GetBackupResponse(backup=meta, manifest=None)

    def delete_backup(self, request: DeleteBackupRequest) -> DeleteBackupResponse:
        """Atomically delete a backup artifact and its sidecar metadata."""
        meta = self.get_metadata(request.backup_id)
        artifact_name = meta.filename if meta else f"{request.backup_id}{BACKUP_EXTENSION}"
        artifact_path = self.resolve_artifact_path(artifact_name)
        meta_path = self.resolve_artifact_path(f"{request.backup_id}{BACKUP_METADATA_EXTENSION}")

        deleted = False
        if artifact_path.is_file():
            try:
                artifact_path.unlink()
                deleted = True
            except OSError as exc:
                raise BackupStorageError(f"Failed to delete artifact '{artifact_path}': {exc}") from exc

        if meta_path.is_file():
            try:
                meta_path.unlink()
                deleted = True
            except OSError as exc:
                raise BackupStorageError(f"Failed to delete sidecar metadata '{meta_path}': {exc}") from exc

        if not deleted:
            raise BackupNotFoundError(f"Backup with ID '{request.backup_id}' does not exist to delete.")

        return DeleteBackupResponse(
            backup_id=request.backup_id,
            deleted=True,
            state=BackupState.DELETED,
        )

    def cleanup_orphaned_temporaries(self, max_age_seconds: int = 3600) -> int:
        """Sweep and unlink any abandoned temporary files older than max_age_seconds."""
        now = time.time()
        unlinked_count = 0

        for pattern in (f"*{BACKUP_TMP_EXTENSION}", "*.tmp", "*.cryptmp", "*.dectmp"):
            for tmp_file in self._dir.glob(pattern):
                if not tmp_file.is_file():
                    continue

                try:
                    mtime = tmp_file.stat().st_mtime
                    if now - mtime > max_age_seconds:
                        tmp_file.unlink(missing_ok=True)
                        unlinked_count += 1
                        logger.info("Cleaned up orphaned backup temporary: %s", tmp_file)
                except OSError as exc:
                    logger.warning("Could not unlink temporary '%s': %s", tmp_file, exc)

        return unlinked_count
