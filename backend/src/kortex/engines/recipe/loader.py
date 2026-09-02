"""
KORTEX Recipe Engine Asset Loader.

Loads recipe specifications and raw package files from local directories, ZIP archives,
or `.kortex-recipe` package payloads. Performs NO installation.
"""

from __future__ import annotations

import io
import os
import zipfile

from kortex.engines.recipe.exceptions import RecipePackageError
from kortex.engines.recipe.models import RecipeDefinition
from kortex.engines.recipe.parser import RecipeParser


class RecipeLoader:
    """Loader for recipe folders, ZIP archives, and .kortex-recipe packages."""

    def __init__(self, parser: RecipeParser | None = None) -> None:
        self.parser = parser or RecipeParser()

    def read_package_files(self, package_bytes: bytes) -> dict[str, bytes]:
        """Extract files from a binary ZIP / .kortex-recipe payload.

        Args:
            package_bytes: Raw binary content of the package.

        Returns:
            Dict mapping file relative paths to binary content bytes.

        Raises:
            RecipePackageError: If package is not a valid ZIP archive.
        """
        try:
            files: dict[str, bytes] = {}
            with zipfile.ZipFile(io.BytesIO(package_bytes), "r") as zf:
                for name in zf.namelist():
                    if not name.endswith("/"):  # Ignore directory entries
                        files[name] = zf.read(name)
            return files
        except zipfile.BadZipFile as e:
            raise RecipePackageError(f"Invalid .kortex-recipe package archive: {e}") from e

    def load_from_package(self, package_bytes: bytes) -> RecipeDefinition:
        """Load and parse a RecipeDefinition from a binary .kortex-recipe payload.

        Args:
            package_bytes: Binary archive content.

        Returns:
            Parsed RecipeDefinition model.
        """
        files = self.read_package_files(package_bytes)

        recipe_content: str | None = None
        manifest_content: str | None = None

        for path, data in files.items():
            base_name = os.path.basename(path)
            if base_name in ("recipe.yaml", "recipe.yml"):
                recipe_content = data.decode("utf-8")
            elif base_name in ("manifest.yaml", "manifest.yml"):
                manifest_content = data.decode("utf-8")

        if not recipe_content:
            raise RecipePackageError("Package missing mandatory 'recipe.yaml' specification file.")

        return self.parser.parse_definition(raw_recipe=recipe_content, raw_manifest=manifest_content)

    def load_from_folder_files(self, files: dict[str, bytes]) -> RecipeDefinition:
        """Load and parse RecipeDefinition from a dictionary of file bytes."""
        recipe_content: str | None = None
        manifest_content: str | None = None

        for path, data in files.items():
            base_name = os.path.basename(path)
            if base_name in ("recipe.yaml", "recipe.yml"):
                recipe_content = data.decode("utf-8")
            elif base_name in ("manifest.yaml", "manifest.yml"):
                manifest_content = data.decode("utf-8")

        if not recipe_content:
            raise RecipePackageError("Folder assets missing mandatory 'recipe.yaml' specification file.")

        return self.parser.parse_definition(raw_recipe=recipe_content, raw_manifest=manifest_content)
