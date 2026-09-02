"""
KORTEX Recipe Engine DSL Structural Representation.

Defines structural representation and validation utilities for declarative Recipe DSL.
Contains ZERO execution or runtime capability dispatch logic.
"""

from __future__ import annotations

from kortex.engines.recipe.exceptions import RecipeValidationError
from kortex.engines.recipe.models import (
    RecipeCompatibility,
    RecipeDefinition,
    RecipeInput,
    RecipeManifest,
    RecipeOutput,
    RecipePermission,
    RecipeSettings,
    RecipeStep,
)


class RecipeDSL:
    """Pure structural specification of a Recipe DSL instance."""

    def __init__(
        self,
        manifest: RecipeManifest,
        inputs: list[RecipeInput] | None = None,
        steps: list[RecipeStep] | None = None,
        outputs: list[RecipeOutput] | None = None,
        settings: RecipeSettings | None = None,
        permissions: list[RecipePermission] | None = None,
        compatibility: RecipeCompatibility | None = None,
    ) -> None:
        self.manifest = manifest
        self.inputs = inputs or []
        self.steps = steps or []
        self.outputs = outputs or []
        self.settings = settings or RecipeSettings()
        self.permissions = permissions or []
        self.compatibility = compatibility or RecipeCompatibility()

    def to_definition(self) -> RecipeDefinition:
        """Convert DSL structure into a RecipeDefinition domain model."""
        return RecipeDefinition(
            manifest=self.manifest,
            inputs=self.inputs,
            steps=self.steps,
            outputs=self.outputs,
            settings=self.settings,
            permissions=self.permissions,
            compatibility=self.compatibility,
        )

    @classmethod
    def from_definition(cls, definition: RecipeDefinition) -> RecipeDSL:
        """Construct a RecipeDSL instance from a RecipeDefinition model."""
        return cls(
            manifest=definition.manifest,
            inputs=definition.inputs,
            steps=definition.steps,
            outputs=definition.outputs,
            settings=definition.settings,
            permissions=definition.permissions,
            compatibility=definition.compatibility,
        )

    def validate_structure(self) -> None:
        """Perform basic structural integrity check on DSL components.

        Raises:
            RecipeValidationError: If step IDs are missing or duplicated.
        """
        if not self.steps:
            raise RecipeValidationError("Recipe DSL must contain at least one step.")

        step_ids = set()
        for step in self.steps:
            if not step.id:
                raise RecipeValidationError("Every Recipe step must specify a non-empty 'id'.")
            if step.id in step_ids:
                raise RecipeValidationError(f"Duplicate step ID '{step.id}' found in Recipe DSL.")
            step_ids.add(step.id)
