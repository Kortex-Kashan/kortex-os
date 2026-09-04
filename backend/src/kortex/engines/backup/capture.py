"""KORTEX Backup Engine point-in-time capture subsystem.

Phase 7 — Production Hardening — Backup Engine.
Implements:
1. Transactionally consistent SQLite online backup via `sqlite3.Connection.backup`
   executed asynchronously in a worker thread.
2. Sandboxed file and object blob capture from storage data directories.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from kortex.engines.backup.constants import (
    MAX_FILE_COUNT,
    SQLITE_ONLINE_BACKUP_PAGE_STEP,
    BackupComponentType,
)
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.exceptions import BackupCorruptionError, BackupStorageError
from kortex.engines.backup.models import ChecksumManifestEntry, ManifestComponentEntry
from kortex.engines.storage.sandbox import PathSandboxError, PathSandboxValidator

logger = logging.getLogger("kortex.engines.backup.capture")


class DatabaseSnapshotCapture:
    """Performs an online, non-blocking, consistent snapshot of the SQLite database."""

    def __init__(self, page_step: int = SQLITE_ONLINE_BACKUP_PAGE_STEP) -> None:
        self._page_step = page_step

    async def capture_database(
        self,
        source_db_path: Path,
        destination_db_path: Path,
    ) -> tuple[ManifestComponentEntry, str | None]:
        """Execute point-in-time SQLite online backup asynchronously.

        Args:
            source_db_path: Path to live SQLite database.
            destination_db_path: Path where snapshot should be created.

        Returns:
            Tuple of (ManifestComponentEntry, schema_revision).

        Raises:
            BackupStorageError: If database file cannot be read or written.
            BackupCorruptionError: If target snapshot fails integrity checks.
        """
        if not source_db_path.is_file():
            # If database doesn't exist yet, create an empty SQLite database for cold start
            logger.info("Database file '%s' does not exist yet; creating empty database snapshot.", source_db_path)
            destination_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(destination_db_path)
            conn.close()
            sha256, size_bytes = BackupCryptoManager.compute_sha256(destination_db_path)
            entry = ManifestComponentEntry(
                name="database",
                component_type=BackupComponentType.DATABASE,
                relative_path="database/kortex_snapshot.db",
                sha256=sha256,
                size_bytes=size_bytes,
                metadata={"source": str(source_db_path), "page_count": 0, "status": "empty"},
            )
            return entry, None

        destination_db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            schema_rev = await asyncio.to_thread(
                self._run_online_backup_sync,
                source_db_path,
                destination_db_path,
                self._page_step,
            )
        except sqlite3.DatabaseError as exc:
            raise BackupStorageError(f"SQLite online backup failed: {exc}") from exc

        # Compute digest and size
        sha256, size_bytes = BackupCryptoManager.compute_sha256(destination_db_path)

        entry = ManifestComponentEntry(
            name="database",
            component_type=BackupComponentType.DATABASE,
            relative_path="database/kortex_snapshot.db",
            sha256=sha256,
            size_bytes=size_bytes,
            metadata={
                "source": str(source_db_path),
                "schema_revision": schema_rev,
                "integrity": "verified",
            },
        )
        return entry, schema_rev

    @staticmethod
    def _run_online_backup_sync(
        source_path: Path,
        dest_path: Path,
        step: int,
    ) -> str | None:
        """Synchronous worker thread routine executing sqlite3 online backup and verification."""
        # Clean destination if previously existing
        if dest_path.exists():
            dest_path.unlink()

        # Connect with timeout and read-only URI if possible for safety
        src_uri = f"file:{source_path.resolve().as_posix()}?mode=ro"
        try:
            src_conn = sqlite3.connect(src_uri, uri=True, timeout=30.0)
        except sqlite3.OperationalError:
            src_conn = sqlite3.connect(str(source_path.resolve()), timeout=30.0)

        dest_conn = sqlite3.connect(str(dest_path.resolve()), timeout=30.0)

        try:
            # Perform SQLite online backup
            with dest_conn:
                src_conn.backup(dest_conn, pages=step)
        finally:
            src_conn.close()

        # Integrity check and schema discovery on destination connection
        try:
            cursor = dest_conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            rows = cursor.fetchall()
            if not rows or rows[0][0] != "ok":
                errors = "; ".join(r[0] for r in rows)
                raise BackupCorruptionError(f"Captured database snapshot failed PRAGMA integrity_check: {errors}")

            # Check for alembic schema revision
            schema_revision: str | None = None
            try:
                cursor.execute("SELECT version_num FROM alembic_version LIMIT 1;")
                ver_row = cursor.fetchone()
                if ver_row and ver_row[0]:
                    schema_revision = str(ver_row[0])
            except sqlite3.OperationalError:
                # alembic_version table doesn't exist
                schema_revision = None

            return schema_revision
        finally:
            dest_conn.close()


class StoragePayloadCapture:
    """Scans and streams persistent file and object blobs from KORTEX storage directories."""

    def __init__(self, storage_root: Path) -> None:
        self._storage_root = storage_root.resolve()
        self._sandbox = PathSandboxValidator(self._storage_root)

    def scan_storage_files(self) -> tuple[list[tuple[Path, str]], list[ChecksumManifestEntry], int]:
        """Scan storage directory for all regular files within sandbox boundaries.

        Returns:
            Tuple of:
            - List of (absolute_source_path, archive_relative_path)
            - List of ChecksumManifestEntry
            - Cumulative uncompressed size in bytes
        """
        if not self._storage_root.exists() or not self._storage_root.is_dir():
            logger.info("Storage root '%s' does not exist or is empty.", self._storage_root)
            return [], [], 0

        collected_files: list[tuple[Path, str]] = []
        checksum_entries: list[ChecksumManifestEntry] = []
        total_size = 0
        file_count = 0

        # Scan storage_root recursively
        for path in self._storage_root.rglob("*"):
            if not path.is_file():
                continue

            # Ignore temporary, cache, or backup files
            if path.name.endswith(".tmp") or ".cache" in path.parts or "backups" in path.parts:
                continue

            try:
                canonical = self._sandbox.resolve_sandboxed_path(path)
            except PathSandboxError as exc:
                logger.warning("Skipping file outside sandbox: %s (%s)", path, exc)
                continue

            file_count += 1
            if file_count > MAX_FILE_COUNT:
                raise BackupStorageError(
                    f"Storage directory exceeds maximum supported file count limit ({MAX_FILE_COUNT})."
                )

            # Archive relative path: storage/<rel_to_storage_root>
            rel_path = self._sandbox.get_relative_string(canonical)
            archive_path = f"storage/{rel_path}"

            try:
                sha256, size_bytes = BackupCryptoManager.compute_sha256(canonical)
            except OSError as exc:
                logger.warning("File '%s' changed or became inaccessible during scan: %s", canonical, exc)
                continue

            collected_files.append((canonical, archive_path))
            checksum_entries.append(
                ChecksumManifestEntry(
                    path=archive_path,
                    sha256=sha256,
                    size_bytes=size_bytes,
                )
            )
            total_size += size_bytes

        return collected_files, checksum_entries, total_size
