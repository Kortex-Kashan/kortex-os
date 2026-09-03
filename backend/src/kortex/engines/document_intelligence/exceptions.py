"""KORTEX Document Intelligence Engine — Exception Hierarchy.

A dedicated hierarchy, rooted at `DocumentIntelligenceError`, kept
independent of `kortex.engines.document.exceptions` — the two engines are
architecturally independent (see `engine.py` module docstring); importing
another engine's exception module would be a cross-engine coupling this
engine deliberately avoids.
"""

from __future__ import annotations


class DocumentIntelligenceError(Exception):
    """Root of the Document Intelligence exception hierarchy."""


class InvalidRequestError(DocumentIntelligenceError):
    """Raised for a structurally invalid request not already caught by Pydantic validation."""


class TenantAuthorityError(DocumentIntelligenceError):
    """Raised when the caller-supplied session token cannot be cryptographically verified,
    or Security Engine is unavailable to perform that verification."""


class StorageAccessError(DocumentIntelligenceError):
    """Raised when Storage Engine is unavailable, or the referenced object cannot be retrieved."""


class CorruptedDocumentError(DocumentIntelligenceError):
    """Raised for a malformed/corrupt document payload that cannot be parsed at all."""


class EncryptedDocumentError(DocumentIntelligenceError):
    """Raised for a password-protected PDF whose content cannot be read without a password."""


class UnsupportedImageError(DocumentIntelligenceError):
    """Raised for an image payload the OCR provider cannot decode."""


class OCRProviderUnavailableError(DocumentIntelligenceError):
    """Raised when the OCR provider fails to initialize or is otherwise unavailable."""


class ExtractionTimeoutError(DocumentIntelligenceError):
    """Raised when a provider call exceeds the engine's configured timeout envelope."""


class ResourceLimitExceededError(DocumentIntelligenceError):
    """Raised when input size/page-count exceeds the engine's configured limits."""
