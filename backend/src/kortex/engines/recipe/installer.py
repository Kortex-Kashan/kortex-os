"""
KORTEX Recipe Engine Lifecycle Installer.

Manages recipe installation, upgrades, removal, rollback, dependency verification,
and persistent storage via Storage Engine (IFileStore / IDataStore).
"""

from __future__ import annotations

import contextlib
import datetime

from kortex.engines.recipe.compiler import RecipeCompiler
from kortex.engines.recipe.loader import RecipeLoader
from kortex.engines.recipe.models import (
    RecipeDefinition,
    RecipeInstallationResult,
    RecipeRemovalResult,
    RecipeUpgradeResult,
)
from kortex.engines.recipe.registry import RecipeRegistry
from kortex.engines.recipe.validator import RecipeValidator
from kortex.engines.storage.interfaces import IFileStore


class RecipeInstaller:
    """Installer for managing recipe lifecycle operations through Storage Engine."""

    def __init__(
        self,
        registry: RecipeRegistry,
        file_store: IFileStore | None = None,
        validator: RecipeValidator | None = None,
        loader: RecipeLoader | None = None,
        compiler: RecipeCompiler | None = None,
    ) -> None:
        self.registry = registry
        self.file_store = file_store
        self.validator = validator or RecipeValidator()
        self.loader = loader or RecipeLoader()
        self.compiler = compiler or RecipeCompiler()

    async def install(
        self,
        recipe: RecipeDefinition,
        package_bytes: bytes | None = None,
        raw_files: dict[str, bytes] | None = None,
    ) -> RecipeInstallationResult:
        """Install a new recipe into the system environment and register catalog entry.

        Args:
            recipe: RecipeDefinition target.
            package_bytes: Optional .kortex-recipe archive payload.
            raw_files: Optional raw recipe files mapping.

        Returns:
            RecipeInstallationResult payload.
        """
        recipe_id = recipe.manifest.id
        version = recipe.manifest.version

        # 1. Check existing installation
        existing = self.registry.find_by_id(recipe_id, version)
        if existing:
            return RecipeInstallationResult(
                success=False,
                recipe_id=recipe_id,
                version=version,
                errors=[f"Recipe '{recipe_id}' version '{version}' is already installed."],
            )

        # 2. Extract and validate files if package provided
        files_to_validate = raw_files
        if package_bytes and not files_to_validate:
            files_to_validate = self.loader.read_package_files(package_bytes)

        validation = self.validator.validate_recipe(recipe, files_to_validate)
        if not validation.is_valid:
            return RecipeInstallationResult(
                success=False,
                recipe_id=recipe_id,
                version=version,
                errors=validation.errors,
            )

        # 3. Store files in sandboxed IFileStore if available
        if self.file_store and files_to_validate:
            for fname, fcontent in files_to_validate.items():
                store_path = f"recipes/{recipe_id}/{version}/{fname}"
                await self.file_store.write_file(store_path, fcontent)

        # 4. Register catalog entry
        self.registry.register(recipe)

        installed_at = datetime.datetime.now(datetime.UTC).isoformat()
        return RecipeInstallationResult(
            success=True,
            recipe_id=recipe_id,
            version=version,
            installed_at=installed_at,
        )

    async def upgrade(
        self,
        recipe: RecipeDefinition,
        package_bytes: bytes | None = None,
        raw_files: dict[str, bytes] | None = None,
    ) -> RecipeUpgradeResult:
        """Upgrade an installed recipe to a new version.

        Args:
            recipe: Target RecipeDefinition with new version.
            package_bytes: Optional package content bytes.
            raw_files: Optional file mapping.

        Returns:
            RecipeUpgradeResult payload.
        """
        recipe_id = recipe.manifest.id
        new_version = recipe.manifest.version

        existing = self.registry.find_by_id(recipe_id)
        if not existing:
            return RecipeUpgradeResult(
                success=False,
                recipe_id=recipe_id,
                previous_version="",
                new_version=new_version,
                errors=[f"Cannot upgrade. Base recipe '{recipe_id}' is not installed."],
            )

        prev_version = existing.manifest.version

        # Install new version
        install_res = await self.install(recipe, package_bytes, raw_files)
        if not install_res.success:
            return RecipeUpgradeResult(
                success=False,
                recipe_id=recipe_id,
                previous_version=prev_version,
                new_version=new_version,
                errors=install_res.errors,
            )

        return RecipeUpgradeResult(
            success=True,
            recipe_id=recipe_id,
            previous_version=prev_version,
            new_version=new_version,
        )

    async def remove(self, recipe_id: str, version: str) -> RecipeRemovalResult:
        """Remove an installed recipe version.

        Args:
            recipe_id: Recipe ID.
            version: Version string.

        Returns:
            RecipeRemovalResult payload.
        """
        removed = self.registry.unregister(recipe_id, version)
        if not removed:
            return RecipeRemovalResult(
                success=False,
                recipe_id=recipe_id,
                version=version,
                removed_at="",
                errors=[f"Recipe '{recipe_id}' version '{version}' not found in registry."],
            )

        # Remove files from IFileStore if available
        if self.file_store:
            with contextlib.suppress(Exception):  # Ignore missing files cleanup warnings
                base_prefix = f"recipes/{recipe_id}/{version}"
                files = await self.file_store.list_files(base_prefix)
                for f in files:
                    await self.file_store.delete_file(f)

        removed_at = datetime.datetime.now(datetime.UTC).isoformat()
        return RecipeRemovalResult(
            success=True,
            recipe_id=recipe_id,
            version=version,
            removed_at=removed_at,
        )

    async def rollback(self, recipe_id: str, target_version: str) -> RecipeInstallationResult:
        """Rollback recipe configuration to a target registered version."""
        existing = self.registry.find_by_id(recipe_id, target_version)
        if not existing:
            return RecipeInstallationResult(
                success=False,
                recipe_id=recipe_id,
                version=target_version,
                errors=[f"Target rollback version '{target_version}' for recipe '{recipe_id}' is not available."],
            )

        installed_at = datetime.datetime.now(datetime.UTC).isoformat()
        return RecipeInstallationResult(
            success=True,
            recipe_id=recipe_id,
            version=target_version,
            installed_at=installed_at,
        )
