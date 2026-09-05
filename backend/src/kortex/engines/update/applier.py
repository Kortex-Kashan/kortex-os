"""KORTEX Update Engine filesystem component swapper and local rollback coordinator.

Phase 7 — Production Hardening — Update Engine.
Guarantees staged filesystem replacement, preservation of .rollback_<id> snapshot copies,
and deterministic reverse-swap rollback on failure.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from kortex.engines.update.exceptions import UpdateSwapError

logger = logging.getLogger(__name__)

# System paths that must NEVER be modified or overwritten by filesystem component swap
PROTECTED_PATHS: frozenset[str] = frozenset(
    {
        "storage_data/backups",
        "storage_data/.recovery",
        "storage_data/.update",
        "kortex_local.db",
        "kortex_local.db-wal",
        "kortex_local.db-shm",
        "alembic.ini",
        "alembic/env.py",
        "alembic/script.py.mako",
        ".venv",
        ".git",
    }
)


class UpdateApplier:
    """Coordinates atomic staged filesystem swapping and local reverse-swap rollback."""

    def __init__(self, target_root: Path | str | None = None) -> None:
        if target_root:
            self._target_root = Path(target_root).resolve()
        else:
            # Default to backend root
            self._target_root = Path(__file__).resolve().parents[3]

    @property
    def target_root(self) -> Path:
        return self._target_root

    def is_path_protected(self, rel_path: str) -> bool:
        """Check if a relative path falls within protected system topology."""
        norm = rel_path.replace("\\", "/").lstrip("/")
        return any(norm == protected or norm.startswith(f"{protected}/") for protected in PROTECTED_PATHS)

    def swap_components(
        self,
        staging_dir: Path | str,
        update_id: str,
    ) -> list[str]:
        """Swap validated staged files into live paths while preserving .rollback copies.

        Returns list of preserved rollback copy paths.
        Raises UpdateSwapError on failure and immediately reverts all swapped files.
        """
        staged_path = Path(staging_dir).resolve()
        if not staged_path.is_dir():
            raise FileNotFoundError(f"Staging directory not found: {staged_path}")

        # Discover all files to swap
        staged_files: list[Path] = [p for p in staged_path.rglob("*") if p.is_file()]
        # Filter out manifest.json, checksums.json from application payload if at root
        payload_files = [p for p in staged_files if p.name not in ("manifest.json", "checksums.json")]

        swapped_records: list[tuple[Path, Path | None]] = []  # (live_path, rollback_copy_path)
        created_new_files: list[Path] = []

        try:
            for staged_file in payload_files:
                rel = staged_file.relative_to(staged_path)
                rel_str = str(rel).replace("\\", "/")

                # Reject protected paths
                if self.is_path_protected(rel_str):
                    raise UpdateSwapError(f"Protected system topology path detected in update payload: {rel_str}")

                live_file = (self._target_root / rel).resolve()

                # Ensure destination directory exists
                live_file.parent.mkdir(parents=True, exist_ok=True)

                rollback_copy: Path | None = None
                if live_file.exists():
                    # Preserve rollback copy
                    rollback_copy = live_file.parent / f"{live_file.name}.rollback_{update_id}"
                    shutil.copy2(live_file, rollback_copy)
                    swapped_records.append((live_file, rollback_copy))
                else:
                    created_new_files.append(live_file)
                    swapped_records.append((live_file, None))

                # Atomically replace file
                # Write to temp file in same directory first to ensure atomic os.replace across platforms
                temp_swap = live_file.parent / f"{live_file.name}.tmp_swap_{update_id}"
                shutil.copy2(staged_file, temp_swap)
                os.replace(temp_swap, live_file)

            logger.info("Successfully swapped %d components for update '%s'", len(payload_files), update_id)
            result_paths: list[str] = []
            for live_file, rollback_copy in swapped_records:
                if rollback_copy is not None:
                    result_paths.append(str(rollback_copy))
                else:
                    result_paths.append(str(live_file))
            return result_paths

        except Exception as exc:
            logger.error("Error during filesystem swap for update '%s': %s; executing reverse swap", update_id, exc)
            self.reverse_swap(swapped_records, created_new_files)
            raise UpdateSwapError(f"Filesystem component swap failed: {exc}. Reverted all swapped files.") from exc

    def reverse_swap(
        self,
        swapped_records: Sequence[tuple[Path, Path | None]],
        created_new_files: list[Path] | None = None,
    ) -> None:
        """Revert swapped files from .rollback copies in reverse order."""
        errors: list[str] = []

        # 1. Remove files that were newly created
        files_to_remove = list(created_new_files or [])
        for live_file, rollback_copy in swapped_records:
            if rollback_copy is None and live_file not in files_to_remove:
                files_to_remove.append(live_file)

        for new_file in files_to_remove:
            try:
                if new_file.is_file():
                    new_file.unlink()
            except OSError as exc:
                errors.append(f"Failed to remove newly created file {new_file}: {exc}")

        # 2. Restore previous files from rollback copies
        for live_file, rollback_copy in reversed(swapped_records):
            if rollback_copy and rollback_copy.is_file():
                try:
                    os.replace(rollback_copy, live_file)
                except OSError as exc:
                    errors.append(f"Failed to restore {live_file} from {rollback_copy}: {exc}")

        if errors:
            logger.critical("Reverse swap encountered errors: %s", errors)
            raise UpdateSwapError(f"Reverse swap partially failed: {'; '.join(errors)}")

    def cleanup_rollback_copies(self, rollback_copy_paths: list[str]) -> None:
        """Delete preserved .rollback copies after update has been fully committed and verified."""
        for path_str in rollback_copy_paths:
            p = Path(path_str)
            if p.is_file():
                try:
                    p.unlink()
                except OSError as exc:
                    logger.warning("Could not delete rollback copy %s: %s", p, exc)
