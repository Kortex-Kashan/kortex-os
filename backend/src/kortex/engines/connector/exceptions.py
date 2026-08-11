"""Custom exception hierarchy for the KORTEX OS Connector Engine.

This module defines all domain and infrastructure exceptions raised by the Connector Engine,
adhering to Clean Architecture and strict error classification principles.
"""

from __future__ import annotations


class ConnectorEngineError(Exception):
    """Base exception for all Connector Engine errors."""

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConnectorOperationError(ConnectorEngineError):
    """Raised when a connector action execution or operation fails."""


class ConnectorDriverError(ConnectorEngineError):
    """Base exception for connector driver plugin errors."""


class DriverNotFoundError(ConnectorDriverError):
    """Raised when a requested driver is not registered in the registry."""


class DriverExecutionError(ConnectorDriverError):
    """Raised when a driver execution fails within its sandboxed context."""


class DriverLoadError(ConnectorDriverError):
    """Raised when dynamic driver loading or discovery fails."""


class ConnectorProfileNotFoundError(ConnectorEngineError):
    """Raised when a requested Connector Profile is not found."""


class ConnectorValidationError(ConnectorEngineError):
    """Raised when request or profile validation fails."""


class RateLimitExceededError(ConnectorEngineError):
    """Raised when an outbound action is throttled by the rate limiter."""


class ConnectorSecurityError(ConnectorEngineError):
    """Raised when secret handle resolution or permission checks fail."""


class ConnectorConnectionError(ConnectorEngineError):
    """Raised when connection testing or endpoint validation fails."""


__all__ = [
    "ConnectorConnectionError",
    "ConnectorDriverError",
    "ConnectorEngineError",
    "ConnectorOperationError",
    "ConnectorProfileNotFoundError",
    "ConnectorSecurityError",
    "ConnectorValidationError",
    "DriverExecutionError",
    "DriverLoadError",
    "DriverNotFoundError",
    "RateLimitExceededError",
]
