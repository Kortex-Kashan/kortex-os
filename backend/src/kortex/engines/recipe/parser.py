"""
KORTEX Recipe Engine YAML Specification Parser.

Parses raw YAML strings into dictionary mapping structures for recipe.yaml,
manifest.yaml, schema.yaml, and permissions.yaml. Performs NO validation.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import yaml

from kortex.engines.recipe.exceptions import RecipeValidationError
from kortex.engines.recipe.manifest import RecipeManifestManager
from kortex.engines.recipe.models import (
    RecipeCompatibility,
    RecipeDefinition,
    RecipeInput,
    RecipeOutput,
    RecipePermission,
    RecipeSettings,
    RecipeStep,
)


class RecipeParser:
    """YAML parser for loading recipe specification payloads."""

    def parse_yaml_string(self, yaml_content: str) -> Dict[str, Any]:
        """Parse raw YAML content into a dictionary mapping.

        Args:
            yaml_content: Raw YAML string.

        Returns:
            Dictionary mapping.

        Raises:
            RecipeValidationError: If YAML formatting is invalid.
        """
        try:
            parsed = yaml.safe_load(yaml_content)
        except Exception as e:
            raise RecipeValidationError(f"Invalid YAML syntax: {e}") from e

        if not isinstance(parsed, dict):
            raise RecipeValidationError("Parsed YAML must be a root key-value dictionary.")
        return parsed

    def parse_recipe_yaml(self, raw_content: str) -> Dict[str, Any]:
        """Parse recipe.yaml specification payload."""
        return self.parse_yaml_string(raw_content)

    def parse_manifest_yaml(self, raw_content: str) -> Dict[str, Any]:
        """Parse manifest.yaml specification payload."""
        return self.parse_yaml_string(raw_content)

    def parse_schema_yaml(self, raw_content: str) -> Dict[str, Any]:
        """Parse schema.yaml specification payload."""
        return self.parse_yaml_string(raw_content)

    def parse_permissions_yaml(self, raw_content: str) -> Dict[str, Any]:
        """Parse permissions.yaml specification payload."""
        return self.parse_yaml_string(raw_content)

    def parse_definition(self, raw_recipe: str, raw_manifest: Optional[str] = None) -> RecipeDefinition:
        """Parse raw recipe YAML and optional manifest YAML into a RecipeDefinition model.

        Args:
            raw_recipe: Content of recipe.yaml.
            raw_manifest: Content of manifest.yaml (if separated).

        Returns:
            Unvalidated RecipeDefinition model.
        """
        recipe_dict = self.parse_recipe_yaml(raw_recipe)

        if raw_manifest:
            manifest_dict = self.parse_manifest_yaml(raw_manifest)
            manifest = RecipeManifestManager.parse_manifest_dict(manifest_dict)
        elif "manifest" in recipe_dict and isinstance(recipe_dict["manifest"], dict):
            manifest = RecipeManifestManager.parse_manifest_dict(recipe_dict["manifest"])
        else:
            raise RecipeValidationError("Recipe specification must include a valid manifest section or separate manifest.yaml.")

        inputs = [RecipeInput(**i) for i in recipe_dict.get("inputs", [])]
        steps = [RecipeStep(**s) for s in recipe_dict.get("steps", [])]
        outputs = [RecipeOutput(**o) for o in recipe_dict.get("outputs", [])]

        settings_data = recipe_dict.get("settings", {})
        settings = RecipeSettings(**settings_data) if isinstance(settings_data, dict) else RecipeSettings()

        permissions = [RecipePermission(**p) for p in recipe_dict.get("permissions", [])]

        compat_data = recipe_dict.get("compatibility", {})
        compatibility = RecipeCompatibility(**compat_data) if isinstance(compat_data, dict) else RecipeCompatibility()

        return RecipeDefinition(
            manifest=manifest,
            inputs=inputs,
            steps=steps,
            outputs=outputs,
            settings=settings,
            permissions=permissions,
            compatibility=compatibility,
        )
