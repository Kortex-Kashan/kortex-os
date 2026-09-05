"""KORTEX Update Engine typed exception hierarchy.

All exceptions inherit from `UpdateError`.
Security-related errors inherit from `UpdateSecurityError`.
"""

from __future__ import annotations


class UpdateError(Exception):
    """Base exception for all Update Engine errors."""


class UpdateSecurityError(UpdateError):
    """Base exception for security and authenticity violations in Update Engine."""


class UpdateAuthenticationError(UpdateSecurityError):
    """Raised when an update operation lacks required authentication or context."""


class UpdateAuthorizationError(UpdateSecurityError):
    """Raised when an update caller lacks required RBAC permissions."""


class UpdateManifestError(UpdateSecurityError):
    """Raised when an update manifest is malformed, invalid, or expired."""


class UpdateSignatureError(UpdateManifestError):
    """Raised when a digital signature over a manifest fails cryptographic verification."""


class UpdateKeyNotFoundError(UpdateManifestError):
    """Raised when an update manifest specifies an unknown or untrusted key ID."""


class UpdateChecksumMismatchError(UpdateSecurityError):
    """Raised when an archive or component SHA-256 digest fails verification."""


class UpdateArchiveSecurityError(UpdateSecurityError):
    """Raised when an update archive violates security constraints (traversal, symlink, etc.)."""


class UpdatePathTraversalError(UpdateArchiveSecurityError):
    """Raised when an update archive entry attempts directory traversal (e.g. '..', absolute path)."""


class UpdateZipBombError(UpdateArchiveSecurityError):
    """Raised when an update archive exceeds file count, expansion ratio, or uncompressed size limits."""


class UpdateCompatibilityError(UpdateError):
    """Base exception for platform, architecture, version, or schema incompatibilities."""


class UpdatePlatformMismatchError(UpdateCompatibilityError):
    """Raised when an update package does not match the host OS or CPU architecture."""


class UpdateSchemaIncompatibleError(UpdateCompatibilityError):
    """Raised when an update requires an unsupported or disconnected schema migration revision."""


class UpdateDowngradeError(UpdateSchemaIncompatibleError):
    """Raised when an update attempts an illegal version or schema downgrade."""


class UpdateDiskSpaceError(UpdateError):
    """Raised when available disk space is insufficient for safe staging and backup."""


class UpdateConcurrencyError(UpdateError):
    """Raised when an update operation conflicts with an active Update, Backup, or Recovery operation."""


class UpdateCheckpointError(UpdateError):
    """Raised when the mandatory pre-update safety checkpoint fails to be created or verified."""


class UpdateQuiescenceError(UpdateError):
    """Raised when the system fails to enter quiescence or drain active database connections."""


class UpdateMigrationError(UpdateError):
    """Raised when forward Alembic database schema migration fails."""


class UpdateSwapError(UpdateError):
    """Raised when filesystem component replacement fails."""


class UpdateVerificationError(UpdateError):
    """Raised when post-update verification (schema, integrity, import) fails."""


class UpdateRollbackError(UpdateError):
    """Raised when update rollback or disaster recovery delegation fails."""


class UpdateOperatorActionRequiredError(UpdateError):
    """Raised when the update system halts in a fail-closed FAILED_NEEDS_OPERATOR state."""


class UpdateNotFoundError(UpdateError):
    """Raised when a requested update operation or artifact is not found."""
