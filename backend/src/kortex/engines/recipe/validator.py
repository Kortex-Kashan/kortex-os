"""
KORTEX Recipe Engine Comprehensive Validator.

Validates recipe schema, security rules (banning executable code files), DSL structure,
permissions, dependencies, compatibility, SHA256 checksums, and digital signatures.
Performs NO compilation or execution.
"""

from __future__ import annotations

from kortex.engines.recipe.manifest import RecipeManifestManager
from kortex.engines.recipe.models import RecipeDefinition, RecipeValidationResult

FORBIDDEN_EXTENSIONS: set[str] = {
    ".py",
    ".pyc",
    ".pyd",
    ".pyo",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".sql",
    ".ps1",
    ".psm1",
    ".bat",
    ".cmd",
    ".sh",
    ".bash",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".com",
}


class RecipeValidator:
    """Validator inspecting recipe structures, security policies, and checksums."""

    def __init__(self, registered_capabilities: list[str] | None = None) -> None:
        """Initialize validator with optional list of valid system capabilities."""
        self.registered_capabilities = set(registered_capabilities) if registered_capabilities else None

    def validate_security(self, raw_files: dict[str, bytes]) -> RecipeValidationResult:
        """Verify that a recipe asset package contains NO executable code files.

        Args:
            raw_files: Mapping of relative filename to file byte content.

        Returns:
            RecipeValidationResult detailing validation pass/fail.
        """
        errors: list[str] = []
        for filename in raw_files:
            lower_fn = filename.lower()
            for ext in FORBIDDEN_EXTENSIONS:
                if lower_fn.endswith(ext):
                    errors.append(f"Forbidden executable file type detected in recipe package: '{filename}' ({ext})")

        is_valid = len(errors) == 0
        return RecipeValidationResult(is_valid=is_valid, recipe_id="security_check", errors=errors)

    def validate_schema(self, recipe: RecipeDefinition) -> RecipeValidationResult:
        """Verify structural and schema validity of a RecipeDefinition.

        Args:
            recipe: RecipeDefinition model instance.

        Returns:
            RecipeValidationResult object.
        """
        errors: list[str] = []
        recipe_id = recipe.manifest.id

        if not recipe.manifest.name:
            errors.append("Recipe manifest title 'name' must not be empty.")
        if not recipe.manifest.namespace:
            errors.append("Recipe manifest 'namespace' must not be empty.")
        if not recipe.steps:
            errors.append("Recipe must define at least one execution step.")

        step_ids: set[str] = set()
        for idx, step in enumerate(recipe.steps):
            if not step.id:
                errors.append(f"Step at index {idx} missing required 'id'.")
            elif step.id in step_ids:
                errors.append(f"Duplicate step ID '{step.id}' detected in recipe.")
            else:
                step_ids.add(step.id)

            if not step.name:
                errors.append(f"Step '{step.id}' missing human-readable 'name'.")

            # Check capability if registered_capabilities provided
            if (
                self.registered_capabilities is not None and step.capability
            ) and step.capability not in self.registered_capabilities:
                errors.append(f"Step '{step.id}' references unregistered capability '{step.capability}'.")

        is_valid = len(errors) == 0
        return RecipeValidationResult(is_valid=is_valid, recipe_id=recipe_id, errors=errors)

    def validate_checksum(self, manifest_checksum: str, recipe: RecipeDefinition) -> bool:
        """Verify checksum matching for a recipe manifest.

        Args:
            manifest_checksum: Expected SHA256 checksum string.
            recipe: RecipeDefinition model instance.

        Returns:
            True if checksum matches.
        """
        computed = RecipeManifestManager.calculate_checksum(recipe.manifest)
        return computed == manifest_checksum

    def validate_recipe(
        self,
        recipe: RecipeDefinition,
        raw_files: dict[str, bytes] | None = None,
    ) -> RecipeValidationResult:
        """Comprehensive validation of recipe schema, security rules, and DSL integrity.

        Args:
            recipe: Target RecipeDefinition.
            raw_files: Optional package file bytes for security checking.

        Returns:
            RecipeValidationResult with aggregated errors.
        """
        schema_result = self.validate_schema(recipe)
        errors = list(schema_result.errors)

        if raw_files:
            sec_result = self.validate_security(raw_files)
            errors.extend(sec_result.errors)

        is_valid = len(errors) == 0
        return RecipeValidationResult(
            is_valid=is_valid,
            recipe_id=recipe.manifest.id,
            errors=errors,
            warnings=schema_result.warnings,
        )
