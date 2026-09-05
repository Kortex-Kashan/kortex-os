"""End-to-end integration tests for KORTEX Recovery Engine."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.engine import BackupEngine
from kortex.engines.backup.models import BackupConfig, CreateBackupRequest
from kortex.engines.recovery.constants import RecoveryJournalPhase, RecoveryState
from kortex.engines.recovery.crypto import RecoveryCryptoManager
from kortex.engines.recovery.engine import RecoveryEngine
from kortex.engines.recovery.exceptions import (
    RecoveryRollbackError,
    RecoveryValidationError,
)
from kortex.engines.recovery.journal import RecoveryJournalManager
from kortex.engines.recovery.models import (
    ChecksumsMetadata,
    RecoveryConfig,
    RecoveryJournalEntry,
    RollbackState,
    TargetIdentity,
    VerificationState,
)
from kortex.engines.security.models import PrincipalType, SecurityPrincipal


def create_sqlite_database(path: Path, table_name: str, sample_text: str) -> None:
    """Helper to initialize a real SQLite database file."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, content TEXT);")
    cur.execute(f"INSERT INTO {table_name} (content) VALUES (?);", (sample_text,))  # noqa: S608
    cur.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);")
    cur.execute("INSERT INTO alembic_version (version_num) VALUES ('81d6d64c51ba');")
    conn.commit()
    conn.close()


def make_admin_context() -> CapabilityExecutionContext:
    principal = SecurityPrincipal(
        principal_id="admin-user",
        tenant_id="primary",
        principal_type=PrincipalType.USER,
        roles=["TENANT_ADMIN"],
    )
    return CapabilityExecutionContext(
        request_id="req-int-1",
        correlation_id="corr-int-1",
        capability_name="kortex.recovery.create",
        principal=principal,
        tenant_id="primary",
    )


