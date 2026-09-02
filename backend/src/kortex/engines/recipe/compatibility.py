"""
KORTEX Recipe Engine System Compatibility Validator.

Validates recipe compatibility constraints against running Kernel, Workflow, Storage,
Document, Connector, and Module version specs.
"""

from __future__ import annotations

from kortex.engines.recipe.exceptions import RecipeCompatibilityError
from kortex.engines.recipe.models import RecipeDefinition
from kortex.engines.recipe.versioning import VersionResolver


class CompatibilityValidator:
    """Validates Recipe Engine compatibility constraints."""

    @staticmethod
    def validate_compatibility(
        recipe: RecipeDefinition,
        system_versions: dict[str, str],
    ) -> bool:
        """Verify recipe compatibility settings against active system engine versions.

        Args:
            recipe: Target RecipeDefinition.
            system_versions: Dict mapping component names (kernel, workflow_engine, storage_engine,
                             document_engine, connector_engine) to actual version strings.

        Returns:
            True if all compatibility rules pass.

        Raises:
            RecipeCompatibilityError: If system version fails recipe constraint.
        """
        compat = recipe.compatibility

        # Kernel compatibility check
        if "kernel" in system_versions and not VersionResolver.satisfies_constraint(
            system_versions["kernel"], compat.kernel
        ):
            raise RecipeCompatibilityError(
                f"Kernel version '{system_versions['kernel']}' does not satisfy recipe requirement '{compat.kernel}'."
            )

        # Workflow Engine check
        if "workflow_engine" in system_versions and not VersionResolver.satisfies_constraint(
            system_versions["workflow_engine"], compat.workflow_engine
        ):
            raise RecipeCompatibilityError(
                f"Workflow Engine version '{system_versions['workflow_engine']}' does not satisfy recipe "
                f"requirement '{compat.workflow_engine}'."
            )

        # Storage Engine check
        if "storage_engine" in system_versions and not VersionResolver.satisfies_constraint(
            system_versions["storage_engine"], compat.storage_engine
        ):
            raise RecipeCompatibilityError(
                f"Storage Engine version '{system_versions['storage_engine']}' does not satisfy recipe requirement "
                f"'{compat.storage_engine}'."
            )

        # Optional Document Engine check
        if (
            compat.document_engine and "document_engine" in system_versions
        ) and not VersionResolver.satisfies_constraint(system_versions["document_engine"], compat.document_engine):
            raise RecipeCompatibilityError(
                f"Document Engine version '{system_versions['document_engine']}' does not satisfy recipe "
                f"requirement '{compat.document_engine}'."
            )

        # Optional Connector Engine check
        if (
            compat.connector_engine and "connector_engine" in system_versions
        ) and not VersionResolver.satisfies_constraint(system_versions["connector_engine"], compat.connector_engine):
            raise RecipeCompatibilityError(
                f"Connector Engine version '{system_versions['connector_engine']}' does not satisfy recipe "
                f"requirement '{compat.connector_engine}'."
            )

        # Module version checks
        for module_name, required_ver in compat.module_versions.items():
            if module_name not in system_versions:
                raise RecipeCompatibilityError(
                    f"Required module '{module_name}' is not installed in system environment."
                )
            actual_ver = system_versions[module_name]
            if not VersionResolver.satisfies_constraint(actual_ver, required_ver):
                raise RecipeCompatibilityError(
                    f"Module '{module_name}' version '{actual_ver}' does not satisfy recipe constraint "
                    f"'{required_ver}'."
                )

        return True
