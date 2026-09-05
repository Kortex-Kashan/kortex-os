"""KORTEX Update Engine staging workspace manager and hostile archive defenses.

Phase 7 — Production Hardening — Update Engine.
Guarantees sandbox isolation, disk space preflighting, ZIP slip/bomb defense,
and component checksum verification.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from kortex.engines.update.constants import (
    CHECKSUMS_FILENAME,
    MAX_ARCHIVE_SIZE_BYTES,
    MAX_EXPANSION_RATIO,
    MAX_FILE_COUNT,
    MAX_PATH_LENGTH,
    MAX_SINGLE_FILE_SIZE_BYTES,
    MAX_UNCOMPRESSED_SIZE_BYTES,
    SAFETY_RESERVE_BYTES,
)
from kortex.engines.update.crypto import compute_file_sha256, verify_file_sha256
from kortex.engines.update.exceptions import (
    UpdateArchiveSecurityError,
    UpdateChecksumMismatchError,
    UpdateDiskSpaceError,
    UpdatePathTraversalError,
    UpdateZipBombError,
)
from kortex.engines.update.models import UpdateManifest


class UpdateStagingManager:
    """Manages update artifact acquisition, preflight disk checks, and secure extraction."""

    def __init__(
        self,
        staging_base_dir: str | Path = "storage_data/.update/staging",
        safety_reserve_bytes: int = SAFETY_RESERVE_BYTES,
    ) -> None:
        self._staging_base_dir = Path(staging_base_dir).resolve()
        self._safety_reserve_bytes = safety_reserve_bytes

    @property
    def staging_base_dir(self) -> Path:
        return self._staging_base_dir

    def get_update_staging_dir(self, update_id: str) -> Path:
        """Return the dedicated staging directory for an update ID."""
        # Sanitize update_id
        safe_id = "".join(c for c in update_id if c.isalnum() or c in ("-", "_"))
        if not safe_id or safe_id != update_id:
            raise UpdateArchiveSecurityError(f"Invalid update_id for staging path: {update_id!r}")
        return self._staging_base_dir / safe_id

    def preflight_disk_space(
        self,
        manifest: UpdateManifest,
        live_db_path: Path | str | None = None,
    ) -> None:
        """Verify that host filesystem has sufficient free space before staging or mutating."""
        package_size = manifest.package.size_bytes
        uncompressed_size = manifest.package.uncompressed_bytes

        # Live DB and estimated backup size
        db_size = 0
        if live_db_path:
            p = Path(live_db_path)
            if p.is_file():
                db_size = p.stat().st_size
        if db_size == 0:
            # Conservative default if live DB not yet created or measured
            db_size = 50 * 1024 * 1024  # 50 MB

        estimated_backup_size = db_size + (10 * 1024 * 1024)

        # Formula: (1.0 * Package) + (1.5 * Extracted) + (1.0 * DB) + (1.0 * Backup) + Reserve
        required_bytes = int(
            (1.0 * package_size)
            + (1.5 * uncompressed_size)
            + (1.0 * db_size)
            + (1.0 * estimated_backup_size)
            + self._safety_reserve_bytes
        )

        # Query disk space
        check_path = self._staging_base_dir
        check_path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(check_path)

        free_bytes = getattr(usage, "free", usage[2] if isinstance(usage, tuple) and len(usage) > 2 else 0)
        if free_bytes < required_bytes:
            raise UpdateDiskSpaceError(
                f"Insufficient disk space for update. Required: {required_bytes / (1024 * 1024):.1f} MB, "
                f"Available: {free_bytes / (1024 * 1024):.1f} MB on {check_path}."
            )

    def validate_archive_security(self, archive_path: Path | str) -> list[zipfile.ZipInfo]:
        """Inspect ZIP archive metadata to prevent ZIP slip, traversal, bombs, and symlinks."""
        path = Path(archive_path)
        if not path.is_file():
            raise FileNotFoundError(f"Update archive not found: {path}")

        archive_size = path.stat().st_size
        if archive_size > MAX_ARCHIVE_SIZE_BYTES:
            raise UpdateArchiveSecurityError(
                f"Archive size {archive_size} exceeds maximum limit of {MAX_ARCHIVE_SIZE_BYTES} bytes."
            )

        try:
            with zipfile.ZipFile(path, "r") as zf:
                members = zf.infolist()
        except zipfile.BadZipFile as exc:
            raise UpdateArchiveSecurityError(f"Corrupt or invalid ZIP archive: {exc}") from exc

        if len(members) > MAX_FILE_COUNT:
            raise UpdateZipBombError(f"Archive contains {len(members)} entries, exceeding limit of {MAX_FILE_COUNT}.")

        total_uncompressed = 0
        seen_names: set[str] = set()

        for member in members:
            # 1. Total & single size checks
            total_uncompressed += member.file_size
            if member.file_size > MAX_SINGLE_FILE_SIZE_BYTES:
                raise UpdateZipBombError(
                    f"Entry '{member.filename}' size {member.file_size} exceeds "
                    f"single file limit of {MAX_SINGLE_FILE_SIZE_BYTES} bytes."
                )

            # 2. Duplicate entries check
            if member.filename in seen_names:
                raise UpdateArchiveSecurityError(f"Duplicate archive member detected: '{member.filename}'")
            seen_names.add(member.filename)

            # 3. Path length check
            if len(member.filename) > MAX_PATH_LENGTH:
                raise UpdateArchiveSecurityError(
                    f"Archive member path length {len(member.filename)} exceeds limit of {MAX_PATH_LENGTH}."
                )

            # 4. Path traversal / absolute / UNC / drive letter defense
            norm = member.filename.replace("\\", "/")
            if norm.startswith("/") or norm.startswith("\\"):
                raise UpdatePathTraversalError(f"Absolute paths forbidden in update archive: '{member.filename}'")
            if ":" in norm:
                raise UpdatePathTraversalError(
                    f"Drive-letter or colon paths forbidden in update archive: '{member.filename}'"
                )

            parts = [p for p in norm.split("/") if p]
            if ".." in parts:
                raise UpdatePathTraversalError(
                    f"Directory traversal '..' forbidden in update archive: '{member.filename}'"
                )

            # 5. Symlink / Hardlink defense (POSIX symlink attribute in high 16 bits)
            is_symlink = ((member.external_attr >> 16) & 0o120000) == 0o120000
            if is_symlink:
                raise UpdateArchiveSecurityError(f"Symlinks forbidden in update archive: '{member.filename}'")

        if total_uncompressed > MAX_UNCOMPRESSED_SIZE_BYTES:
            raise UpdateZipBombError(
                f"Total uncompressed size {total_uncompressed} exceeds limit of {MAX_UNCOMPRESSED_SIZE_BYTES} bytes."
            )

        if archive_size > 0:
            ratio = total_uncompressed / archive_size
            if ratio > MAX_EXPANSION_RATIO:
                raise UpdateZipBombError(
                    f"Archive expansion ratio {ratio:.1f}:1 exceeds maximum threshold of {MAX_EXPANSION_RATIO}:1."
                )

        return members

    def extract_staged_archive(
        self,
        archive_path: Path | str,
        update_id: str,
    ) -> Path:
        """Securely extract a validated update archive into the isolated staging workspace."""
        staging_dir = self.get_update_staging_dir(update_id)

        # Clean existing staging if present
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)

        # Validate security before extraction
        self.validate_archive_security(archive_path)

        # Extract members safely
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.infolist():
                target_path = (staging_dir / member.filename).resolve()
                # Verify destination is strictly within staging_dir
                if not str(target_path).startswith(str(staging_dir.resolve())):
                    raise UpdatePathTraversalError(
                        f"Zip slip escape detected: '{member.filename}' resolves to outside staging root"
                    )

                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, target_path.open("wb") as dest:
                        shutil.copyfileobj(source, dest)

        # Verify internal checksums.json if present
        checksums_path = staging_dir / CHECKSUMS_FILENAME
        if checksums_path.is_file():
            self.verify_staging_checksums(staging_dir)

        return staging_dir

    def verify_staging_checksums(self, staging_dir: Path) -> None:
        """Verify internal components against staging checksums.json."""
        checksums_path = staging_dir / CHECKSUMS_FILENAME
        if not checksums_path.is_file():
            return

        try:
            with checksums_path.open("r", encoding="utf-8") as f:
                checksums = json.load(f)
        except Exception as exc:
            raise UpdateChecksumMismatchError(f"Malformed checksums.json in update package: {exc}") from exc

        if not isinstance(checksums, dict):
            raise UpdateChecksumMismatchError("checksums.json must contain a JSON object mapping filenames to SHA-256")

        for rel_path, expected_hash in checksums.items():
            if not isinstance(rel_path, str) or not isinstance(expected_hash, str):
                continue
            file_on_disk = staging_dir / rel_path
            if not file_on_disk.is_file():
                raise UpdateChecksumMismatchError(f"File listed in checksums.json missing from staging: '{rel_path}'")
            if not verify_file_sha256(file_on_disk, expected_hash):
                actual = compute_file_sha256(file_on_disk)
                raise UpdateChecksumMismatchError(
                    f"Component checksum mismatch for '{rel_path}': expected '{expected_hash}', got '{actual}'"
                )

    def purge_staging(self, update_id: str) -> None:
        """Purge staging workspace for an update operation."""
        staging_dir = self.get_update_staging_dir(update_id)
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
