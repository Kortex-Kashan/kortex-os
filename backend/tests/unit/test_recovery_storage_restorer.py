"""Unit tests for Recovery Engine storage restorer, subtree isolation, and referential checks."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kortex.engines.recovery.storage_restorer import StorageRestorer


def test_storage_swap_and_reverse_rollback(tmp_path: Path) -> None:
    """Verify managed storage subtrees swap with rollback preservation and reversal."""
    live_storage = tmp_path / "storage_data"
    live_docs = live_storage / "documents"
    live_docs.mkdir(parents=True, exist_ok=True)
    (live_docs / "initial.txt").write_text("initial live content", encoding="utf-8")

    # Staged storage
    staged_storage = tmp_path / "staged_storage"
    staged_docs = staged_storage / "documents"
    staged_docs.mkdir(parents=True, exist_ok=True)
    (staged_docs / "restored.txt").write_text("restored content from backup", encoding="utf-8")

    restorer = StorageRestorer(storage_root=live_storage)

    # 1. Swap storage subtrees
    rollback_sources = restorer.execute_storage_swap(
        staged_storage_dir=staged_storage,
        recovery_id="rec-store-001",
    )
    assert "storage_documents" in rollback_sources
    rollback_path = Path(rollback_sources["storage_documents"])
    assert rollback_path.exists()

    # Verify live storage now contains restored file
    assert not (live_docs / "initial.txt").exists()
    assert (live_docs / "restored.txt").exists()
    assert (live_docs / "restored.txt").read_text(encoding="utf-8") == "restored content from backup"

    # 2. Reverse swap rollback
    restorer.execute_reverse_swap(rollback_sources)

    # Verify live storage is restored to initial content
    assert (live_docs / "initial.txt").exists()
    assert not (live_docs / "restored.txt").exists()


def test_storage_excludes_backups_and_system_dirs(tmp_path: Path) -> None:
    """Verify swap preserves existing backups, .tmp, and .recovery directories untouched."""
    live_storage = tmp_path / "storage_data"
    backups_dir = live_storage / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    (backups_dir / "my_backup.kortex-backup").write_bytes(b"existing backup")

    recovery_dir = live_storage / ".recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    (recovery_dir / "journal.json").write_text("{}", encoding="utf-8")

    # Staged storage attempting to overwrite documents
    staged_storage = tmp_path / "staged_storage"
    staged_docs = staged_storage / "documents"
    staged_docs.mkdir(parents=True, exist_ok=True)
    (staged_docs / "doc.txt").write_text("staged doc", encoding="utf-8")

    restorer = StorageRestorer(storage_root=live_storage)

    restorer.execute_storage_swap(
        staged_storage_dir=staged_storage,
        recovery_id="rec-store-002",
    )

    # Critical directories must remain untouched
    assert (backups_dir / "my_backup.kortex-backup").exists()
    assert (recovery_dir / "journal.json").exists()


def test_referential_consistency_validation_success(tmp_path: Path) -> None:
    """Verify referential consistency validation succeeds when records match files."""
    db_path = tmp_path / "staged.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, storage_path TEXT);")
    cur.execute("INSERT INTO documents VALUES ('doc-1', 'documents/doc1.pdf');")
    conn.commit()
    conn.close()

    storage_root = tmp_path / "storage"
    doc_file = storage_root / "documents" / "doc1.pdf"
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.write_bytes(b"%PDF-1.4...")

    restorer = StorageRestorer(storage_root=storage_root)
    is_consistent, missing, _warnings = restorer.verify_referential_consistency(
        db_path=db_path,
        storage_dir=storage_root,
    )
    assert is_consistent is True
    assert len(missing) == 0


def test_referential_consistency_validation_missing_file_failure(tmp_path: Path) -> None:
    """Verify referential consistency fails when database record references a missing file."""
    db_path = tmp_path / "staged.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, storage_path TEXT);")
    cur.execute("INSERT INTO documents VALUES ('doc-1', 'documents/missing_doc.pdf');")
    conn.commit()
    conn.close()

    storage_root = tmp_path / "storage"
    (storage_root / "documents").mkdir(parents=True, exist_ok=True)

    restorer = StorageRestorer(storage_root=storage_root)
    is_consistent, missing, _warnings = restorer.verify_referential_consistency(
        db_path=db_path,
        storage_dir=storage_root,
    )
    assert is_consistent is False
    assert len(missing) == 1
    assert "document:doc-1" in missing[0]
