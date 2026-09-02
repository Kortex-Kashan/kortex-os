"""
Unit tests for RecipeDSL structural model and validation.
"""

import pytest

from kortex.engines.recipe.dsl import RecipeDSL
from kortex.engines.recipe.exceptions import RecipeValidationError
from kortex.engines.recipe.models import RecipeManifest, RecipeStep


def test_recipe_dsl_structure_and_conversion() -> None:
    manifest = RecipeManifest(id="dsl-1", name="DSL Recipe", namespace="kortex.dsl", version="1.0.0", checksum="1")
    step1 = RecipeStep(id="s1", name="Step 1")

    dsl = RecipeDSL(manifest=manifest, steps=[step1])
    dsl.validate_structure()

    definition = dsl.to_definition()
    assert definition.manifest.id == "dsl-1"
    assert len(definition.steps) == 1

    dsl_from_def = RecipeDSL.from_definition(definition)
    assert dsl_from_def.manifest.id == "dsl-1"


def test_recipe_dsl_validation_errors() -> None:
    manifest = RecipeManifest(id="dsl-err", name="Err Recipe", namespace="kortex.dsl", version="1.0.0", checksum="1")

    # Empty steps
    dsl_empty = RecipeDSL(manifest=manifest, steps=[])
    with pytest.raises(RecipeValidationError, match="Recipe DSL must contain at least one step"):
        dsl_empty.validate_structure()

    # Empty step ID
    dsl_empty_id = RecipeDSL(manifest=manifest, steps=[RecipeStep(id="", name="No ID")])
    with pytest.raises(RecipeValidationError, match="Every Recipe step must specify a non-empty 'id'"):
        dsl_empty_id.validate_structure()

    # Duplicate step ID
    dsl_dup = RecipeDSL(manifest=manifest, steps=[RecipeStep(id="s1", name="S1"), RecipeStep(id="s1", name="S1 Dup")])
    with pytest.raises(RecipeValidationError, match="Duplicate step ID 's1' found in Recipe DSL"):
        dsl_dup.validate_structure()
