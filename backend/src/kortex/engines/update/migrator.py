"""KORTEX Update Engine Alembic migration orchestrator.

Phase 7 — Production Hardening — Update Engine.
Implements the Cases A-J forward-only migration model.
In-place Alembic downgrades (`alembic downgrade`) are STRICTLY FORBIDDEN.
Disaster database restoration is delegated exclusively to Recovery Engine.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from kortex.engines.update.exceptions import (
    UpdateDowngradeError,
    UpdateMigrationError,
    UpdateSchemaIncompatibleError,
)
from kortex.engines.update.models import UpdateManifest

logger = logging.getLogger(__name__)


class UpdateMigrator:
    """Orchestrates forward-only Alembic database schema migrations for Update Engine."""

    def __init__(self, alembic_ini_path: Path | str | None = None) -> None:
        if alembic_ini_path:
            self._ini_path = Path(alembic_ini_path).resolve()
        else:
            # Default to backend/alembic.ini (migrator.py -> update -> engines -> kortex -> src -> backend)
            backend_dir = Path(__file__).resolve().parents[4]
            self._ini_path = backend_dir / "alembic.ini"
            if not self._ini_path.is_file():
                alt = Path(__file__).resolve().parents[3] / "alembic.ini"
                if alt.is_file():
                    self._ini_path = alt

    @property
    def ini_path(self) -> Path:
        return self._ini_path

    def get_alembic_config(self, db_url: str | None = None) -> Config:
        """Create and configure an Alembic Config object."""
        if not self._ini_path.is_file():
            raise FileNotFoundError(f"Alembic configuration file not found at: {self._ini_path}")

        cfg = Config(str(self._ini_path))
        if db_url:
            cfg.set_main_option("sqlalchemy.url", db_url)
        return cfg

    def _get_revision_map(self, db_url: str | None = None) -> dict[str, Any]:
        """Return a mapping of revision identifier to revision script object."""
        cfg = self.get_alembic_config(db_url)
        script_dir = ScriptDirectory.from_config(cfg)
        return {rev.revision: rev for rev in script_dir.walk_revisions()}

    def get_current_revision(self, db_path_or_url: str | None = None) -> str | None:
        """Query current revision from the alembic_version table directly."""
        url = db_path_or_url or os.environ.get("KORTEX_DATABASE_URL")
        # If it's a sqlite path or sqlite url
        sqlite_path = None
        if url:
            if url.startswith("sqlite:///") or url.startswith("sqlite+aiosqlite:///"):
                sqlite_path = url.split(":///", 1)[1]
            elif not url.startswith("sqlite"):
                # Non-sqlite; return None or inspect
                sqlite_path = None
            else:
                sqlite_path = url
        else:
            # Check default app storage
            storage_dir = os.environ.get("KORTEX_STORAGE_DIR", "storage_data")
            p = Path(storage_dir) / "kortex_local.db"
            if p.is_file():
                sqlite_path = str(p)

        if sqlite_path and Path(sqlite_path).is_file():
            try:
                conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT version_num FROM alembic_version LIMIT 1")
                    row = cursor.fetchone()
                    if row:
                        return str(row[0])
                except sqlite3.OperationalError:
                    # Table does not exist yet
                    return None
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning("Could not read current revision directly from SQLite at %s: %s", sqlite_path, exc)

        return None

    def validate_target_revision(self, target_revision: str, db_url: str | None = None) -> None:
        """Validate that the target revision exists in the Alembic script directory."""
        cfg = self.get_alembic_config(db_url)
        script_dir = ScriptDirectory.from_config(cfg)
        try:
            script_dir.get_revision(target_revision)
        except Exception as exc:
            raise UpdateSchemaIncompatibleError(
                f"Target schema revision '{target_revision}' not found in migration scripts: {exc}"
            ) from exc

    def run_forward_migration(
        self,
        manifest: UpdateManifest,
        db_url: str | None = None,
    ) -> dict[str, Any]:
        """Synchronously evaluate and execute forward migration for a given manifest."""
        if not manifest.database.requires_migration:
            return {"migrated": False, "current_revision": None}

        target_revision = manifest.database.target_revision
        if not target_revision:
            return {"migrated": False, "current_revision": None}

        current_rev = self.get_current_revision(db_url)
        if current_rev and current_rev == target_revision:
            logger.info("Database schema already at target revision %s; skipping migration.", target_revision)
            return {
                "migrated": False,
                "current_revision": current_rev,
                "target_revision": target_revision,
                "status": "ALREADY_CURRENT",
            }

        if (
            manifest.database.supported_source_revisions
            and current_rev
            and current_rev not in manifest.database.supported_source_revisions
        ):
            raise UpdateSchemaIncompatibleError(
                f"Current revision '{current_rev}' is not in manifest's supported source revisions: "
                f"{manifest.database.supported_source_revisions}"
            )

        # Check downgrade prevention
        if current_rev:
            rev_map = self._get_revision_map(db_url)
            curr = rev_map.get(current_rev)
            chain: set[str] = set()
            while curr and curr.down_revision:
                if isinstance(curr.down_revision, (tuple, list)):
                    chain.update(str(x) for x in curr.down_revision)
                    break
                if isinstance(curr.down_revision, str):
                    chain.add(curr.down_revision)
                    curr = rev_map.get(curr.down_revision)
                else:
                    break
            if target_revision in chain:
                raise UpdateDowngradeError(
                    f"Alembic downgrade is strictly forbidden: cannot migrate backwards from "
                    f"{current_rev} to {target_revision}. "
                    f"Use Recovery Engine to restore a pre-update backup snapshot."
                )

        cfg = self.get_alembic_config(db_url)
        # `alembic/env.py`'s online-migration path re-derives the target URL from
        # KORTEX_DATABASE_URL directly (ignoring the Config object's own
        # sqlalchemy.url), so this env var must be set for the duration of the
        # upgrade call. It is a process-wide global, so it MUST be restored to its
        # prior value afterward -- never left mutated, or it silently leaks into
        # every later DatabaseEngineManager/Alembic invocation in the same process.
        previous_db_url_env = os.environ.get("KORTEX_DATABASE_URL")
        try:
            if db_url:
                os.environ["KORTEX_DATABASE_URL"] = db_url
            try:
                command.upgrade(cfg, target_revision)
            except Exception as exc:
                raise UpdateMigrationError(
                    f"Alembic forward migration to '{target_revision}' failed: {exc}. "
                    f"Database must be restored from pre-update checkpoint via Recovery Engine."
                ) from exc
        finally:
            if db_url:
                if previous_db_url_env is None:
                    os.environ.pop("KORTEX_DATABASE_URL", None)
                else:
                    os.environ["KORTEX_DATABASE_URL"] = previous_db_url_env

        new_rev = self.get_current_revision(db_url)
        logger.info(
            "Successfully migrated schema from %s to %s (confirmed on disk: %s)", current_rev, target_revision, new_rev
        )

        return {
            "migrated": True,
            "previous_revision": current_rev,
            "target_revision": target_revision,
            "confirmed_revision": new_rev,
            "status": "MIGRATION_COMPLETED",
        }

    async def execute_forward_migration(
        self,
        manifest: UpdateManifest,
        db_url: str | None = None,
    ) -> dict[str, Any]:
        """Execute a forward-only Alembic database schema upgrade in a worker thread.

        Takes the full verified update manifest (not just the target revision) so that
        `supported_source_revisions` and other manifest-declared migration compatibility
        gates are enforced against the actual signed manifest, not a synthetic stand-in.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.run_forward_migration(manifest, db_url))