@pytest.mark.asyncio
async def test_end_to_end_backup_and_recovery_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify complete lifecycle: Backup creation -> State mutation -> Full Recovery -> Verification."""
    key = b"\x77" * 32
    storage_root = tmp_path / "storage_data"
    backups_dir = storage_root / "backups"
    live_db_path = storage_root / "kortex_local.db"
    docs_dir = storage_root / "documents"

    storage_root.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(storage_root))
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{live_db_path.resolve().as_posix()}")

    # 1. Initialize original live state
    create_sqlite_database(live_db_path, "business_records", "Golden State Record 1")
    (docs_dir / "golden_doc.txt").write_text("Golden document content", encoding="utf-8")

    # 2. Capture backup via BackupEngine
    backup_crypto = BackupCryptoManager(key=key)
    backup_cfg = BackupConfig(
        backup_directory=str(backups_dir),
        storage_root=str(storage_root),
        live_db_path=str(live_db_path),
    )
    backup_engine = BackupEngine(config=backup_cfg, crypto_manager=backup_crypto)
    await backup_engine.initialize()

    bck_res = await backup_engine.create_backup(CreateBackupRequest())
    assert bck_res.backup_id is not None
    backup_id = bck_res.backup_id

    # 3. Corrupt/Mutate live state (simulating data loss)
    live_db_path.unlink(missing_ok=True)
    create_sqlite_database(live_db_path, "business_records", "CORRUPTED UNWANTED STATE")
    (docs_dir / "golden_doc.txt").write_text("DESTROYED CONTENT", encoding="utf-8")
    (docs_dir / "unwanted_new_file.txt").write_text("Junk", encoding="utf-8")

    # 4. Initialize RecoveryEngine
    recovery_crypto = RecoveryCryptoManager(key=key)
    recovery_cfg = RecoveryConfig(
        staging_directory=str(tmp_path / "staging"),
        journal_directory=str(tmp_path / "recovery_journal"),
        backup_directory=str(backups_dir),
    )
    recovery_engine = RecoveryEngine(
        config=recovery_cfg,
        crypto_manager=recovery_crypto,
    )
    # Mock kernel with backup engine so pre-recovery safety checkpoint can be created
    kernel_mock = MagicMock()
    kernel_mock.get_engine = MagicMock(return_value=backup_engine)
    kernel_mock.register_capability = MagicMock()
    await recovery_engine.initialize(kernel=kernel_mock)

    ctx = make_admin_context()

    # 5. Execute recovery
    report_dict = await recovery_engine.handle_recovery_create(
        backup_id=backup_id,
        confirm_destructive_restore=True,
        execution_context=ctx,
    )

    assert report_dict["state"] == RecoveryState.COMPLETED.value
    assert report_dict["safety_checkpoint_id"] is not None

    # 6. Verify restored state
    conn = sqlite3.connect(live_db_path)
    cur = conn.cursor()
    cur.execute("SELECT content FROM business_records;")
    row = cur.fetchone()
    assert row[0] == "Golden State Record 1"
    conn.close()

    assert (docs_dir / "golden_doc.txt").read_text(encoding="utf-8") == "Golden document content"
    # Unwanted file from corrupted session should be gone
    assert not (docs_dir / "unwanted_new_file.txt").exists()


@pytest.mark.asyncio
async def test_recovery_requires_explicit_confirmation(tmp_path: Path) -> None:
    """Verify recovery aborts if confirm_destructive_restore is False."""
    key = b"\x88" * 32
    recovery_crypto = RecoveryCryptoManager(key=key)
    recovery_cfg = RecoveryConfig(
        staging_directory=str(tmp_path / "staging"),
        journal_directory=str(tmp_path / "journal"),
    )
    engine = RecoveryEngine(config=recovery_cfg, crypto_manager=recovery_crypto)
    await engine.initialize()

    ctx = make_admin_context()
    with pytest.raises(RecoveryValidationError, match="Destructive system recovery requires explicit confirmation"):
        await engine.handle_recovery_create(
            backup_id="bck-001",
            confirm_destructive_restore=False,
            execution_context=ctx,
        )


@pytest.mark.asyncio
async def test_recovery_rollback_on_failed_post_restore_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that if post-restore verification fails, an automated rollback is executed."""
    key = b"\x99" * 32
    storage_root = tmp_path / "storage_data"
    backups_dir = storage_root / "backups"
    live_db_path = storage_root / "kortex_local.db"
    docs_dir = storage_root / "documents"

    storage_root.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(storage_root))
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{live_db_path.resolve().as_posix()}")

    # 1. Initialize original live state
    create_sqlite_database(live_db_path, "test_table", "Original Before Recovery")
    (docs_dir / "test_file.txt").write_text("Original Doc Content", encoding="utf-8")

    # 2. Capture backup
    backup_crypto = BackupCryptoManager(key=key)
    backup_cfg = BackupConfig(
        backup_directory=str(backups_dir),
        storage_root=str(storage_root),
        live_db_path=str(live_db_path),
    )
    backup_engine = BackupEngine(config=backup_cfg, crypto_manager=backup_crypto)
    await backup_engine.initialize()

    bck_res = await backup_engine.create_backup(CreateBackupRequest())
    backup_id = bck_res.backup_id

    # 3. Mutate live state before starting recovery
    live_db_path.unlink(missing_ok=True)
    create_sqlite_database(live_db_path, "test_table", "State When Restore Started")
    (docs_dir / "test_file.txt").write_text("Doc State When Restore Started", encoding="utf-8")

    # 4. Initialize RecoveryEngine
    recovery_crypto = RecoveryCryptoManager(key=key)
    recovery_cfg = RecoveryConfig(
        staging_directory=str(tmp_path / "staging"),
        journal_directory=str(storage_root / ".recovery"),
        backup_directory=str(backups_dir),
    )
    recovery_engine = RecoveryEngine(
        config=recovery_cfg,
        crypto_manager=recovery_crypto,
    )
    kernel_mock = MagicMock()
    kernel_mock.get_engine = MagicMock(return_value=backup_engine)
    kernel_mock.register_capability = MagicMock()
    kernel_mock.db = MagicMock()
    kernel_mock.db.disconnect = AsyncMock()
    kernel_mock.db.connect = AsyncMock()
    await recovery_engine.initialize(kernel=kernel_mock)

    # 5. Inject failure during post-restore verification step
    orig_validate = recovery_engine._db_restorer.validate_sqlite_file
    failed_once = False

    def mock_validate(path: Path) -> tuple[bool, str, list[str]]:
        nonlocal failed_once
        if path.resolve() == live_db_path.resolve() and not failed_once:
            failed_once = True
            return (False, "Simulated post-restore corruption", ["page corruption"])
        return orig_validate(path)

    monkeypatch.setattr(recovery_engine._db_restorer, "validate_sqlite_file", mock_validate)

    ctx = make_admin_context()

    # 6. Execute recovery — must trigger automated rollback
    with pytest.raises(RecoveryRollbackError, match="failed and was successfully rolled back"):
        await recovery_engine.handle_recovery_create(
            backup_id=backup_id,
            confirm_destructive_restore=True,
            execution_context=ctx,
        )

    assert recovery_engine.recoveries_rolled_back_count == 1

    # 7. Verify live state was rolled back to the state that existed when restore started
    conn = sqlite3.connect(live_db_path)
    cur = conn.cursor()
    cur.execute("SELECT content FROM test_table;")
    row = cur.fetchone()
    assert row[0] == "State When Restore Started"
    conn.close()

    assert (docs_dir / "test_file.txt").read_text(encoding="utf-8") == "Doc State When Restore Started"


