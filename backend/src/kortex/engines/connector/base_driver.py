"""Abstract Base Class for Connector Drivers in the KORTEX OS Connector Engine.

This module defines the BaseConnectorDriver abstract base class that all Connector Driver
plugins MUST inherit. Protocol and driver implementations remain strictly decoupled
behind this abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorProfile,
    DriverMetadata,
)


class BaseConnectorDriver(ABC):
    """Abstract base class for all sandboxed connector driver plugins.

    All technology drivers (HTTP REST, Webhook, reference dummy plugins, etc.)
    implement this contract.
    """

    @property
    @abstractmethod
    def metadata(self) -> DriverMetadata:
        """Return immutable driver metadata object."""

    @property
    def driver_id(self) -> str:
        """Return unique driver identifier string."""
        return self.metadata.driver_id

    @property
    def supported_actions(self) -> list[ConnectorActionType]:
        """Return list of supported action types advertised by this driver."""
        return self.metadata.supported_actions

    def supports_action(self, action_type: ConnectorActionType) -> bool:
        """Check whether this driver advertises support for a specific action type."""
        return action_type in self.supported_actions

    @abstractmethod
    async def execute_action(
        self, request: ActionRequest, secret_token: str | None = None
    ) -> ActionResult:
        """Execute a connector action and return the result payload."""

    @abstractmethod
    async def test_connection(
        self, profile: ConnectorProfile, secret_token: str | None = None
    ) -> bool:
        """Test connectivity and configuration validity for target profile."""


__all__ = ["BaseConnectorDriver"]
