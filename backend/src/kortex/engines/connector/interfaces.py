"""Public abstract interfaces and protocol declarations for the KORTEX OS Connector Engine.

This module defines all formal Protocol interfaces exposed by the Connector Engine core,
enforcing Clean Architecture, Dependency Inversion, and strict type checking.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorProfile,
    DriverMetadata,
)


@runtime_checkable
class IBaseConnectorDriver(Protocol):
    """Abstract protocol for all connector driver plugins."""

    @property
    def driver_id(self) -> str:
        """Return unique driver identifier string."""
        ...

    @property
    def metadata(self) -> DriverMetadata:
        """Return immutable driver metadata object."""
        ...

    @property
    def supported_actions(self) -> list[ConnectorActionType]:
        """Return list of supported action types."""
        ...

    async def execute_action(
        self, request: ActionRequest, secret_token: str | None = None
    ) -> ActionResult:
        """Execute a connector action and return execution result."""
        ...

    async def test_connection(
        self, profile: ConnectorProfile, secret_token: str | None = None
    ) -> bool:
        """Test connection to target system using profile configuration."""
        ...


@runtime_checkable
class IConnectorEngine(Protocol):
    """Primary facade interface exposed by the Connector Engine."""

    async def execute_action(self, request: ActionRequest) -> ActionResult:
        """Execute action request through configured Connector Profile and Driver."""
        ...

    def register_driver(self, driver: IBaseConnectorDriver) -> None:
        """Register a new connector driver in the engine registry."""
        ...

    def list_drivers(self) -> list[DriverMetadata]:
        """Return list of metadata for all registered connector drivers."""
        ...

    async def get_profile(self, profile_id: str) -> ConnectorProfile:
        """Retrieve Connector Profile by profile ID."""
        ...


@runtime_checkable
class IConnectorDriverRegistry(Protocol):
    """Thread-safe registry protocol for registering and looking up connector drivers."""

    def register_driver(self, driver: IBaseConnectorDriver) -> None:
        """Register a connector driver in the registry."""
        ...

    def unregister_driver(self, driver_id: str) -> bool:
        """Unregister connector driver by driver ID."""
        ...

    def get_driver(self, driver_id: str) -> IBaseConnectorDriver:
        """Retrieve registered driver by driver ID."""
        ...

    def list_drivers(self) -> list[DriverMetadata]:
        """Return metadata for all registered connector drivers."""
        ...

    def find_drivers_for_action(
        self, action_type: ConnectorActionType
    ) -> list[DriverMetadata]:
        """Find drivers advertising support for a specific action type."""
        ...


@runtime_checkable
class IRateLimiter(Protocol):
    """Token-bucket rate limiter protocol for outbound connector actions."""

    async def acquire_token(self, key: str, tokens: float = 1.0) -> bool:
        """Attempt to acquire tokens for a given rate limit key."""
        ...

    async def release_token(self, key: str, tokens: float = 1.0) -> None:
        """Release tokens back to the specified rate limit key."""
        ...


@runtime_checkable
class IConnectorProfileManager(Protocol):
    """Interface for registering and resolving Connector Profiles."""

    async def get_profile(self, profile_id: str) -> ConnectorProfile:
        """Retrieve Connector Profile by profile ID."""
        ...

    async def register_profile(self, profile: ConnectorProfile) -> None:
        """Register or update a Connector Profile."""
        ...

    async def list_profiles(self) -> list[ConnectorProfile]:
        """Return all registered Connector Profiles."""
        ...


@runtime_checkable
class IConnectorDriverLoader(Protocol):
    """Interface for dynamic discovery and loading of connector drivers."""

    def load_driver(self, module_path: str, class_name: str) -> IBaseConnectorDriver:
        """Instantiate and return a driver plugin from module path."""
        ...

    def discover_drivers(self, package_path: str) -> list[DriverMetadata]:
        """Discover and inspect driver packages inside a directory."""
        ...


@runtime_checkable
class IConnectorPipeline(Protocol):
    """Interface for executing multi-stage Connector Pipelines."""

    async def execute(
        self, request: ActionRequest, profile: ConnectorProfile
    ) -> ActionResult:
        """Execute multi-stage pipeline for an action request."""
        ...


@runtime_checkable
class IEngineDiagnostics(Protocol):
    """Standardized diagnostics interface protocol exposed by KORTEX System Engines."""

    def health(self) -> dict[str, Any]:
        """Return operational health status and diagnostic checks."""
        ...

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and throughput metrics."""
        ...

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics and environment details."""
        ...

    def status(self) -> str:
        """Return current engine state name string."""
        ...

    def version(self) -> str:
        """Return semantic version string of the engine."""
        ...

    def capabilities(self) -> list[str]:
        """Return list of capability strings registered by the engine."""
        ...


__all__ = [
    "IBaseConnectorDriver",
    "IConnectorDriverLoader",
    "IConnectorDriverRegistry",
    "IConnectorEngine",
    "IConnectorPipeline",
    "IConnectorProfileManager",
    "IEngineDiagnostics",
    "IRateLimiter",
]
