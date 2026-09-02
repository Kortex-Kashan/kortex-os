"""
KORTEX Core Exception Hierarchy.

All exception types raised by KORTEX Core, System Engines, and Modules inherit
from the base `KortexError` exception class.
"""

from __future__ import annotations


class KortexError(Exception):
    """Base exception for all errors in KORTEX OS."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# -- Engine Exceptions ------------------------------------------------------


class EngineError(KortexError):
    """Base class for System Engine errors."""


class EngineNotFoundError(EngineError):
    """Raised when an engine lookup fails."""


class EngineInitializationError(EngineError):
    """Raised when an engine fails during initialization or startup."""


class EngineStateError(EngineError):
    """Raised when an operation is invalid for the engine's current state."""


# -- Registry Exceptions ----------------------------------------------------


class RegistryError(KortexError):
    """Base class for Registry Engine errors."""


class ResourceAlreadyExistsError(RegistryError):
    """Raised when registering a resource name/ID that is already registered."""


class ResourceNotFoundError(RegistryError):
    """Raised when a requested resource is not found in the registry."""


class CapabilityNotFoundError(RegistryError):
    """Raised when a requested capability is not registered."""


# -- Configuration Exceptions -----------------------------------------------


class ConfigurationError(KortexError):
    """Base class for Configuration Engine errors."""


class ConfigurationValidationError(ConfigurationError):
    """Raised when configuration validation fails."""


class ConfigurationLoadError(ConfigurationError):
    """Raised when configuration cannot be loaded from a file/source."""


# -- Event Exceptions -------------------------------------------------------


class EventError(KortexError):
    """Base class for Event Engine errors."""


class EventPublishError(EventError):
    """Raised when event publishing fails."""


class EventSubscriberError(EventError):
    """Raised when an event subscriber handler throws an error."""


# -- Kernel Exceptions ------------------------------------------------------


class KernelError(KortexError):
    """Base class for Kernel Runtime errors."""


class KernelBootError(KernelError):
    """Raised when the Kernel fails during its boot sequence."""


class KernelStateError(KernelError):
    """Raised when an operation is executed in an invalid Kernel state."""


# -- Dispatcher & Idempotency Exceptions ------------------------------------


class DispatchError(KortexError):
    """Base class for Capability Dispatcher errors."""


class ConcurrentExecutionError(DispatchError):
    """Raised when a mutation with the same idempotency key is currently processing."""


class IdempotencyError(DispatchError):
    """Raised when an idempotency violation or conflict occurs."""


class ReservedParameterError(DispatchError):
    """Raised when a caller-supplied `CapabilityRequest.parameters` dict contains a
    reserved key (e.g. `execution_context`, `principal`) that only the Kernel's own
    trusted `CapabilityDispatcher` may populate. Rejected outright rather than
    silently discarded, so a caller mistake or attack attempt is loud and auditable,
    not swallowed."""
