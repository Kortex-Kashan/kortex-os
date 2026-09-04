"""Unit tests for Backup Engine capture subsystem (Database & Storage)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kortex.engines.backup.capture import DatabaseSnapshotCapture, StoragePayloadCapture
from kortex.engines.backup.constants import BackupComponentType
from kortex.engines.backup.exceptions import BackupStorageError


@pytest.mark.asyncio
async def test_database_snapshot_capture_success(tmp_path: Path) -> None:
    """Verify consistent SQLite online backup capture."""
    source_db = tmp_path / "live.db"
    dest_db = tmp_path / "snap.db"

    # Create live database with schema and data
    conn = sqlite3.connect(source_db)
    with conn:
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT);")
        conn.execute("INSERT INTO users VALUES ('u1', 'Alice'), ('u2', 'Bob');")
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);")
        conn.execute("INSERT INTO alembic_version VALUES ('m7_prod_hardening');")
    conn.close()

    capture = DatabaseSnapshotCapture(page_step=50)
    entry, schema_rev = await capture.capture_database(source_db, dest_db)

    assert entry.component_type == BackupComponentType.DATABASE
    assert entry.relative_path == "database/kortex_snapshot.db"
    assert entry.size_bytes > 0
    assert len(entry.sha256) == 64
    assert schema_rev == "m7_prod_hardening"
    assert dest_db.is_file()

    # Verify snapshot contents
    snap_conn = sqlite3.connect(dest_db)
    cur = snap_conn.cursor()
    cur.execute("SELECT count(*) FROM users;")
    count = cur.fetchone()[0]
    snap_conn.close()
    assert count == 2


@pytest.mark.asyncio
async def test_database_snapshot_cold_start_empty(tmp_path: Path) -> None:
    """Verify handling when live database file does not exist yet."""
    non_existent = tmp_path / "no_such_db.db"
    dest_db = tmp_path / "snap_empty.db"

    capture = DatabaseSnapshotCapture()
    entry, schema_rev = await capture.capture_database(non_existent, dest_db)

    assert dest_db.is_file()
    assert schema_rev is None
    assert entry.metadata.get("status") == "empty"


@pytest.mark.asyncio
async def test_database_snapshot_corruption_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that a corrupted snapshot triggers BackupStorageError."""
    source_db = tmp_path / "corrupted_source.db"
    dest_db = tmp_path / "snap_corrupted.db"

    # Write a database file
    conn = sqlite3.connect(source_db)
    with conn:
        conn.execute("CREATE TABLE items (val TEXT);")
        conn.execute("INSERT INTO items VALUES ('valid');")
    conn.close()

    capture = DatabaseSnapshotCapture()

    def corrupt_sync(src: Path, dest: Path, step: int) -> str | None:
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(capture, "_run_online_backup_sync", corrupt_sync)

    with pytest.raises(BackupStorageError, match="SQLite online backup failed"):
        await capture.capture_database(source_db, dest_db)


def test_storage_payload_capture(tmp_path: Path) -> None:
    """Verify storage scanning, exclusion of backups and temporary files."""
    storage_root = tmp_path / "storage_data"
    storage_root.mkdir()

    files_dir = storage_root / "files"
    files_dir.mkdir()
    (files_dir / "doc1.pdf").write_bytes(b"document-content-1")
    (files_dir / "doc2.png").write_bytes(b"image-content-2")

    # Files that must be skipped
    (files_dir / "temp.tmp").write_bytes(b"temporary")
    cache_dir = storage_root / ".cache"
    cache_dir.mkdir()
    (cache_dir / "cached.dat").write_bytes(b"cached")
    backups_dir = storage_root / "backups"
    backups_dir.mkdir()
    (backups_dir / "prior.kortex-backup").write_bytes(b"prior-backup")

    capture = StoragePayloadCapture(storage_root)
    collected, _checksums, total_size = capture.scan_storage_files()

    # Only doc1.pdf and doc2.png should be captured
    rel_paths = [c[1] for c in collected]
    assert len(collected) == 2
    assert "storage/files/doc1.pdf" in rel_paths
    assert "storage/files/doc2.png" in rel_paths
    assert not any("prior.kortex-backup" in p for p in rel_paths)
    assert not any("temp.tmp" in p for p in rel_paths)
    assert not any(".cache" in p for p in rel_paths)
    assert total_size == len(b"document-content-1") + len(b"image-content-2")
