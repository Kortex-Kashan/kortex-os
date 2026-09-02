"""
Integration tests for KORTEX Storage Engine (Milestone 9).
"""

from __future__ import annotations

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.kernel import Kernel
from kortex.engines.storage.engine import StorageEngine


@pytest.mark.asyncio
async def test_storage_engine_kernel_boot_integration(tmp_path) -> None:
    """Integration test: Register StorageEngine with Kernel, boot system, resolve capabilities, and operate stores."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "integration_storage"))

    # Register Storage Engine into Kernel
    kernel.register_engine(storage_engine)

    # Boot Kernel (initializes DB, connects boot sequence, initializes StorageEngine)
    await kernel.boot()

    assert kernel.state.value == "RUNNING"
    assert storage_engine.state == EngineState.RUNNING

    # Resolve StorageEngine via DI container
    resolved_engine = kernel.container.resolve("engine.storage")
    assert resolved_engine is storage_engine

    # Test Capability Registry lookup for storage capabilities
    cap_file = kernel.get_capability("kortex.storage.file.store")
    assert cap_file.provider == "storage"

    cap_obj = kernel.get_capability("kortex.storage.object.put")
    assert cap_obj.provider == "storage"

    # Invoke capability handler directly via the M8 test-only accessor
    file_meta = await kernel._registry_engine.get_raw_handler_for_testing("kortex.storage.file.store")(
        "docs/integration.txt", b"Integration Payload"
    )
    assert file_meta.relative_path == "docs/integration.txt"

    # Verify health check aggregation
    health_report = await kernel.health_check()
    assert health_report["kernel_state"] == "RUNNING"
    assert "engines" in health_report["system_health"]
    assert "storage" in health_report["system_health"]["engines"]
    assert health_report["system_health"]["engines"]["storage"]["healthy"] is True

    await kernel.shutdown()
    assert kernel.state.value == "STOPPED"
    assert storage_engine.state == EngineState.STOPPED
