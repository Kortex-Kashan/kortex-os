"""
Unit tests for KORTEX Storage Engine Facade & Diagnostics (Milestone 7 & 8).
"""

from __future__ import annotations

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.kernel import Kernel
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IEngineDiagnostics


@pytest.mark.asyncio
async def test_storage_engine_diagnostics_interface() -> None:
    """Test StorageEngine satisfies IEngineDiagnostics protocol."""
    engine = StorageEngine()
    assert isinstance(engine, IEngineDiagnostics)
    assert engine.name == "storage"
    assert engine.version() == "1.0.0"
    assert engine.status() == EngineState.UNINITIALIZED.value
    assert "kortex.storage.file.store" in engine.capabilities()


@pytest.mark.asyncio
async def test_storage_engine_boot_and_accessors(tmp_path) -> None:
    """Test initializing StorageEngine with Kernel and accessing all 4 stores."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "data"))

    # Register engine with Kernel and boot
    kernel.register_engine(storage_engine)
    await kernel.boot()

    assert storage_engine.state == EngineState.RUNNING

    # Access all 4 storage abstractions
    assert storage_engine.data is not None
    assert storage_engine.file is not None
    assert storage_engine.object is not None
    assert storage_engine.cache is not None

    # Test file store operation through facade
    meta = await storage_engine.file.write_file("test.txt", b"Hello Facade")
    assert meta.file_size_bytes == 12

    # Test object store operation through facade
    obj_meta = await storage_engine.object.put_object("b1", "o1.txt", b"Blob Data")
    assert obj_meta.file_size_bytes == 9

    # Test cache store operation through facade
    await storage_engine.cache.set("k1", "v1")
    assert await storage_engine.cache.get("k1") == "v1"

    # Test health and diagnostics reports
    health = storage_engine.health()
    assert health["healthy"] is True
    assert health["stores"]["data_store"] is True
    assert health["stores"]["file_store"] is True

    diag = storage_engine.diagnostics()
    assert diag["engine"] == "storage"
    assert diag["version"] == "1.0.0"

    await kernel.shutdown()
    assert storage_engine.state == EngineState.STOPPED
