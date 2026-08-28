"""KORTEX OS Marketplace Engine — M7 read-only catalog visibility slice.

This is the first real implementation of the Marketplace domain described
in `docs/architecture/marketplace_architecture.md`. That document specifies
a full distribution ecosystem (publishing, Ed25519 signing, licensing,
pricing, dependency resolution, federated repositories); none of it is
implemented here. This engine implements exactly one thing: a read-only
catalog an authenticated, authorized caller can list. Every other
capability the spec describes is future, unimplemented work — this module
must not be read as a partial implementation of them.

No dependency on Storage Engine (or any other engine): the catalog is
in-memory only, and nothing in this engine's `initialize()` needs to
resolve another engine's state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.marketplace.models import MarketplaceListing
from kortex.engines.marketplace.registry import MarketplaceCatalogRegistry

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel


class MarketplaceEngine(BaseEngine):
    """Core runtime facade for the KORTEX OS Marketplace catalog."""

    def __init__(self, registry: MarketplaceCatalogRegistry | None = None) -> None:
        super().__init__()
        self._registry = registry if registry is not None else MarketplaceCatalogRegistry()
        self._registered_capabilities: list[str] = [
            "kortex.marketplace.listing.list",
        ]

    @property
    def name(self) -> str:
        """Unique engine identifier name."""
        return "marketplace"

    @property
    def dependencies(self) -> list[str]:
        """No prerequisite engines — the catalog is in-memory only."""
        return []

    @property
    def registry(self) -> MarketplaceCatalogRegistry:
        """Access the catalog registry subsystem."""
        return self._registry

    # -- Lifecycle Implementation ---------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize engine resources and register capabilities with the Kernel."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Marketplace Engine...")

        try:
            kernel.register_capability(
                name="kortex.marketplace.listing.list",
                description="List catalog entries available in the Marketplace",
                provider=self.name,
                handler=self.list_listings,
                required_permissions=["marketplace:read"],
            )

            self._set_state(EngineState.READY)
            self.logger.info("Marketplace Engine initialized successfully.")
        except Exception as e:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize Marketplace Engine: %s", e, exc_info=True)
            raise

    async def start(self) -> None:
        """Start active background services (none for this engine)."""
        self.ensure_state(EngineState.READY, EngineState.STOPPED)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Marketplace Engine is RUNNING.")

    async def stop(self) -> None:
        """Gracefully shut down (no background tasks to release)."""
        self.ensure_state(EngineState.RUNNING, EngineState.READY)
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)
        self.logger.info("Marketplace Engine stopped.")

    async def health_check(self) -> dict[str, Any]:
        """Return diagnostic health information (BaseEngine async contract)."""
        return self.health()

    # -- Capability Handler ------------------------------------------------

    def list_listings(self) -> list[MarketplaceListing]:
        """List every catalog entry. An empty catalog is a valid result."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        return self._registry.list_listings()

    # -- Diagnostics (structurally matches IEngineDiagnostics; see
    # kortex.engines.workflow.engine for the same established precedent of
    # implementing this protocol without a formal per-module declaration) --

    def health(self) -> dict[str, Any]:
        """Return operational health status."""
        return {
            "engine": self.name,
            "status": "healthy" if self._state in (EngineState.READY, EngineState.RUNNING) else "unhealthy",
            "state": self._state.value,
            "listing_count": len(self._registry.list_listings()),
        }

    def metrics(self) -> dict[str, Any]:
        """Return runtime metrics."""
        return {"listing_count": len(self._registry.list_listings())}

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics."""
        return {
            "engine": self.name,
            "version": self.version(),
            "state": self._state.value,
            "capabilities": self.capabilities(),
            "metrics": self.metrics(),
        }

    def status(self) -> str:
        """Return current engine state name string."""
        return self._state.value

    def version(self) -> str:
        """Return engine semantic version string."""
        return "1.0.0"

    def capabilities(self) -> list[str]:
        """Return list of registered capability strings."""
        return list(self._registered_capabilities)


__all__ = ["MarketplaceEngine"]
