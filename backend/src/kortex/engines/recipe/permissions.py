"""
KORTEX Recipe Engine Permission Validator.

Validates capability access requirements, permission models, and least privilege
constraints declared within business recipes.
"""

from __future__ import annotations

from kortex.engines.recipe.exceptions import RecipePermissionError
from kortex.engines.recipe.models import RecipeDefinition, RecipePermission


class PermissionValidator:
    """Validator enforcing security least-privilege policies and permission declarations."""

    @staticmethod
    def validate_permissions(
        recipe: RecipeDefinition,
        granted_capabilities: list[str],
        granted_permissions: list[str] | None = None,
    ) -> bool:
        """Verify that all capabilities and permissions requested by recipe are authorized.

        Args:
            recipe: Target RecipeDefinition.
            granted_capabilities: List of capability strings available in Kernel Registry.
            granted_permissions: System RBAC permissions granted to recipe context.

        Returns:
            True if all required permissions/capabilities are authorized.

        Raises:
            RecipePermissionError: If unauthorized capability or permission is requested.
        """
        granted_cap_set: set[str] = set(granted_capabilities)

        # Check required capabilities in manifest
        for required_cap in recipe.manifest.capabilities_required:
            if required_cap not in granted_cap_set:
                raise RecipePermissionError(
                    f"Recipe '{recipe.manifest.id}' requires capability '{required_cap}' which is not registered or "
                    f"granted."
                )

        # Check capabilities invoked in steps
        for step in recipe.steps:
            if step.capability and step.capability not in granted_cap_set:
                raise RecipePermissionError(
                    f"Step '{step.id}' in recipe '{recipe.manifest.id}' invokes unauthorized capability "
                    f"'{step.capability}'."
                )

        # Check permission rules
        if granted_permissions is not None:
            granted_perm_set = set(granted_permissions)
            for perm in recipe.manifest.permissions_required:
                if perm not in granted_perm_set:
                    raise RecipePermissionError(
                        f"Recipe '{recipe.manifest.id}' requires permission '{perm}' which is not granted in security "
                        f"context."
                    )

        return True

    @staticmethod
    def parse_permissions_yaml_dict(data: dict) -> list[RecipePermission]:
        """Convert parsed permissions.yaml dictionary into RecipePermission models."""
        perms = data.get("permissions", [])
        return [RecipePermission(**p) for p in perms]
