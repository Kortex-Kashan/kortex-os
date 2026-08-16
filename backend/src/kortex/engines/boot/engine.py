"""
KORTEX Boot Engine.

Manages the system startup sequence, engine dependency graph resolution (topological sort),
orderly engine initialization, system-wide health checks, and graceful shutdown sequencing.
"""

from __future__ import annotations

import collections
from typing import TYPE_CHECKING, Any, Dict, List, Set

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.exceptions import EngineNotFoundError, KernelBootError

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel


class BootEngine(BaseEngine):
    """Boot Engine implementation."""

    def __init__(self) -> None:
        super().__init__()
        self._boot_order: List[str] = []
        self._kernel_ref: Kernel | None = None

    @property
    def name(self) -> str:
        return "boot"

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize the Boot Engine."""
        self._set_state(EngineState.INITIALIZING)
        self._kernel_ref = kernel
        self.logger.info("Initializing Boot Engine...")
        self._set_state(EngineState.READY)

    async def start(self) -> None:
        """Start the Boot Engine."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Boot Engine running.")

    async def health_check(self) -> Dict[str, Any]:
        """Diagnostic health check."""
        return {
            "engine": self.name,
            "status": "healthy" if self.state == EngineState.RUNNING else "unhealthy",
            "boot_order": self._boot_order,
        }

    async def stop(self) -> None:
        """Stop the Boot Engine."""
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)
        self.logger.info("Boot Engine stopped.")

    # -- Dependency Resolution & Boot Operations ---------------------------

    def resolve_dependency_order(self, engines: Dict[str, BaseEngine]) -> List[str]:
        """Topologically sort registered system engines by declared dependencies.

        Args:
            engines: Dictionary mapping engine name to BaseEngine instance.

        Returns:
            Ordered list of engine names safe for initialization.

        Raises:
            KernelBootError: If a dependency is missing or a cyclic dependency is detected.
        """
        in_degree: Dict[str, int] = {name: 0 for name in engines}
        adj_list: Dict[str, List[str]] = collections.defaultdict(list)

        for name, engine in engines.items():
            for dep in engine.dependencies:
                if dep not in engines:
                    raise KernelBootError(f"Engine '{name}' depends on unregistered engine '{dep}'")
                adj_list[dep].append(name)
                in_degree[name] += 1

        # Queue of engines with zero incoming dependencies
        queue = collections.deque([name for name, deg in in_degree.items() if deg == 0])
        sorted_order: List[str] = []

        while queue:
            node = queue.popleft()
            sorted_order.append(node)

            for neighbor in adj_list[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_order) != len(engines):
            unresolved = [name for name, deg in in_degree.items() if deg > 0]
            raise KernelBootError(f"Cyclic dependency detected among engines: {unresolved}")

        self._boot_order = sorted_order
        self.logger.info("Resolved engine boot sequence: %s", " -> ".join(sorted_order))
        return sorted_order

    async def boot_system(self, kernel: Kernel) -> None:
        """Execute the full startup boot sequence for all registered system engines.

        1. Resolve dependency graph
        2. Initialize engines in dependency order
        3. Start engines in dependency order
        """
        self.logger.info("Starting KORTEX OS System Boot Sequence...")
        registered_engines = kernel.get_all_engines()
        boot_sequence = self.resolve_dependency_order(registered_engines)

        # 1. Initialize Phase
        for engine_name in boot_sequence:
            engine = registered_engines[engine_name]
            self.logger.info("Initializing engine [%s]...", engine_name)
            try:
                await engine.initialize(kernel)
            except Exception as e:
                self.logger.critical("Engine initialization failed for [%s]: %s", engine_name, e)
                raise KernelBootError(f"Failed to initialize engine '{engine_name}': {e}") from e

        # 2. Start Phase
        for engine_name in boot_sequence:
            engine = registered_engines[engine_name]
            self.logger.info("Starting engine [%s]...", engine_name)
            try:
                await engine.start()
            except Exception as e:
                self.logger.critical("Engine start failed for [%s]: %s", engine_name, e)
                raise KernelBootError(f"Failed to start engine '{engine_name}': {e}") from e

        self.logger.info("KORTEX OS System Boot Sequence completed successfully.")

    async def shutdown_system(self, kernel: Kernel) -> None:
        """Execute graceful shutdown sequence for all system engines in REVERSE dependency order."""
        self.logger.info("Initiating KORTEX OS System Shutdown Sequence...")
        registered_engines = kernel.get_all_engines()
        reverse_sequence = list(reversed(self._boot_order)) if self._boot_order else list(registered_engines.keys())

        for engine_name in reverse_sequence:
            if engine_name in registered_engines:
                engine = registered_engines[engine_name]
                if engine.state not in (EngineState.STOPPED, EngineState.STOPPING):
                    self.logger.info("Stopping engine [%s]...", engine_name)
                    try:
                        await engine.stop()
                    except Exception as e:
                        self.logger.error("Error stopping engine [%s]: %s", engine_name, e)

        self.logger.info("KORTEX OS System Shutdown Sequence completed.")

    async def run_system_health_checks(self, kernel: Kernel) -> Dict[str, Any]:
        """Gather aggregated health check reports across all active system engines."""
        registered_engines = kernel.get_all_engines()
        reports: Dict[str, Any] = {}
        all_healthy = True

        for name, engine in registered_engines.items():
            try:
                report = await engine.health_check()
                reports[name] = report
                if report.get("status") != "healthy":
                    all_healthy = False
            except Exception as e:
                reports[name] = {"engine": name, "status": "unhealthy", "error": str(e)}
                all_healthy = False

        return {
            "status": "healthy" if all_healthy else "degraded",
            "engines": reports,
        }
