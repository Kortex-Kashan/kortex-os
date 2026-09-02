"""
KORTEX Recipe Engine Registry.

Catalog and index provider for managing registered Recipe definitions.
Supports lookup by ID, version, namespace, tag search, and listing.
"""

from __future__ import annotations

from kortex.engines.recipe.models import RecipeDefinition


class RecipeRegistry:
    """In-memory and persistent catalog registry for registered Recipes."""

    def __init__(self) -> None:
        # Key: (recipe_id, version) -> RecipeDefinition
        self._recipes: dict[tuple[str, str], RecipeDefinition] = {}

    def register(self, recipe: RecipeDefinition) -> bool:
        """Register a recipe in the registry catalog.

        Args:
            recipe: Target RecipeDefinition.

        Returns:
            True if registered successfully.
        """
        key = (recipe.manifest.id, recipe.manifest.version)
        self._recipes[key] = recipe
        return True

    def unregister(self, recipe_id: str, version: str) -> bool:
        """Remove a recipe version from the registry catalog.

        Args:
            recipe_id: Target recipe ID.
            version: Target version string.

        Returns:
            True if removed, False if not found.
        """
        key = (recipe_id, version)
        if key in self._recipes:
            del self._recipes[key]
            return True
        return False

    def find_by_id(self, recipe_id: str, version: str | None = None) -> RecipeDefinition | None:
        """Find a registered recipe by ID and optional version string.

        Args:
            recipe_id: Recipe ID.
            version: Specific version string. If None, returns highest registered version.

        Returns:
            Matching RecipeDefinition or None if not found.
        """
        if version:
            return self._recipes.get((recipe_id, version))

        # Find latest version for recipe_id
        matches = [recipe for (rid, _), recipe in self._recipes.items() if rid == recipe_id]
        if not matches:
            return None
        # Return last registered match
        return matches[-1]

    def find_by_namespace(self, namespace: str) -> list[RecipeDefinition]:
        """Find all recipes registered under a given namespace.

        Args:
            namespace: Reverse-domain namespace string (e.g. kortex.hr).

        Returns:
            List of matching RecipeDefinition models.
        """
        return [
            recipe
            for recipe in self._recipes.values()
            if recipe.manifest.namespace == namespace or recipe.manifest.namespace.startswith(f"{namespace}.")
        ]

    def search(self, query: str) -> list[RecipeDefinition]:
        """Search registered recipes by ID, name, namespace, or description text.

        Args:
            query: Search query string.

        Returns:
            List of matching RecipeDefinition models.
        """
        q = query.lower()
        results: list[RecipeDefinition] = []
        for recipe in self._recipes.values():
            m = recipe.manifest
            if q in m.id.lower() or q in m.name.lower() or q in m.namespace.lower() or q in m.description.lower():
                results.append(recipe)
        return results

    def list_all(self) -> list[RecipeDefinition]:
        """Return list of all currently registered recipes."""
        return list(self._recipes.values())
