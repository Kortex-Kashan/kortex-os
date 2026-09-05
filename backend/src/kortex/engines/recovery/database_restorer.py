"""KORTEX Recovery Engine SQLite snapshot restorer and staged migration coordinator.

Phase 7 — Production Hardening — Recovery Engine.
Validates SQLite page B-tree integrity, evaluates deterministic schema compatibility,
executes forward migrations strictly in isolated staging, and performs atomic
database file swaps with full reverse-swap rollback capabilities.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from kortex.engines.recovery.constants import ROLLBACK_SUFFIX
from kortex.engines.recovery.exceptions import (
    RecoveryCompatibilityError,
    RecoveryDatabaseError,
)

logger = logging.getLogger("kortex.engines.recovery.database_restorer")


class DatabaseRestorer:
    """Coordinates SQLite snapshot validation, staged migration, and physical file swap."""

    def __init__(self, backend_root: Path | None = None) -> None:
        self._backend_root = backend_root or self._resolve_backend_root()

    @staticmethod
    def _resolve_backend_root() -> Path:
        """Locate backend directory containing alembic.ini."""
        current = Path(__file__).resolve()
        # Search parent directories for backend/alembic.ini
        for parent in current.parents:
            if (parent / "alembic.ini").is_file():
                return parent
            if (parent / "backend" / "alembic.ini").is_file():
                return parent / "backend"
        return current.parents[4]  # Default fallback

    @staticmethod
    def validate_sqlite_file(db_path: Path) -> tuple[bool, str, str | None]:
        """Verify SQLite file integrity, foreign keys, and extract schema revision.

        Returns:
            Tuple of (is_valid, status_message, schema_revision).
        """
        if not db_path.is_file():
            return False, f"Database file does not exist: '{db_path}'", None

        # Connect with read-only URI
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=30.0)
        except sqlite3.OperationalError:
            try:
                conn = sqlite3.connect(str(db_path.resolve()), timeout=30.0)
            except Exception as exc:
                return False, f"Cannot open SQLite connection: {exc}", None

        schema_revision: str | None = None
        try:
            cursor = conn.cursor()

            # 1. PRAGMA integrity_check
            cursor.execute("PRAGMA integrity_check;")
            rows = cursor.fetchall()
            if not rows or rows[0][0] != "ok":
                errors = "; ".join(str(r[0]) for r in rows)
                return False, f"PRAGMA integrity_check failed: {errors}", None

            # 2. PRAGMA foreign_key_check
            cursor.execute("PRAGMA foreign_key_check;")
            fk_violations = cursor.fetchall()
            if fk_violations:
                return False, f"PRAGMA foreign_key_check failed with {len(fk_violations)} violations.", None

            # 3. Read alembic schema revision
            try:
                cursor.execute("SELECT version_num FROM alembic_version LIMIT 1;")
                ver_row = cursor.fetchone()
                if ver_row and ver_row[0]:
                    schema_revision = str(ver_row[0])
            except sqlite3.OperationalError:
                schema_revision = None

            return True, "ok", schema_revision

        except Exception as exc:
            return False, f"Error validating SQLite database: {exc}", None
        finally:
            conn.close()

    def get_app_schema_head(self) -> str | None:
        """Resolve current application Alembic head revision."""
        alembic_ini = self._backend_root / "alembic.ini"
        if not alembic_ini.is_file():
            return None

        try:
            from alembic.config import Config
            from alembic.script import ScriptDirectory

            cfg = Config(str(alembic_ini))
            cfg.set_main_option("script_location", str(self._backend_root / "alembic"))
            script = ScriptDirectory.from_config(cfg)
            return script.get_current_head()
        except Exception as exc:
            logger.debug("Failed to determine Alembic head revision: %s", exc)
            return None

    def evaluate_schema_compatibility(
        self,
        snapshot_revision: str | None,
        app_revision: str | None,
    ) -> tuple[bool, bool, str]:
        """Evaluate deterministic schema compatibility.

        Returns:
            Tuple of (is_compatible, requires_staged_migration, explanation).
        """
        if snapshot_revision is None or app_revision is None:
            # Cold start or unversioned schema: compatible without migration
            return True, False, "Unversioned schema snapshot; direct restore permitted."

        if snapshot_revision == app_revision:
            return True, False, "Schema revision exactly matches running application. Direct restore."

        # Discover revision order
        alembic_ini = self._backend_root / "alembic.ini"
        if alembic_ini.is_file():
            try:
                from alembic.config import Config
                from alembic.script import ScriptDirectory

                cfg = Config(str(alembic_ini))
                cfg.set_main_option("script_location", str(self._backend_root / "alembic"))
                script = ScriptDirectory.from_config(cfg)

                from alembic.script.revision import RangeNotAncestorError

                # Check if snapshot_revision is an ancestor of app_revision (older backup -> forward migration)
                try:
                    list(script.iterate_revisions(app_revision, snapshot_revision))
                    return (
                        True,
                        True,
                        f"Backup schema ({snapshot_revision}) is older than app ({app_revision}). "
                        "Forward migration required.",
                    )
                except RangeNotAncestorError:
                    pass

                # Check if app_revision is an ancestor of snapshot_revision (newer backup -> unsupported downgrade)
                try:
                    list(script.iterate_revisions(snapshot_revision, app_revision))
                    return (
                        False,
                        False,
                        f"Backup schema ({snapshot_revision}) is newer than application ({app_revision}). "
                        "Downgrade unsupported.",
                    )
                except RangeNotAncestorError:
                    pass
            except Exception as exc:
                logger.warning("Could not trace Alembic revision ancestry: %s", exc)

        # Fallback heuristic
        if snapshot_revision != app_revision:
            return (
                True,
                True,
                f"Schema revision mismatch ({snapshot_revision} vs {app_revision}). "
                "Staged migration evaluation required.",
            )

        return True, False, "Compatible."

    def apply_staged_migration(self, staged_db_path: Path) -> str | None:
        """Run Alembic upgrade head strictly on the staged database snapshot.

        LIVE DATABASE IS NEVER TOUCHED.
        """
        alembic_ini = self._backend_root / "alembic.ini"
        if not alembic_ini.is_file():
            raise RecoveryDatabaseError(f"alembic.ini not found at '{alembic_ini}'; cannot execute staged migration.")

        staged_url = f"sqlite+aiosqlite:///{staged_db_path.resolve().as_posix()}"
        logger.info("Executing forward schema migration strictly against staged database: '%s'", staged_url)

        try:
            import concurrent.futures

            from alembic import command
            from alembic.config import Config

            cfg = Config(str(alembic_ini))
            cfg.attributes["configure_logger"] = False
            cfg.set_main_option("script_location", str(self._backend_root / "alembic"))
            cfg.set_main_option("sqlalchemy.url", staged_url)

            def _run_upgrade() -> None:
                command.upgrade(cfg, "head")

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_upgrade)
                future.result(timeout=60.0)

        except Exception as exc:
            raise RecoveryCompatibilityError(
                f"Staged database forward schema migration failed: {exc}. Live database remains untouched and intact."
            ) from exc

        # Verify staged database integrity post-migration
        valid, msg, new_rev = self.validate_sqlite_file(staged_db_path)
        if not valid:
            raise RecoveryDatabaseError(f"Staged database integrity check failed after forward migration: {msg}")

        logger.info("Staged forward schema migration succeeded. New revision: '%s'", new_rev)
        return new_rev

    def execute_database_swap(
        self,
        staged_db_path: Path,
        live_db_path: Path,
        recovery_id: str,
    ) -> dict[str, str]:
        """Perform physical file replacement of live database with staged snapshot.

        Preserves live database files to .rollback_<recovery_id>.
        Returns map of preserved rollback paths.
        """
        if not staged_db_path.is_file():
            raise RecoveryDatabaseError(f"Staged database snapshot not found: '{staged_db_path}'")

        live_db_path = live_db_path.resolve()
        live_db_path.parent.mkdir(parents=True, exist_ok=True)
        rollback_sources: dict[str, str] = {}

        # 1. Move live database file to rollback if it exists
        if live_db_path.is_file():
            rollback_db = live_db_path.with_name(f"{live_db_path.name}{ROLLBACK_SUFFIX}{recovery_id}")
            try:
                os.replace(live_db_path, rollback_db)
                rollback_sources["database"] = str(rollback_db)
                logger.info("Preserved live database to rollback: '%s'", rollback_db)
            except OSError as exc:
                raise RecoveryDatabaseError(f"Failed to preserve live database to rollback: {exc}") from exc

        # 2. Move live WAL and SHM files to rollback if they exist
        wal_path = live_db_path.with_name(f"{live_db_path.name}-wal")
        if wal_path.is_file():
            rollback_wal = live_db_path.with_name(f"{live_db_path.name}-wal{ROLLBACK_SUFFIX}{recovery_id}")
            try:
                os.replace(wal_path, rollback_wal)
                rollback_sources["database_wal"] = str(rollback_wal)
            except OSError as exc:
                logger.warning("Failed to move WAL file to rollback: %s", exc)

        shm_path = live_db_path.with_name(f"{live_db_path.name}-shm")
        if shm_path.is_file():
            rollback_shm = live_db_path.with_name(f"{live_db_path.name}-shm{ROLLBACK_SUFFIX}{recovery_id}")
            try:
                os.replace(shm_path, rollback_shm)
                rollback_sources["database_shm"] = str(rollback_shm)
            except OSError as exc:
                logger.warning("Failed to move SHM file to rollback: %s", exc)

        # 3. Copy/Move staged snapshot into place
        try:
            shutil.copy2(staged_db_path, live_db_path)
            # Durably flush to disk
            try:
                with live_db_path.open("r+b") as f:
                    os.fsync(f.fileno())
            except OSError:
                pass

            # Fsync directory on POSIX
            if os.name != "nt":
                try:
                    dir_fd = os.open(str(live_db_path.parent), os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass

            logger.info("Swapped staged database into live target: '%s'", live_db_path)
        except OSError as exc:
            # Swap failed; attempt emergency reverse
            self.execute_reverse_swap(live_db_path, rollback_sources)
            raise RecoveryDatabaseError(f"Failed to swap staged database into live target: {exc}") from exc

        return rollback_sources

    def execute_reverse_swap(
        self,
        live_db_path: Path,
        rollback_sources: dict[str, str],
    ) -> None:
        """Roll back live database to preserved rollback snapshot."""
        logger.warning("Executing reverse swap for database on '%s'...", live_db_path)
        rollback_db_str = rollback_sources.get("database")
        if not rollback_db_str:
            logger.info("No database rollback source was recorded; live DB did not exist previously.")
            if live_db_path.is_file():
                live_db_path.unlink(missing_ok=True)
            return

        rollback_db = Path(rollback_db_str)
        if not rollback_db.is_file():
            raise RecoveryDatabaseError(f"Rollback database source file is missing: '{rollback_db}'. Cannot roll back.")

        try:
            os.replace(rollback_db, live_db_path)
            try:
                with live_db_path.open("r+b") as f:
                    os.fsync(f.fileno())
            except OSError:
                pass
            logger.info("Successfully restored live database from rollback source: '%s'", rollback_db)
        except OSError as exc:
            raise RecoveryDatabaseError(f"Critical error restoring database from rollback source: {exc}") from exc

        # Restore WAL/SHM if present
        wal_str = rollback_sources.get("database_wal")
        if wal_str and Path(wal_str).is_file():
            wal_target = live_db_path.with_name(f"{live_db_path.name}-wal")
            with contextlib.suppress(OSError):
                os.replace(Path(wal_str), wal_target)

        shm_str = rollback_sources.get("database_shm")
        if shm_str and Path(shm_str).is_file():
            shm_target = live_db_path.with_name(f"{live_db_path.name}-shm")
            with contextlib.suppress(OSError):
                os.replace(Path(shm_str), shm_target)
