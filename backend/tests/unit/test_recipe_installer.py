"""
Unit tests for KORTEX Recipe Engine Installer and Storage integration.
"""

import pytest
from kortex.engines.recipe.installer import RecipeInstaller
from kortex.engines.recipe.models import RecipeDefinition, RecipeManifest, RecipeStep
from kortex.engines.recipe.registry import RecipeRegistry
from kortex.engines.storage.stores.file_store import LocalFileStore


@pytest.mark.asyncio
async def test_installer_lifecycle(tmp_path: pytest.TempPathFactory) -> None:
    registry = RecipeRegistry()
    file_store = LocalFileStore(base_directory=str(tmp_path))
    installer = RecipeInstaller(registry=registry, file_store=file_store)

    m1 = RecipeManifest(id="rec-inst", name="Inst Recipe", namespace="kortex.inst", version="1.0.0", checksum="1")
    def1 = RecipeDefinition(manifest=m1, steps=[RecipeStep(id="s1", name="Step")])

    raw_files = {
        "manifest.yaml": b"id: rec-inst\n...",
        "recipe.yaml": b"steps: [...]",
    }

    # Install
    res_inst = await installer.install(def1, raw_files=raw_files)
    assert res_inst.success is True
    assert registry.find_by_id("rec-inst", "1.0.0") is not None
    assert await file_store.file_exists("recipes/rec-inst/1.0.0/recipe.yaml") is True

    # Duplicate Install
    res_dup = await installer.install(def1, raw_files=raw_files)
    assert res_dup.success is False

    # Upgrade
    m2 = RecipeManifest(id="rec-inst", name="Inst Recipe v2", namespace="kortex.inst", version="2.0.0", checksum="2")
    def2 = RecipeDefinition(manifest=m2, steps=[RecipeStep(id="s1", name="Step")])
    res_upg = await installer.upgrade(def2, raw_files=raw_files)
    assert res_upg.success is True
    assert res_upg.previous_version == "1.0.0"
    assert res_upg.new_version == "2.0.0"

    # Upgrade non-existent recipe failure
    m_new = RecipeManifest(id="rec-nonexistent", name="New", namespace="kortex.inst", version="1.0.0", checksum="1")
    def_new = RecipeDefinition(manifest=m_new, steps=[RecipeStep(id="s1", name="Step")])
    res_upg_fail = await installer.upgrade(def_new)
    assert res_upg_fail.success is False

    # Rollback success
    res_rb = await installer.rollback("rec-inst", "1.0.0")
    assert res_rb.success is True

    # Rollback failure
    res_rb_fail = await installer.rollback("rec-inst", "9.9.9")
    assert res_rb_fail.success is False

    # Remove
    res_rem = await installer.remove("rec-inst", "1.0.0")
    assert res_rem.success is True
    assert registry.find_by_id("rec-inst", "1.0.0") is None

    # Remove missing
    res_rem_fail = await installer.remove("rec-inst", "9.9.9")
    assert res_rem_fail.success is False
