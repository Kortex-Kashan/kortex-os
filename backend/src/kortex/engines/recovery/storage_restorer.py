"""KORTEX Recovery Engine storage subtree restorer and referential consistency checker.

Phase 7 — Production Hardening — Recovery Engine.
Replaces managed storage subtrees (documents, buckets, metadata) while protecting
canonical backups and cache directories. Enforces database-to-storage referential integrity
and provides deterministic reverse-swap rollback.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from pathlib import Path

from kortex.engines.recovery.constants import ROLLBACK_SUFFIX
from kortex.engines.recovery.exceptions import (
    RecoveryStorageError,
)

logger = logging.getLogger("kortex.engines.recovery.storage_restorer")

_MANAGED_SUBTREES = ("documents", "buckets", "metadata")
_EXCLUDED_PARTS = ("backups", ".cache", ".tmp", ".recovery", ".recovery_staging")


class StorageRestorer:
    """Coordinates storage subtree replacement, referential checks, and rollbacks."""

    def __init__(self, storage_root: str | Path) -> None:
        self._storage_root = Path(storage_root).resolve()

    @property
    def storage_root(self) -> Path:
        return self._storage_root

    def discover_staged_subtrees(self, staged_storage_dir: Path) -> list[str]:
        """Discover which managed subtrees are present in the staged archive."""
        if not staged_storage_dir.is_dir():
            return []

        subtrees: list[str] = []
        for item in staged_storage_dir.iterdir():
            if item.is_dir() and item.name not in _EXCLUDED_PARTS:
                subtrees.append(item.name)
        return sorted(subtrees)

    def verify_referential_consistency(
        self,
        db_path: Path,
        storage_dir: Path,
    ) -> tuple[bool, list[str], list[str]]:
        """Verify that every database-referenced document or object exists in storage.

        Returns:
            Tuple of (is_consistent, missing_files, warnings).
        """
        if not db_path.is_file():
            return False, ["Database snapshot not found for referential check."], []

        missing_files: list[str] = []
        warnings: list[str] = []

        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=30.0)
        except sqlite3.OperationalError:
            conn = sqlite3.connect(str(db_path.resolve()), timeout=30.0)

        try:
            cursor = conn.cursor()

            # 1. Check documents and document_versions tables
            tables_query = "SELECT name FROM sqlite_master WHERE type='table';"
            cursor.execute(tables_query)
            existing_tables = {row[0] for row in cursor.fetchall()}

            checked_refs = 0
            if "documents" in existing_tables:
                cursor.execute("SELECT id, storage_path FROM documents WHERE storage_path IS NOT NULL;")
                for doc_id, rel_path in cursor.fetchall():
                    if not rel_path:
                        continue
                    clean_rel = str(rel_path).lstrip("/\\").replace("\\", "/")
                    target = (storage_dir / clean_rel).resolve()
                    # Also check storage_dir / "documents" / clean_rel
                    target_alt = (storage_dir / "documents" / clean_rel).resolve()

                    checked_refs += 1
                    if not target.is_file() and not target_alt.is_file():
                        missing_files.append(f"document:{doc_id} -> {clean_rel}")

            if "document_versions" in existing_tables:
                cursor.execute("SELECT id, storage_path FROM document_versions WHERE storage_path IS NOT NULL;")
                for ver_id, rel_path in cursor.fetchall():
                    if not rel_path:
                        continue
                    clean_rel = str(rel_path).lstrip("/\\").replace("\\", "/")
                    target = (storage_dir / clean_rel).resolve()
                    target_alt = (storage_dir / "documents" / clean_rel).resolve()

                    checked_refs += 1
                    if not target.is_file() and not target_alt.is_file():
                        missing_files.append(f"document_version:{ver_id} -> {clean_rel}")

            logger.info("Referential check completed: verified %d DB references.", checked_refs)

        except Exception as exc:
            warnings.append(f"Referential inspection encountered error: {exc}")
        finally:
            conn.close()

        is_consistent = len(missing_files) == 0
        return is_consistent, missing_files, warnings

    def execute_storage_swap(
        self,
        staged_storage_dir: Path,
        recovery_id: str,
    ) -> dict[str, str]:
        """Swap staged storage subtrees into live storage_data.

        Preserves live subtrees to <name>.rollback_<recovery_id>.
        Returns map of preserved rollback sources.
        """
        self._storage_root.mkdir(parents=True, exist_ok=True)
        staged_subtrees = self.discover_staged_subtrees(staged_storage_dir)
        rollback_sources: dict[str, str] = {}

        for subtree_name in staged_subtrees:
            live_subtree = (self._storage_root / subtree_name).resolve()
            staged_subtree = (staged_storage_dir / subtree_name).resolve()

            # 1. Preserve live subtree if it exists
            if live_subtree.exists():
                rollback_path = self._storage_root / f"{subtree_name}{ROLLBACK_SUFFIX}{recovery_id}"
                try:
                    # Rename live directory to rollback
                    os.replace(live_subtree, rollback_path)
                    rollback_sources[f"storage_{subtree_name}"] = str(rollback_path)
                    logger.info("Preserved live storage subtree '%s' to '%s'", subtree_name, rollback_path)
                except OSError as exc:
                    # Windows handle fallback: copy live tree to rollback, then remove contents
                    logger.warning(
                        "Directory rename failed for '%s' (%s); attempting copy fallback.", subtree_name, exc
                    )
                    try:
                        shutil.copytree(live_subtree, rollback_path, dirs_exist_ok=True)
                        shutil.rmtree(live_subtree, ignore_errors=True)
                        rollback_sources[f"storage_{subtree_name}"] = str(rollback_path)
                    except OSError as copy_exc:
                        # Attempt rollback of any previously moved subtrees
                        self.execute_reverse_swap(rollback_sources)
                        raise RecoveryStorageError(
                            f"Failed to preserve live storage subtree '{subtree_name}': {copy_exc}"
                        ) from copy_exc

            # 2. Swap staged subtree into live location
            try:
                os.replace(staged_subtree, live_subtree)
                logger.info("Swapped staged storage subtree '%s' into '%s'", subtree_name, live_subtree)
            except OSError:
                # Attempt copytree fallback if os.replace fails across boundaries
                try:
                    shutil.copytree(staged_subtree, live_subtree, dirs_exist_ok=True)
                    logger.info("Copied staged storage subtree '%s' into '%s'", subtree_name, live_subtree)
                except OSError as copy_exc:
                    self.execute_reverse_swap(rollback_sources)
                    raise RecoveryStorageError(
                        f"Failed to move staged storage subtree '{subtree_name}' into live storage: {copy_exc}"
                    ) from copy_exc

        return rollback_sources

    def execute_reverse_swap(self, rollback_sources: dict[str, str]) -> None:
        """Restore live storage subtrees from preserved rollback sources."""
        logger.warning("Executing reverse swap for storage subtrees...")

        for key, source_path_str in rollback_sources.items():
            if key.startswith("storage_"):
                subtree_name = key.replace("storage_", "", 1)
            elif key in _MANAGED_SUBTREES:
                subtree_name = key
            else:
                continue

            source_path = Path(source_path_str)
            live_path = self._storage_root / subtree_name

            if not source_path.exists():
                logger.error("Storage rollback source '%s' is missing! Cannot restore.", source_path)
                continue

            try:
                if live_path.exists():
                    shutil.rmtree(live_path, ignore_errors=True)
                os.replace(source_path, live_path)
                logger.info("Restored storage subtree '%s' from '%s'", subtree_name, source_path)
            except OSError as exc:
                logger.warning("Failed directory rename for '%s' (%s); attempting copytree restore.", subtree_name, exc)
                try:
                    shutil.copytree(source_path, live_path, dirs_exist_ok=True)
                    shutil.rmtree(source_path, ignore_errors=True)
                except OSError as copy_exc:
                    raise RecoveryStorageError(
                        f"Critical failure during reverse swap of storage subtree '{subtree_name}': {copy_exc}"
                    ) from copy_exc
