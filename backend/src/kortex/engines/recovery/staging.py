"""KORTEX Recovery Engine isolated staging and safe archive extraction.

Phase 7 — Production Hardening — Recovery Engine.
Provides sandboxed extraction workspaces, path traversal defense,
ZIP bomb detection, streaming validation, and disk capacity preflighting.
"""

from __future__ import annotations

import logging
import os
import shutil
import unicodedata
import zipfile
from pathlib import Path

from kortex.engines.backup.constants import CHUNK_SIZE_BYTES
from kortex.engines.recovery.constants import (
    DEFAULT_SAFETY_MARGIN_BYTES,
    MAX_ARCHIVE_SIZE_BYTES,
    MAX_DECOMPRESSION_RATIO,
    MAX_FILE_COUNT,
)
from kortex.engines.recovery.exceptions import (
    RecoveryInsufficientDiskSpaceError,
    RecoverySecurityError,
    RecoveryStorageError,
)
from kortex.engines.storage.sandbox import PathSandboxError, PathSandboxValidator

logger = logging.getLogger("kortex.engines.recovery.staging")


class RecoveryStagingManager:
    """Manages isolated staging workspaces for recovery validation and extraction."""

    def __init__(
        self,
        staging_base_dir: str | Path,
        max_file_count: int = MAX_FILE_COUNT,
        max_archive_size_bytes: int = MAX_ARCHIVE_SIZE_BYTES,
        safety_margin_bytes: int = DEFAULT_SAFETY_MARGIN_BYTES,
    ) -> None:
        self._staging_base_dir = Path(staging_base_dir).resolve()
        self._max_file_count = max_file_count
        self._max_archive_size_bytes = max_archive_size_bytes
        self._safety_margin_bytes = safety_margin_bytes

    @property
    def staging_base_dir(self) -> Path:
        return self._staging_base_dir

    def get_recovery_workspace(self, recovery_id: str) -> Path:
        """Resolve sandboxed workspace for a specific recovery operation."""
        workspace = (self._staging_base_dir / recovery_id).resolve()
        # Verify it stays strictly within staging base directory
        if not str(workspace).startswith(str(self._staging_base_dir)):
            raise RecoverySecurityError(f"Recovery ID '{recovery_id}' attempts staging directory escape.")
        return workspace

    def preflight_disk_capacity(
        self,
        artifact_size: int,
        uncompressed_payload_size: int,
        extracted_db_size: int,
        extracted_storage_size: int,
        live_db_size: int,
        live_storage_size: int,
        target_volume_dir: Path,
    ) -> tuple[int, int]:
        """Verify volume has sufficient free disk space according to the physical capacity formula.

        Formula:
        Required = S_encrypted + S_decrypted_zip + S_extracted_db + S_extracted_storage
                 + S_live_db_rollback + S_live_storage_rollback + S_safety_margin (500 MB)
        """
        required_bytes = (
            artifact_size
            + uncompressed_payload_size
            + extracted_db_size
            + extracted_storage_size
            + live_db_size
            + live_storage_size
            + self._safety_margin_bytes
        )

        try:
            target_volume_dir.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(target_volume_dir)
            available_bytes = getattr(usage, "free", usage[2] if len(usage) > 2 else 0)
        except OSError as exc:
            raise RecoveryStorageError(f"Failed to check disk usage on '{target_volume_dir}': {exc}") from exc

        if available_bytes < required_bytes:
            raise RecoveryInsufficientDiskSpaceError(
                f"Insufficient disk space for recovery: requires {required_bytes} bytes "
                f"({required_bytes / (1024 * 1024):.1f} MB), but only {available_bytes} bytes "
                f"({available_bytes / (1024 * 1024):.1f} MB) available on '{target_volume_dir}'."
            )

        return required_bytes, available_bytes

    def extract_zip_safely(self, zip_path: Path, destination_dir: Path) -> list[Path]:
        """Extract ZIP container with comprehensive traversal, symlink, and ZIP-bomb defenses."""
        if not zip_path.is_file():
            raise RecoveryStorageError(f"ZIP container not found for extraction: '{zip_path}'")

        destination_dir.mkdir(parents=True, exist_ok=True)
        sandbox = PathSandboxValidator(destination_dir)

        compressed_size = zip_path.stat().st_size
        extracted_files: list[Path] = []
        cumulative_uncompressed_bytes = 0
        file_count = 0

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                infolist = zf.infolist()

                if len(infolist) > self._max_file_count:
                    raise RecoverySecurityError(
                        f"Archive entry count ({len(infolist)}) exceeds maximum limit ({self._max_file_count})."
                    )

                # Preflight ZIP bomb check using archive headers
                declared_total_uncompressed = sum(info.file_size for info in infolist)
                if compressed_size > 0 and (declared_total_uncompressed / compressed_size) > MAX_DECOMPRESSION_RATIO:
                    raise RecoverySecurityError(
                        f"Archive decompression ratio ({declared_total_uncompressed / compressed_size:.1f}x) "
                        f"exceeds safety threshold ({MAX_DECOMPRESSION_RATIO}x). Potential ZIP bomb."
                    )

                if declared_total_uncompressed > self._max_archive_size_bytes:
                    raise RecoverySecurityError(
                        f"Archive uncompressed size ({declared_total_uncompressed} bytes) "
                        f"exceeds maximum allowed ({self._max_archive_size_bytes} bytes)."
                    )

                for info in infolist:
                    raw_filename = info.filename
                    # Normalize unicode NFC
                    norm_filename = unicodedata.normalize("NFC", raw_filename).replace("\\", "/")

                    # Security checks: traversal, drive letters, absolute paths, null bytes
                    if "\x00" in norm_filename:
                        raise RecoverySecurityError(f"Archive entry contains null bytes: '{raw_filename}'")
                    if norm_filename.startswith(("/", "\\")) or (len(norm_filename) > 1 and norm_filename[1] == ":"):
                        raise RecoverySecurityError(f"Archive entry is an absolute path: '{raw_filename}'")

                    parts = [p for p in norm_filename.split("/") if p]
                    if ".." in parts:
                        raise RecoverySecurityError(f"Archive entry contains path traversal ('..'): '{raw_filename}'")

                    # Reject symlinks and special files
                    # Unix symlink attribute check: higher 16 bits == 0o120000
                    unix_mode = info.external_attr >> 16
                    if unix_mode and (unix_mode & 0o170000) == 0o120000:
                        raise RecoverySecurityError(f"Archive entry is a symlink: '{raw_filename}'")

                    # Determine target path
                    try:
                        resolved_target = sandbox.resolve_sandboxed_path(norm_filename)
                    except PathSandboxError as exc:
                        raise RecoverySecurityError(
                            f"Archive entry escapes sandbox boundary: '{raw_filename}' ({exc})"
                        ) from exc

                    if info.is_dir():
                        resolved_target.mkdir(parents=True, exist_ok=True)
                        continue

                    # Stream file to disk and enforce cumulative limits
                    resolved_target.parent.mkdir(parents=True, exist_ok=True)
                    file_count += 1
                    if file_count > self._max_file_count:
                        raise RecoverySecurityError(f"Extracted file count exceeded limit ({self._max_file_count}).")

                    with zf.open(info, "r") as source_fp, resolved_target.open("wb") as dest_fp:
                        while chunk := source_fp.read(CHUNK_SIZE_BYTES):
                            cumulative_uncompressed_bytes += len(chunk)
                            if cumulative_uncompressed_bytes > self._max_archive_size_bytes:
                                raise RecoverySecurityError("Decompressed stream exceeded archive size limit.")
                            dest_fp.write(chunk)
                        dest_fp.flush()
                        os.fsync(dest_fp.fileno())

                    extracted_files.append(resolved_target)

        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise RecoverySecurityError(f"Corrupted or malformed ZIP container: {exc}") from exc
        except Exception:
            # Clean partial extraction on failure
            self.cleanup_directory(destination_dir)
            raise

        return extracted_files

    def cleanup_directory(self, target_dir: Path) -> None:
        """Safely delete target directory if it resides within staging root."""
        if not target_dir.exists():
            return
        resolved = target_dir.resolve()
        if not str(resolved).startswith(str(self._staging_base_dir)):
            logger.warning("Refusing to clean directory outside staging root: '%s'", resolved)
            return

        try:
            shutil.rmtree(resolved, ignore_errors=True)
        except OSError as exc:
            logger.warning("Failed to remove staging directory '%s': %s", resolved, exc)

    def cleanup_workspace(self, recovery_id: str) -> None:
        """Clean workspace for specific recovery ID."""
        ws = self.get_recovery_workspace(recovery_id)
        self.cleanup_directory(ws)
