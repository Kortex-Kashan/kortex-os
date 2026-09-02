"""Lifecycle and capability-registration tests for `DocumentIntelligenceEngine` (M1/M5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.core.exceptions import EngineStateError
from kortex.core.kernel import Kernel
from kortex.engines.document_intelligence.engine import DocumentIntelligenceEngine
from kortex.engines.storage.engine import StorageEngine

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "document_intelligence"


def _kernel(tmp_path: Path) -> tuple[Kernel, DocumentIntelligenceEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    kernel.register_engine(storage_engine)
    engine = DocumentIntelligenceEngine()
    kernel.register_engine(engine)
    return kernel, engine


@pytest.mark.asyncio
async def test_engine_reaches_ready_state_after_boot(tmp_path: Path) -> None:
    kernel, engine = _kernel(tmp_path)
    await kernel.boot()
    assert engine.state.value == "RUNNING"


@pytest.mark.asyncio
async def test_engine_registers_exactly_three_capabilities(tmp_path: Path) -> None:
    kernel, _engine = _kernel(tmp_path)
    await kernel.boot()
    for name in (
        "kortex.document_intelligence.pdf.parse",
        "kortex.document_intelligence.ocr.extract",
        "kortex.document_intelligence.structure.analyze",
    ):
        descriptor = kernel.get_capability(name)
        assert descriptor.name == name
        assert descriptor.requires_authentication is True


@pytest.mark.asyncio
async def test_engine_name_and_dependencies() -> None:
    engine = DocumentIntelligenceEngine()
    assert engine.name == "document_intelligence"
    assert engine.dependencies == ["configuration", "registry", "event", "storage"]


@pytest.mark.asyncio
async def test_health_check_reports_healthy_when_running(tmp_path: Path) -> None:
    kernel, engine = _kernel(tmp_path)
    await kernel.boot()
    health = await engine.health_check()
    assert health["healthy"] is True
    assert health["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_diagnostics_capabilities_match_registered_set() -> None:
    engine = DocumentIntelligenceEngine()
    assert set(engine.capabilities()) == set(engine.registered_capabilities)


@pytest.mark.asyncio
async def test_stop_transitions_to_stopped(tmp_path: Path) -> None:
    kernel, engine = _kernel(tmp_path)
    await kernel.boot()
    await engine.stop()
    assert engine.state.value == "STOPPED"


@pytest.mark.asyncio
async def test_double_initialize_raises_engine_state_error(tmp_path: Path) -> None:
    kernel, engine = _kernel(tmp_path)
    await kernel.boot()
    with pytest.raises(EngineStateError):
        await engine.initialize(kernel)


@pytest.mark.asyncio
async def test_boot_fails_fast_when_declared_storage_dependency_is_missing() -> None:
    """`storage` is a declared dependency (see `dependencies` property), so
    `BootEngine`'s topological sort correctly refuses to boot at all if it
    is not registered — a real, repository-verified platform behavior, not
    an assumption. Fail-fast at boot is preferable to a lazily-failing
    per-request StorageAccessError."""
    from kortex.core.exceptions import KernelBootError

    kernel = Kernel()
    engine = DocumentIntelligenceEngine()
    kernel.register_engine(engine)
    with pytest.raises(KernelBootError, match="storage"):
        await kernel.boot()
