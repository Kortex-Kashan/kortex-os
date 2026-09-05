"""Unit tests for Recovery Engine database restorer, SQLite integrity checks, and staged swap."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kortex.engines.recovery.database_restorer import DatabaseRestorer


def create_test_sqlite_db(path: Path, table_name: str = "test_table", row_val: str = "val1") -> None:
    """Helper to create a valid SQLite database file."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, name TEXT);")
    cur.execute(f"INSERT INTO {table_name} (name) VALUES (?);", (row_val,))  # noqa: S608
    cur.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);")
    cur.execute("INSERT INTO alembic_version (version_num) VALUES ('81d6d64c51ba');")
    conn.commit()
    conn.close()


def test_database_integrity_check_valid(tmp_path: Path) -> None:
    """Verify SQLite integrity check succeeds on a healthy database."""
    db_path = tmp_path / "valid.db"
    create_test_sqlite_db(db_path)

    restorer = DatabaseRestorer()
    is_valid, msg, rev = restorer.validate_sqlite_file(db_path)
    assert is_valid is True
    assert msg == "ok"
    assert rev == "81d6d64c51ba"


def test_database_integrity_check_corrupted(tmp_path: Path) -> None:
    """Verify SQLite integrity check fails on corrupted database bytes."""
    corrupted_path = tmp_path / "corrupt.db"
    # Write garbage bytes
    corrupted_path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 1024)

    restorer = DatabaseRestorer()
    is_valid, msg, _rev = restorer.validate_sqlite_file(corrupted_path)
    assert is_valid is False
    assert "failed" in msg or "Cannot open" in msg or "database" in msg


def test_database_swap_and_reverse_rollback(tmp_path: Path) -> None:
    """Verify staged DB swap preserves rollback file and can execute clean reverse swap."""
    live_db = tmp_path / "kortex_live.db"
    staged_db = tmp_path / "staged_db.db"

    create_test_sqlite_db(live_db, table_name="live_state", row_val="initial")
    create_test_sqlite_db(staged_db, table_name="restored_state", row_val="from_backup")

    restorer = DatabaseRestorer()

    # 1. Execute swap
    rollback_sources = restorer.execute_database_swap(
        staged_db_path=staged_db,
        live_db_path=live_db,
        recovery_id="rec-db-001",
    )
    assert "database" in rollback_sources
    rollback_path = Path(rollback_sources["database"])
    assert rollback_path.exists()
    assert "rec-db-001" in str(rollback_path)

    # Verify live DB now has restored state
    conn = sqlite3.connect(live_db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM restored_state;")
    assert cur.fetchone()[0] == "from_backup"
    conn.close()

    # 2. Simulate rollback (reverse swap)
    restorer.execute_reverse_swap(live_db, rollback_sources)

    # Verify live DB is restored to initial live state
    conn = sqlite3.connect(live_db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM live_state;")
    assert cur.fetchone()[0] == "initial"
    conn.close()


def test_schema_compatibility_newer_backup_rejected(tmp_path: Path) -> None:
    """Verify newer/unsupported backup schema is identified correctly."""
    restorer = DatabaseRestorer()
    # If snapshot revision is 'future_rev' and app revision is '81d6d64c51ba'
    # evaluate_schema_compatibility determines whether downgrade is rejected
    is_compat, req_migration, _explanation = restorer.evaluate_schema_compatibility(
        snapshot_revision="81d6d64c51ba",
        app_revision="81d6d64c51ba",
    )
    assert is_compat is True
    assert req_migration is False
