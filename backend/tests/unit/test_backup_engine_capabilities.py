"""Unit tests for Backup Engine lifecycle, capability dispatch, and concurrency control."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kortex.core.base_engine import EngineState
from kortex.engines.backup.constants import BackupScope, BackupState
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.engine import BackupEngine
from kortex.engines.backup.exceptions import BackupConcurrencyError
from kortex.engines.backup.models import BackupConfig, CreateBackupRequest


@pytest.mark.asyncio
async def test_engine_lifecycle(tmp_path: Path) -> None:
    """Verify clean engine initialization, startup, and shutdown."""
    key = b"\x44" * 32
    crypto = BackupCryptoManager(key=key)
    config = BackupConfig(
        backup_directory=str(tmp_path / "backups"),
        scheduled_interval_seconds=3600,
    )
    engine = BackupEngine(config=config, crypto_manager=crypto)
    assert engine.state == EngineState.UNINITIALIZED

    kernel = MagicMock()
    kernel.register_capability = MagicMock()
    kernel.container = MagicMock()
    kernel.container.has = MagicMock(return_value=False)

    await engine.initialize(kernel=kernel)
    assert engine.state == EngineState.READY
    assert kernel.register_capability.call_count == 6

    await engine.start()
    assert engine.state == EngineState.RUNNING
    assert len(engine.background_tasks) == 1

    await engine.stop()
    assert engine.state == EngineState.STOPPED
    assert len(engine.background_tasks) == 0


@pytest.mark.asyncio
async def test_engine_create_and_handlers(tmp_path: Path) -> None:
    """Verify all 6 capability handlers execute cleanly."""
    key = b"\x44" * 32
    crypto = BackupCryptoManager(key=key)
    config = BackupConfig(backup_directory=str(tmp_path / "backups"))
    engine = BackupEngine(config=config, crypto_manager=crypto)

    # Initialize
    await engine.initialize()

    # 1. Create backup via handler
    create_res = await engine.handle_backup_create(scope="FULL_INSTANCE")
    assert create_res["state"] == BackupState.VALID.value
    backup_id = create_res["backup_id"]

    # 2. List backups via handler
    list_res = await engine.handle_backup_list()
    assert list_res["total_count"] == 1
    assert list_res["backups"][0]["backup_id"] == backup_id

    # 3. Get backup via handler
    get_res = await engine.handle_backup_get(backup_id=backup_id)
    assert get_res["backup"]["backup_id"] == backup_id

    # 4. Verify backup via handler
    verify_res = await engine.handle_backup_verify(backup_id=backup_id)
    assert verify_res["is_valid"] is True
    assert verify_res["checksum_verified"] is True

    # 5. Diagnostics get via handler
    diag_res = await engine.handle_backup_diagnostics_get()
    assert diag_res["engine_name"] == "backup"
    assert diag_res["total_backups"] == 1

    # 6. Delete backup via handler
    del_res = await engine.handle_backup_delete(backup_id=backup_id)
    assert del_res["deleted"] is True

    # Post-delete list is empty
    list_after = await engine.handle_backup_list()
    assert list_after["total_count"] == 0


@pytest.mark.asyncio
async def test_engine_concurrency_lock_collision(tmp_path: Path) -> None:
    """Verify that concurrent backup creation attempts trigger BackupConcurrencyError."""
    key = b"\x44" * 32
    crypto = BackupCryptoManager(key=key)
    config = BackupConfig(backup_directory=str(tmp_path / "backups"))
    engine = BackupEngine(config=config, crypto_manager=crypto)
    await engine.initialize()

    # Artificially acquire the lock to simulate in-progress backup
    await engine._backup_lock.acquire()

    try:
        with pytest.raises(BackupConcurrencyError, match="Another backup operation is currently in progress"):
            await engine.create_backup(CreateBackupRequest(scope=BackupScope.FULL_INSTANCE))
    finally:
        engine._backup_lock.release()
