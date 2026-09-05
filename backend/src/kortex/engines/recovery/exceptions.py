"""KORTEX Recovery Engine exception hierarchy.

Phase 7 — Production Hardening — Recovery Engine.
Typed exceptions representing all fail-closed failure, validation,
concurrency, resource, database, storage, and rollback conditions.
"""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class RecoveryError(KortexError):
    """Base exception for all Recovery Engine failures."""


class RecoveryNotFoundError(RecoveryError):
    """Raised when a target backup artifact or recovery record is not found."""


class RecoveryArtifactCorruptError(RecoveryError):
    """Raised when an artifact exhibits truncation, bad central directory, or corruption."""


class RecoveryEncryptionError(RecoveryError):
    """Raised when cryptographic key material is missing, invalid, or AEAD fails."""


class RecoveryKeyError(RecoveryEncryptionError):
    """Raised when the required decryption key is unavailable or mismatched."""


class RecoveryValidationError(RecoveryError):
    """Raised when artifact, manifest, checksums, or staging verification fails."""


class RecoverySecurityError(RecoveryError):
    """Raised when path traversal, symlink, hardlink, or ZIP bomb is detected."""


class RecoveryCompatibilityError(RecoveryError):
    """Raised when backup format or schema revision is incompatible."""


class RecoveryPreflightError(RecoveryError):
    """Raised when pre-recovery environment checks fail."""


class RecoveryInsufficientDiskSpaceError(RecoveryPreflightError):
    """Raised when free disk space is less than required capacity formula."""


class PreRecoveryCheckpointError(RecoveryError):
    """Raised when the mandatory pre-recovery safety checkpoint fails to create."""


class RecoveryQuiescenceTimeoutError(RecoveryError):
    """Raised when workloads or connections fail to drain within timeout."""


class RecoveryDatabaseError(RecoveryError):
    """Raised when SQLite operations, migrations, or database file swaps fail."""


class RecoveryStorageError(RecoveryError):
    """Raised when storage subtrees cannot be accessed, moved, or restored."""


class RecoveryConsistencyError(RecoveryError):
    """Raised when database references and storage files fail referential check."""


class RecoveryConcurrencyError(RecoveryError):
    """Raised when concurrent recovery or backup operations conflict."""


class RecoveryRollbackError(RecoveryError):
    """Raised when an automated reverse-swap rollback operation fails."""


class RecoveryOperatorActionRequiredError(RecoveryError):
    """Raised when a system must halt in fail-closed MAINTENANCE state for manual operator repair."""


class RecoveryVerificationError(RecoveryError):
    """Raised when post-restore verification gates fail."""


class RecoveryAuthenticationError(RecoverySecurityError):
    """Raised when an operation lacks valid authentication or execution context."""


class RecoveryAuthorizationError(RecoverySecurityError):
    """Raised when a principal lacks required recovery permissions."""


class RecoveryCryptoError(RecoveryEncryptionError):
    """Raised when cryptographic operations fail."""


class RecoverySchemaCompatibilityError(RecoveryCompatibilityError):
    """Raised when database schema compatibility check fails."""


class RecoveryCrashError(RecoveryError):
    """Raised when a crash or interrupted state is detected."""


class RecoveryExtractionError(RecoverySecurityError):
    """Raised when archive extraction violates sandbox or size constraints."""


class RecoveryResourceError(RecoveryInsufficientDiskSpaceError):
    """Raised when system resources are insufficient for recovery."""


class RecoveryChecksumMismatchError(RecoveryValidationError):
    """Raised when SHA-256 checksum verification fails."""


class RecoveryStateError(RecoveryError):
    """Raised when an invalid lifecycle state transition is requested."""


class RecoveryJournalError(RecoveryError):
    """Raised when recovery journal read/write fails or is corrupt."""
