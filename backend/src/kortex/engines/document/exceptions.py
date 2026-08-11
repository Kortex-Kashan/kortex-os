"""Custom exception hierarchy for the KORTEX OS Document Engine.

This module defines all domain and infrastructure exceptions raised by the Document Engine,
adhering to Clean Architecture and strict error classification principles.
"""

from __future__ import annotations


class DocumentEngineError(Exception):
    """Base exception for all Document Engine errors."""

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class DocumentOperationError(DocumentEngineError):
    """Raised when a document operation profile or adapter pipeline execution fails."""


class DocumentLifecycleError(DocumentEngineError):
    """Raised when an invalid lifecycle state transition or immutable lock violation occurs."""


class DocumentTemplateError(DocumentEngineError):
    """Raised when template resolution, validation, or context data binding fails."""


class DocumentAdapterError(DocumentEngineError):
    """Base exception for document adapter plugin errors."""


class AdapterNotFoundError(DocumentAdapterError):
    """Raised when a requested adapter or capability is not registered in the registry."""


class AdapterExecutionError(DocumentAdapterError):
    """Raised when an adapter execution fails inside the sandbox context."""


class DocumentProfileNotFoundError(DocumentEngineError):
    """Raised when a requested Document Operation Profile is not found."""


class DocumentSecurityError(DocumentEngineError):
    """Raised when security classification, label validation, or permission checks fail."""


class DocumentRecoveryError(DocumentEngineError):
    """Raised when execution checkpointing, retry, or rollback stack operation fails."""


__all__ = [
    "AdapterExecutionError",
    "AdapterNotFoundError",
    "DocumentAdapterError",
    "DocumentEngineError",
    "DocumentLifecycleError",
    "DocumentOperationError",
    "DocumentProfileNotFoundError",
    "DocumentRecoveryError",
    "DocumentSecurityError",
    "DocumentTemplateError",
]
