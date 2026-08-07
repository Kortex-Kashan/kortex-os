"""
KORTEX Recipe Engine Core Implementation.

Acts as central facade and orchestrator for Recipe management in KORTEX OS.
Inherits BaseEngine and implements IEngineDiagnostics.
Registers 10 canonical capabilities with the Kernel.
Contains strictly ZERO execution, ZERO direct parsing, and ZERO direct compilation logic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.recipe.compiler import RecipeCompiler
from kortex.engines.recipe.diagnostics import RecipeDiagnostics
from kortex.engines.recipe.exceptions import RecipeError
from kortex.engines.recipe.installer import RecipeInstaller
from kortex.engines.recipe.interfaces import IEngineDiagnostics
from kortex.engines.recipe.loader import RecipeLoader
from kortex.engines.recipe.manifest import RecipeManifestManager
from kortex.engines.recipe.models import (
    RecipeCompilationResult,
    RecipeDefinition,
    RecipeInstallationResult,
    RecipeManifest,
    RecipePackage,
    RecipeRemovalResult,
    RecipeUpgradeResult,
    RecipeValidationResult,
)
from kortex.engines.recipe.packager import RecipePackager
from kortex.engines.recipe.parser import RecipeParser
from kortex.engines.recipe.registry import RecipeRegistry
from kortex.engines.recipe.validator import RecipeValidator

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.recipe")


class RecipeEngine(BaseEngine, IEngineDiagnostics):
    """Recipe Engine orchestrator for loading, validating, compiling, and packaging recipes."""

    def __init__(self) -> None:
        super().__init__()
        self.parser = RecipeParser()
        self.validator = RecipeValidator()
        self.compiler = RecipeCompiler()
        self.registry = RecipeRegistry()
        self.loader = RecipeLoader(self.parser)
        self.packager = RecipePackager()
        self.installer = RecipeInstaller(
            registry=self.registry,
            validator=self.validator,
            loader=self.loader,
            compiler=self.compiler,
        )
        self._diagnostics_provider = RecipeDiagnostics(self)

    @property
    def name(self) -> str:
        """Unique engine identifier name."""
        return "recipe"

    @property
    def dependencies(self) -> List[str]:
        """Names of prerequisite system engines."""
        return ["configuration", "registry", "storage", "workflow"]

    # -- Lifecycle Implementation -------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize Recipe Engine and register capabilities with Kernel."""
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Recipe Engine...")

        try:
            # Wire file store if storage engine is present in kernel container
            if hasattr(kernel, "container") and kernel.container.has("storage"):
                storage_engine = kernel.container.get("storage")
                if hasattr(storage_engine, "file"):
                    self.installer.file_store = storage_engine.file

            # Register capabilities with Kernel
            kernel.register_capability(
                name="kortex.recipe.load",
                description="Load and parse raw recipe specification payload",
                provider=self.name,
                handler=self.load_package,
            )
            kernel.register_capability(
                name="kortex.recipe.validate",
                description="Validate recipe structure, security rules, and permissions",
                provider=self.name,
                handler=self.validate,
            )
            kernel.register_capability(
                name="kortex.recipe.compile",
                description="Compile declarative Recipe into executable WorkflowDefinition",
                provider=self.name,
                handler=self.compile,
            )
            kernel.register_capability(
                name="kortex.recipe.install",
                description="Install recipe package into workspace",
                provider=self.name,
                handler=self.install,
            )
            kernel.register_capability(
                name="kortex.recipe.remove",
                description="Uninstall recipe package version from workspace",
                provider=self.name,
                handler=self.remove,
            )
            kernel.register_capability(
                name="kortex.recipe.upgrade",
                description="Upgrade existing installed recipe package",
                provider=self.name,
                handler=self.upgrade,
            )
            kernel.register_capability(
                name="kortex.recipe.package",
                description="Create standalone .kortex-recipe archive package",
                provider=self.name,
                handler=self.package,
            )
            kernel.register_capability(
                name="kortex.recipe.search",
                description="Search registered recipe catalog",
                provider=self.name,
                handler=self.search,
            )
            kernel.register_capability(
                name="kortex.recipe.list",
                description="List all registered recipe assets",
                provider=self.name,
                handler=self.list_recipes,
            )
            kernel.register_capability(
                name="kortex.recipe.info",
                description="Get detailed metadata for a registered recipe",
                provider=self.name,
                handler=self.info,
            )

            self._set_state(EngineState.READY)
            self.logger.info("Recipe Engine initialized successfully.")
        except Exception as e:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize Recipe Engine: %s", e, exc_info=True)
            raise

    async def start(self) -> None:
        """Start active background tasks."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Recipe Engine is RUNNING.")

    async def stop(self) -> None:
        """Shut down engine operations."""
        if self._state in (EngineState.STOPPED, EngineState.UNINITIALIZED):
            return
        self._set_state(EngineState.STOPPING)
        self.logger.info("Stopping Recipe Engine...")
        self._set_state(EngineState.STOPPED)
        self.logger.info("Recipe Engine stopped cleanly.")

    async def health_check(self) -> Dict[str, Any]:
        """Return diagnostic health check dict."""
        return self.health()

    # -- Facade Capability Methods (Delegated to internal services) ----------

    def parse(self, raw_recipe: str, raw_manifest: Optional[str] = None) -> RecipeDefinition:
        """Parse raw recipe YAML string."""
        self._diagnostics_provider.increment_metric("recipes_parsed")
        return self.parser.parse_definition(raw_recipe, raw_manifest)

    def validate(self, recipe: RecipeDefinition) -> RecipeValidationResult:
        """Validate recipe schema, security rules, and permissions."""
        self._diagnostics_provider.increment_metric("recipes_validated")
        return self.validator.validate_recipe(recipe)

    def compile(
        self,
        recipe: RecipeDefinition,
        input_parameters: Optional[Dict[str, Any]] = None,
    ) -> RecipeCompilationResult:
        """Compile RecipeDefinition into WorkflowDefinition."""
        self._diagnostics_provider.increment_metric("recipes_compiled")
        return self.compiler.compile(recipe, input_parameters)

    def load_package(self, package_bytes: bytes) -> RecipeDefinition:
        """Load recipe from binary .kortex-recipe payload."""
        return self.loader.load_from_package(package_bytes)

    def package(self, files: Dict[str, bytes], manifest: RecipeManifest) -> RecipePackage:
        """Assemble .kortex-recipe archive package."""
        self._diagnostics_provider.increment_metric("packages_created")
        return self.packager.create_package(files, manifest)

    async def install(self, recipe_or_package: Any) -> RecipeInstallationResult:
        """Install recipe from RecipeDefinition or binary package bytes."""
        self._diagnostics_provider.increment_metric("recipes_installed")
        if isinstance(recipe_or_package, bytes):
            recipe = self.loader.load_from_package(recipe_or_package)
            return await self.installer.install(recipe, package_bytes=recipe_or_package)
        elif isinstance(recipe_or_package, RecipeDefinition):
            return await self.installer.install(recipe_or_package)
        else:
            raise RecipeError("Invalid payload for install. Expected RecipeDefinition or package bytes.")

    async def upgrade(self, recipe_or_package: Any) -> RecipeUpgradeResult:
        """Upgrade an installed recipe."""
        if isinstance(recipe_or_package, bytes):
            recipe = self.loader.load_from_package(recipe_or_package)
            return await self.installer.upgrade(recipe, package_bytes=recipe_or_package)
        elif isinstance(recipe_or_package, RecipeDefinition):
            return await self.installer.upgrade(recipe_or_package)
        else:
            raise RecipeError("Invalid payload for upgrade. Expected RecipeDefinition or package bytes.")

    async def remove(self, recipe_id: str, version: str) -> RecipeRemovalResult:
        """Uninstall recipe version."""
        return await self.installer.remove(recipe_id, version)

    def search(self, query: str) -> List[RecipeDefinition]:
        """Search registered recipes."""
        return self.registry.search(query)

    def list_recipes(self) -> List[RecipeDefinition]:
        """List registered recipes."""
        return self.registry.list_all()

    def info(self, recipe_id: str, version: Optional[str] = None) -> Optional[RecipeDefinition]:
        """Lookup registered recipe by ID."""
        return self.registry.find_by_id(recipe_id, version)

    # -- Common Diagnostics Interface --------------------------------------

    def health(self) -> Dict[str, Any]:
        return self._diagnostics_provider.health()

    def metrics(self) -> Dict[str, Any]:
        return self._diagnostics_provider.metrics()

    def diagnostics(self) -> Dict[str, Any]:
        return self._diagnostics_provider.diagnostics()

    def status(self) -> str:
        return self._diagnostics_provider.status()

    def version(self) -> str:
        return self._diagnostics_provider.version()

    def capabilities(self) -> List[str]:
        return self._diagnostics_provider.capabilities()
