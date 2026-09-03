"""
KORTEX License Engine Exception Hierarchy (Milestone M5.7).

All license-related errors inherit from `LicenseEngineError`, which inherits
from KORTEX `EngineError`.
"""

from __future__ import annotations

from kortex.core.exceptions import EngineError


class LicenseEngineError(EngineError):
    """Base exception for all License Engine errors."""


# -- Cryptographic & Token Validation Errors --------------------------------


class LicenseValidationError(LicenseEngineError):
    """Base class for token validation and structural errors."""


class MalformedTokenError(LicenseValidationError):
    """Raised when token packaging, base64url encoding, or JSON is malformed."""


class UnsupportedAlgorithmError(LicenseValidationError):
    """Raised when token specifies an algorithm other than Ed25519."""


class UnsupportedScopeError(LicenseValidationError):
    """Raised when token scope is not 'TENANT' or contains an invalid tenant identifier."""


class UnknownKeyIdentifierError(LicenseValidationError):
    """Raised when token header 'kid' is not in the trusted root key store."""


class InvalidTokenSignatureError(LicenseValidationError):
    """Raised when the cryptographic signature over canonical payload fails verification."""


class InvalidKeyFormatError(LicenseValidationError):
    """Raised when root key material or signature bytes are of wrong length or format."""


class LicenseNotYetValidError(LicenseValidationError):
    """Raised when system clock is prior to the token's not_before timestamp."""


class LicenseExpiredError(LicenseValidationError):
    """Raised when token has exceeded its expiration date plus grace period."""


class LicenseRevokedError(LicenseValidationError):
    """Raised when attempting to activate a token that has been permanently revoked."""


# -- Security & Authorization Errors ----------------------------------------


class LicenseSecurityError(LicenseEngineError):
    """Base class for license security violations."""


class TenantMismatchError(LicenseSecurityError):
    """Raised when the caller's execution context tenant does not match token subject_tenant_id."""


class SecurityConfigurationError(LicenseSecurityError):
    """Raised when an unauthorized key override is attempted in production mode."""


# -- Lifecycle & State Errors ----------------------------------------------


class LicenseStateError(LicenseEngineError):
    """Base class for license lifecycle state errors."""


class LicenseConflictError(LicenseStateError):
    """Raised when submitting an identical license_id with divergent claims or token bytes."""


class TerminalLicenseError(LicenseStateError):
    """Raised when attempting to reactivate an EXPIRED, REVOKED, or SUPERSEDED license."""


class ConcurrentActivationError(LicenseStateError):
    """Raised when concurrent activation requests race for the same tenant."""


# -- Storage Errors --------------------------------------------------------


class LicenseStorageError(LicenseEngineError):
    """Base class for storage operation errors."""


class StorageOperationError(LicenseStorageError):
    """Raised when durable database commit fails during license activation or revocation."""
