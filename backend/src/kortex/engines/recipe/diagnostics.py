"""
KORTEX Recipe Engine Diagnostics Provider.

Implements the Common Diagnostics Interface (IEngineDiagnostics) for telemetry,
health check monitoring, active metric tracking, and diagnostic inspection.
"""

from __future__ import annotations

from typing import Any, Dict, List
from kortex.engines.recipe.interfaces import IEngineDiagnostics

RECIPE_CAPABILITIES: List[str] = [
    "kortex.recipe.load",
    "kortex.recipe.validate",
    "kortex.recipe.compile",
    "kortex.recipe.install",
    "kortex.recipe.remove",
    "kortex.recipe.upgrade",
    "kortex.recipe.package",
    "kortex.recipe.search",
    "kortex.recipe.list",
    "kortex.recipe.info",
]


class RecipeDiagnostics(IEngineDiagnostics):
    """Diagnostics provider implementation for Recipe Engine."""

    def __init__(self, engine_state_provider: Any) -> None:
        self._engine_state_provider = engine_state_provider
        self._metrics: Dict[str, Any] = {
            "recipes_parsed": 0,
            "recipes_validated": 0,
            "recipes_compiled": 0,
            "recipes_installed": 0,
            "packages_created": 0,
            "errors": 0,
        }

    def increment_metric(self, key: str, amount: int = 1) -> None:
        """Increment a metric counter by amount."""
        if key in self._metrics:
            self._metrics[key] += amount
        else:
            self._metrics[key] = amount

    def health(self) -> Dict[str, Any]:
        """Return diagnostic health checks."""
        current_state = self.status()
        is_healthy = current_state in ("READY", "RUNNING")
        return {
            "engine": "recipe",
            "status": current_state,
            "healthy": is_healthy,
            "error_count": self._metrics.get("errors", 0),
        }

    def metrics(self) -> Dict[str, Any]:
        """Return operational runtime metrics."""
        return dict(self._metrics)

    def diagnostics(self) -> Dict[str, Any]:
        """Return complete deep diagnostic report."""
        return {
            "engine": "recipe",
            "version": self.version(),
            "status": self.status(),
            "capabilities": self.capabilities(),
            "metrics": self.metrics(),
            "health": self.health(),
        }

    def status(self) -> str:
        """Return current engine operational state string."""
        if hasattr(self._engine_state_provider, "state"):
            return str(self._engine_state_provider.state.value)
        return "READY"

    def version(self) -> str:
        """Return semantic version string of the engine."""
        return "1.0.0"

    def capabilities(self) -> List[str]:
        """Return canonical capability list registered by Recipe Engine."""
        return list(RECIPE_CAPABILITIES)