@pytest.mark.asyncio
async def test_recovery_startup_detects_interrupted_destructive_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that RecoveryEngine startup sweep reconciles interrupted destructive journal."""
    key = b"\xaa" * 32
    storage_root = tmp_path / "storage_data"
    journal_dir = storage_root / ".recovery"
    live_db_path = storage_root / "kortex_local.db"
    docs_dir = storage_root / "documents"

    storage_root.mkdir(parents=True, exist_ok=True)
    journal_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(storage_root))
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{live_db_path.resolve().as_posix()}")

    # Live DB currently has "Corrupted Interrupted State"
    create_sqlite_database(live_db_path, "demo", "Corrupted Interrupted State")
    (docs_dir / "doc.txt").write_text("Interrupted doc", encoding="utf-8")

    # Preserved rollback files
    recovery_id = "rec-interrupted-001"
    rb_db = live_db_path.parent / f"{live_db_path.name}.rollback_{recovery_id}"
    create_sqlite_database(rb_db, "demo", "Safe Rollback State")

    rb_docs = docs_dir.parent / f"{docs_dir.name}.rollback_{recovery_id}"
    rb_docs.mkdir(parents=True, exist_ok=True)
    (rb_docs / "doc.txt").write_text("Safe rollback doc", encoding="utf-8")

    rollback_sources = {
        "database": str(rb_db),
        "storage_documents": str(rb_docs),
    }

    journal_mgr = RecoveryJournalManager(journal_dir / "journal.json")
    target_ident = TargetIdentity(
        instance_id="inst-1",
        database_path=str(live_db_path),
        storage_root=str(storage_root),
    )
    rb_state = RollbackState(
        safety_checkpoint_id="chk-001",
        rollback_sources=rollback_sources,
        is_protected=True,
    )
    entry = RecoveryJournalEntry(
        recovery_id=recovery_id,
        backup_id="bck-orig-001",
        target_identity=target_ident,
        created_at="2026-09-05T00:00:00Z",
        updated_at="2026-09-05T00:00:00Z",
        current_phase=RecoveryJournalPhase.STORAGE_SWAP_COMPLETE,
        rollback_state=rb_state,
        verification_state=VerificationState(),
        checksums=ChecksumsMetadata(artifact_sha256="0" * 64),
    )
    journal_mgr.write_journal(entry)

    # Initialize new RecoveryEngine instance — boot sweep should detect and rollback
    recovery_crypto = RecoveryCryptoManager(key=key)
    recovery_cfg = RecoveryConfig(
        staging_directory=str(tmp_path / "staging"),
        journal_directory=str(journal_dir),
    )
    engine = RecoveryEngine(config=recovery_cfg, crypto_manager=recovery_crypto)
    await engine.initialize()

    # Verify journal was processed and archived as rolled_back
    assert journal_mgr.load_journal() is None
    archived = list(journal_dir.glob("journal.json.rolled_back.*"))
    assert len(archived) == 1

    # Verify live DB and docs were restored to safe rollback content
    conn = sqlite3.connect(live_db_path)
    cur = conn.cursor()
    cur.execute("SELECT content FROM demo;")
    row = cur.fetchone()
    assert row[0] == "Safe Rollback State"
    conn.close()

    assert (docs_dir / "doc.txt").read_text(encoding="utf-8") == "Safe rollback doc"
