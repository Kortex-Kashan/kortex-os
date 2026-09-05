"""Unit tests for Recovery Engine capability registration, dispatch handlers, and concurrency control."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.recovery.constants import (
    CAPABILITY_RECOVERY_CREATE,
    CAPABILITY_RECOVERY_DELETE,
    CAPABILITY_RECOVERY_DIAGNOSTICS_GET,
    CAPABILITY_RECOVERY_GET,
    CAPABILITY_RECOVERY_LIST,
    CAPABILITY_RECOVERY_VERIFY,
)
from kortex.engines.recovery.crypto import RecoveryCryptoManager
from kortex.engines.recovery.engine import RecoveryEngine
from kortex.engines.recovery.exceptions import RecoveryConcurrencyError
from kortex.engines.recovery.models import RecoveryConfig
from kortex.engines.security.models import PrincipalType, SecurityPrincipal


def make_context(
    tenant_id: str = "primary",
    principal_id: str = "admin",
) -> CapabilityExecutionContext:
    principal = SecurityPrincipal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        principal_type=PrincipalType.USER,
        roles=["TENANT_ADMIN"],
    )
    return CapabilityExecutionContext(
        request_id="req-123",
        correlation_id="corr-123",
        capability_name="kortex.recovery.test",
        principal=principal,
        tenant_id=tenant_id,
    )


@pytest.mark.asyncio
async def test_recovery_engine_lifecycle(tmp_path: Path) -> None:
    """Verify clean BaseEngine lifecycle progression."""
    crypto = RecoveryCryptoManager(key=b"\x10" * 32)
    config = RecoveryConfig(staging_directory=str(tmp_path / "staging"))
    engine = RecoveryEngine(config=config, crypto_manager=crypto)
    assert engine.state == EngineState.UNINITIALIZED

    kernel = MagicMock()
    kernel.register_capability = MagicMock()

    await engine.initialize(kernel=kernel)
    assert engine.state == EngineState.READY
    # Exactly 6 capabilities registered
    assert kernel.register_capability.call_count == 6

    # Verify registered capability names
    registered_names = {call.kwargs["name"] for call in kernel.register_capability.call_args_list}
    expected_names = {
        CAPABILITY_RECOVERY_CREATE,
        CAPABILITY_RECOVERY_LIST,
        CAPABILITY_RECOVERY_GET,
        CAPABILITY_RECOVERY_VERIFY,
        CAPABILITY_RECOVERY_DELETE,
        CAPABILITY_RECOVERY_DIAGNOSTICS_GET,
    }
    assert registered_names == expected_names

    await engine.start()
    assert engine.state == EngineState.RUNNING

    await engine.stop()
    assert engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_recovery_engine_concurrency_guard(tmp_path: Path) -> None:
    """Verify engine prevents concurrent recovery operations using its active lock."""
    crypto = RecoveryCryptoManager(key=b"\x20" * 32)
    config = RecoveryConfig(staging_directory=str(tmp_path / "staging"))
    engine = RecoveryEngine(config=config, crypto_manager=crypto)
    await engine.initialize()

    ctx = make_context()

    # Manually simulate an active recovery lock
    await engine._recovery_lock.acquire()
    try:
        with pytest.raises(RecoveryConcurrencyError, match=r"Another recovery operation is currently in progress"):
            await engine.handle_recovery_create(
                backup_id="bck-concurrent",
                confirm_destructive_restore=True,
                execution_context=ctx,
            )
    finally:
        engine._recovery_lock.release()


@pytest.mark.asyncio
async def test_recovery_engine_capability_handlers_get_list(tmp_path: Path) -> None:
    """Verify get and list handlers execute with trusted execution context."""
    crypto = RecoveryCryptoManager(key=b"\x30" * 32)
    config = RecoveryConfig(staging_directory=str(tmp_path / "staging"))
    engine = RecoveryEngine(config=config, crypto_manager=crypto)
    await engine.initialize()

    ctx = make_context()

    # List recoveries (should be empty initially)
    list_res = await engine.handle_recovery_list(execution_context=ctx)
    assert "recoveries" in list_res
    assert len(list_res["recoveries"]) == 0

    # Get diagnostics capability
    diag_res = await engine.handle_recovery_diagnostics_get(execution_context=ctx)
    assert diag_res["state"] == "READY"
    assert diag_res["engine_name"] == "recovery"


@pytest.mark.asyncio
async def test_recovery_diagnostics_adapter_conformance(tmp_path: Path) -> None:
    """Verify conformance with IEngineDiagnostics interface."""
    crypto = RecoveryCryptoManager(key=b"\x40" * 32)
    config = RecoveryConfig(staging_directory=str(tmp_path / "staging"))
    engine = RecoveryEngine(config=config, crypto_manager=crypto)
    await engine.initialize()

    health = engine.health()
    assert health["engine"] == "recovery"
    assert health["status"] == "HEALTHY"

    metrics = engine.metrics()
    assert "recoveries_attempted" in metrics
    assert "recoveries_completed" in metrics

    diagnostics = engine.diagnostics()
    assert diagnostics["engine_name"] == "recovery"
