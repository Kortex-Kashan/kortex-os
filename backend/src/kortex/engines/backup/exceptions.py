"""KORTEX Backup Engine exception hierarchy."""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class BackupError(KortexError):
    """Base exception for all Backup Engine failures."""


class BackupNotFoundError(BackupError):
    """Raised when a requested backup artifact or identifier does not exist."""


class BackupEncryptionError(BackupError):
    """Raised when cryptographic key material is missing, invalid, or crypto fails."""


class BackupValidationError(BackupError):
    """Raised when an artifact fails structural, checksum, or schema validation."""


class BackupCorruptionError(BackupError):
    """Raised when an artifact or live state exhibits data corruption."""


class BackupConcurrencyError(BackupError):
    """Raised when concurrent backup operations conflict."""


class BackupRetentionError(BackupError):
    """Raised when retention policy evaluation or execution encounters a safety failure."""


class BackupStorageError(BackupError):
    """Raised when underlying filesystem, disk space, or I/O operations fail."""


class BackupScopeError(BackupError):
    """Raised when an unsupported or invalid backup scope is requested."""


class BackupPathSecurityError(BackupError):
    """Raised when a path access attempt violates sandbox or backup root boundaries."""
