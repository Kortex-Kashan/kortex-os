"""
Unit tests for RecipeEngine facade and IEngineDiagnostics.
"""

import io
import zipfile

import pytest

from kortex.core.base_engine import EngineState
from kortex.engines.recipe.engine import RecipeEngine
from kortex.engines.recipe.exceptions import RecipeError
from kortex.engines.recipe.models import RecipeDefinition, RecipeManifest, RecipeStep


def test_recipe_engine_initialization_and_diagnostics() -> None:
    engine = RecipeEngine()
    assert engine.name == "recipe"
    assert "configuration" in engine.dependencies
    assert "storage" in engine.dependencies
    assert "workflow" in engine.dependencies

    diag = engine.diagnostics()
    assert diag["engine"] == "recipe"
    assert diag["version"] == "1.0.0"
    assert len(diag["capabilities"]) == 10

    health = engine.health()
    assert health["engine"] == "recipe"
    assert health["status"] == "UNINITIALIZED"
    assert health["healthy"] is False


@pytest.mark.asyncio
async def test_recipe_engine_lifecycle_and_capabilities() -> None:
    class DummyFileStore:
        async def write_file(self, relative_path: str, content: bytes) -> str:
            return relative_path

        async def delete_file(self, relative_path: str) -> bool:
            return True

        async def list_files(self, relative_path: str) -> list[str]:
            return []

    class DummyStorageEngine:
        def __init__(self) -> None:
            self.file = DummyFileStore()

    class DummyContainer:
        def has(self, name: str) -> bool:
            return name == "storage"

        def get(self, name: str) -> any:
            if name == "storage":
                return DummyStorageEngine()
            raise KeyError(name)

    class DummyKernel:
        def __init__(self) -> None:
            self.capabilities: dict = {}
            self.container = DummyContainer()

        def register_capability(self, name: str, description: str, provider: str, handler: any, **kwargs: any) -> None:
            self.capabilities[name] = handler

    kernel = DummyKernel()
    engine = RecipeEngine()

    await engine.initialize(kernel)
    assert engine.state == EngineState.READY
    assert isinstance(engine.installer.file_store, DummyFileStore)
    assert len(kernel.capabilities) == 10

    await engine.start()
    assert engine.state == EngineState.RUNNING
    hc = await engine.health_check()
    assert hc["healthy"] is True

    # Test facade methods
    m = RecipeManifest(id="r-eng", name="Eng Recipe", namespace="kortex.eng", version="1.0.0", checksum="1")
    d = RecipeDefinition(manifest=m, steps=[RecipeStep(id="s1", name="Step")])

    val = engine.validate(d)
    assert val.is_valid is True

    comp = engine.compile(d)
    assert comp.success is True

    # Package
    files = {"manifest.yaml": b"...", "recipe.yaml": b"..."}
    pkg = engine.package(files, m)
    assert pkg.package_id == "r-eng"

    # Load package
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "manifest.yaml", "id: r_pkg\nname: Pkg Recipe\nnamespace: kortex.pkg\nversion: 1.0.0\nchecksum: '123'"
        )
        zf.writestr("recipe.yaml", "steps:\n  - id: s_pkg\n    name: Pkg Step")
    pkg_bytes = buffer.getvalue()

    loaded = engine.load_package(pkg_bytes)
    assert loaded.manifest.id == "r_pkg"

    # Install via bytes
    inst_res = await engine.install(pkg_bytes)
    assert inst_res.success is True

    # Install via RecipeDefinition
    m2 = RecipeManifest(id="r-eng2", name="Eng 2", namespace="kortex.eng", version="1.0.0", checksum="2")
    d2 = RecipeDefinition(manifest=m2, steps=[RecipeStep(id="s1", name="Step")])
    inst_def_res = await engine.install(d2)
    assert inst_def_res.success is True

    # Invalid install payload
    with pytest.raises(RecipeError):
        await engine.install("invalid_payload")

    # Upgrade via bytes
    buffer_v2 = io.BytesIO()
    with zipfile.ZipFile(buffer_v2, "w") as zf:
        zf.writestr(
            "manifest.yaml", "id: r_pkg\nname: Pkg Recipe\nnamespace: kortex.pkg\nversion: 2.0.0\nchecksum: '123'"
        )
        zf.writestr("recipe.yaml", "steps:\n  - id: s_pkg\n    name: Pkg Step")
    pkg_v2_bytes = buffer_v2.getvalue()

    upg_res = await engine.upgrade(pkg_v2_bytes)
    assert upg_res.success is True

    # Upgrade via RecipeDefinition
    m2_v2 = RecipeManifest(id="r-eng2", name="Eng 2 v2", namespace="kortex.eng", version="2.0.0", checksum="2")
    d2_v2 = RecipeDefinition(manifest=m2_v2, steps=[RecipeStep(id="s1", name="Step")])
    upg_def_res = await engine.upgrade(d2_v2)
    assert upg_def_res.success is True

    # Invalid upgrade payload
    with pytest.raises(RecipeError):
        await engine.upgrade(12345)

    # Search & List & Info
    assert len(engine.list_recipes()) >= 2
    assert len(engine.search("Pkg")) >= 1
    assert engine.info("r_pkg") is not None

    # Remove
    rem_res = await engine.remove("r_pkg", "1.0.0")
    assert rem_res.success is True

    await engine.stop()
    assert engine.state == EngineState.STOPPED

    # Idempotent stop
    await engine.stop()


@pytest.mark.asyncio
async def test_recipe_engine_initialize_failure() -> None:
    class FailingKernel:
        def register_capability(self, *args, **kwargs) -> None:
            raise RuntimeError("Capability registration error")

    engine = RecipeEngine()
    with pytest.raises(RuntimeError, match="Capability registration error"):
        await engine.initialize(FailingKernel())
    assert engine.state == EngineState.FAILED
