"""Unit tests for Backup Engine event publisher and diagnostics adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kortex.engines.backup.constants import BackupScope
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.engine import BackupEngine
from kortex.engines.backup.events import BackupEventPublisher
from kortex.engines.storage.interfaces import IEngineDiagnostics


@pytest.mark.asyncio
async def test_event_publisher_without_kernel() -> None:
    """Verify publisher suppresses events gracefully when Kernel is unbound."""
    publisher = BackupEventPublisher(kernel=None)
    res = await publisher.emit_requested("b-test", BackupScope.FULL_INSTANCE)
    assert res is False


@pytest.mark.asyncio
async def test_event_publisher_with_kernel() -> None:
    """Verify publisher emits lifecycle events onto Kernel event bus."""
    kernel = MagicMock()
    kernel.publish_event = AsyncMock(return_value=MagicMock(event_id="ev-123", subscribers_notified=2))

    publisher = BackupEventPublisher(kernel=kernel)

    ok = await publisher.emit_completed(
        backup_id="b-comp",
        scope=BackupScope.FULL_INSTANCE,
        file_size_bytes=4096,
        is_encrypted=True,
    )
    assert ok is True
    assert kernel.publish_event.called

    # Check payload does not leak secret material
    call_args = kernel.publish_event.call_args[1]
    payload = call_args["payload"]
    assert payload["backup_id"] == "b-comp"
    assert "key" not in payload
    assert "token" not in payload


@pytest.mark.asyncio
async def test_diagnostics_implements_iengine_diagnostics() -> None:
    """Verify BackupEngine implements the canonical IEngineDiagnostics interface."""
    key = b"\x33" * 32
    crypto = BackupCryptoManager(key=key)
    engine = BackupEngine(crypto_manager=crypto)
    await engine.initialize()

    assert isinstance(engine, IEngineDiagnostics)

    # Check protocol methods
    st = engine.status()
    assert isinstance(st, str)

    ver = engine.version()
    assert ver == "1.0.0"

    caps = engine.capabilities()
    assert len(caps) == 6
    assert "kortex.backup.create" in caps

    hlth = engine.health()
    assert isinstance(hlth, dict)
    assert "status" in hlth
    assert hlth["healthy"] is True

    mtrx = engine.metrics()
    assert isinstance(mtrx, dict)
    assert "backups_created_total" in mtrx

    diag = engine.diagnostics()
    assert isinstance(diag, dict)
    assert diag["engine_name"] == "backup"
