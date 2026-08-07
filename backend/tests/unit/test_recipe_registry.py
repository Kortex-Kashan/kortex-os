"""
Unit tests for KORTEX Recipe Engine Catalog Registry.
"""

import pytest
from kortex.engines.recipe.models import RecipeDefinition, RecipeManifest, RecipeStep
from kortex.engines.recipe.registry import RecipeRegistry


def test_recipe_registry_operations() -> None:
    registry = RecipeRegistry()

    m1 = RecipeManifest(id="r1", name="Recipe 1", namespace="kortex.hr.payroll", version="1.0.0", checksum="1")
    m2 = RecipeManifest(id="r1", name="Recipe 1 v2", namespace="kortex.hr.payroll", version="2.0.0", checksum="2")
    m3 = RecipeManifest(id="r2", name="Recipe 2", namespace="kortex.finance.invoice", version="1.0.0", checksum="3")

    def1 = RecipeDefinition(manifest=m1, steps=[RecipeStep(id="s1", name="Step")])
    def2 = RecipeDefinition(manifest=m2, steps=[RecipeStep(id="s1", name="Step")])
    def3 = RecipeDefinition(manifest=m3, steps=[RecipeStep(id="s1", name="Step")])

    registry.register(def1)
    registry.register(def2)
    registry.register(def3)

    assert len(registry.list_all()) == 3

    # Lookup by specific version
    found_v1 = registry.find_by_id("r1", "1.0.0")
    assert found_v1 is not None
    assert found_v1.manifest.version == "1.0.0"

    # Lookup latest
    found_latest = registry.find_by_id("r1")
    assert found_latest is not None
    assert found_latest.manifest.version == "2.0.0"

    # Find by namespace
    hr_recipes = registry.find_by_namespace("kortex.hr")
    assert len(hr_recipes) == 2

    # Search
    search_results = registry.search("invoice")
    assert len(search_results) == 1
    assert search_results[0].manifest.id == "r2"

    # Unregister
    removed = registry.unregister("r1", "1.0.0")
    assert removed is True
    assert registry.find_by_id("r1", "1.0.0") is None
    assert len(registry.list_all()) == 2
