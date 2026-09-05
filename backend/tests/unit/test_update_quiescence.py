"""Unit tests for Update Engine runtime quiescence and concurrency control.

Phase 7 — Production Hardening — Update Engine.
Verifies exclusive maintenance lock acquisition, stale PID cleanup, concurrency rejection,
and database connection pool draining.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kortex.engines.update.exceptions import (
    UpdateConcurrencyError,
    UpdateQuiescenceError,
)
from kortex.engines.update.quiescence import UpdateQuiescenceManager


@pytest.mark.asyncio
async def test_acquire_and_release_maintenance_lock(tmp_path: Path) -> None:
    """Verify clean acquisition and release of maintenance lock file."""
    lock_file = tmp_path / ".update" / "maintenance.lock"
    mgr = UpdateQuiescenceManager(lock_file_path=lock_file)

    await mgr.acquire_maintenance_lock(update_id="upd-lock-01")
    assert lock_file.is_file()

    # Release lock
    mgr.release_maintenance_lock()
    assert not lock_file.exists()


@pytest.mark.asyncio
async def test_concurrent_lock_rejected(tmp_path: Path) -> None:
    """Verify active lock by current process/live PID rejects concurrent acquisition."""
    lock_file = tmp_path / ".update" / "maintenance.lock"
    mgr1 = UpdateQuiescenceManager(lock_file_path=lock_file)
    mgr2 = UpdateQuiescenceManager(lock_file_path=lock_file)

    await mgr1.acquire_maintenance_lock(update_id="upd-primary")

    with pytest.raises(UpdateConcurrencyError) as exc_info:
        await mgr2.acquire_maintenance_lock(update_id="upd-secondary")
    assert "actively running under PID" in str(exc_info.value)

    mgr1.release_maintenance_lock()


@pytest.mark.asyncio
async def test_stale_lock_replaced_when_pid_dead(tmp_path: Path) -> None:
    """Verify stale lock file from dead PID is safely overridden."""
    lock_file = tmp_path / ".update" / "maintenance.lock"
    mgr = UpdateQuiescenceManager(lock_file_path=lock_file)

    # Mock _pid_exists to return False for a dead PID
    with patch.object(mgr, "_pid_exists", return_value=False):
        # Write dummy lock with dead PID
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text('{"update_id": "old-dead", "pid": 999999}')

        # Should replace the lock file without error
        await mgr.acquire_maintenance_lock(update_id="upd-replacement")
        assert lock_file.is_file()
        mgr.release_maintenance_lock()


@pytest.mark.asyncio
async def test_drain_connections_success(tmp_path: Path) -> None:
    """Verify database connection pool draining calls disconnect on kernel.db."""
    lock_file = tmp_path / ".update" / "maintenance.lock"
    mgr = UpdateQuiescenceManager(lock_file_path=lock_file)

    mock_kernel = MagicMock()
    mock_db = MagicMock()
    mock_db.disconnect = AsyncMock()
    mock_kernel.db = mock_db

    await mgr.drain_and_disconnect_database(kernel=mock_kernel)
    mock_db.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_drain_connections_timeout(tmp_path: Path) -> None:
    """Verify database connection pool timeout raises UpdateQuiescenceError."""
    lock_file = tmp_path / ".update" / "maintenance.lock"
    mgr = UpdateQuiescenceManager(lock_file_path=lock_file, timeout_seconds=0.01)

    mock_kernel = MagicMock()
    mock_db = MagicMock()

    async def slow_disconnect() -> None:
        await asyncio.sleep(0.5)

    mock_db.disconnect = slow_disconnect
    mock_kernel.db = mock_db

    with pytest.raises(UpdateQuiescenceError) as exc_info:
        await mgr.drain_and_disconnect_database(kernel=mock_kernel)
    assert "Timed out" in str(exc_info.value)
