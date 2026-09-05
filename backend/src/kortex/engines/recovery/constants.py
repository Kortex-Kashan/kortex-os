"""KORTEX Recovery Engine constants and enumerations.

Phase 7 — Production Hardening — Recovery Engine.
Authoritative constants governing state transitions, capability surfaces,
permissions, lifecycle events, filesystem paths, resource bounds, and defaults.
"""

from __future__ import annotations

import enum
from typing import Final

# Engine & Security Identity
RECOVERY_ENGINE_NAME: Final[str] = "recovery"
RECOVERY_SECURITY_CLASSIFICATION: Final[str] = "INTERNAL"
RECOVERY_SYSTEM_PRINCIPAL: Final[str] = "system:recovery"

# Capabilities (Canonical 6-capability surface)
CAPABILITY_RECOVERY_CREATE: Final[str] = "kortex.recovery.create"
CAPABILITY_RECOVERY_LIST: Final[str] = "kortex.recovery.list"
CAPABILITY_RECOVERY_GET: Final[str] = "kortex.recovery.get"
CAPABILITY_RECOVERY_VERIFY: Final[str] = "kortex.recovery.verify"
CAPABILITY_RECOVERY_DELETE: Final[str] = "kortex.recovery.delete"
CAPABILITY_RECOVERY_DIAGNOSTICS_GET: Final[str] = "kortex.recovery.diagnostics.get"

# Permissions
PERMISSION_RECOVERY_READ: Final[str] = "system:recovery:read"
PERMISSION_RECOVERY_MANAGE: Final[str] = "system:recovery:manage"

# Lifecycle Events (Canonical 12-event contract per implementation_plan.md)
EVENT_RECOVERY_REQUESTED: Final[str] = "kortex.recovery.requested"
EVENT_RECOVERY_PRECHECK_PASSED: Final[str] = "kortex.recovery.precheck.passed"
EVENT_RECOVERY_SAFETY_CHECKPOINT_CREATED: Final[str] = "kortex.recovery.safety_checkpoint.created"
EVENT_RECOVERY_VALIDATED: Final[str] = "kortex.recovery.validated"
EVENT_RECOVERY_STAGED: Final[str] = "kortex.recovery.staged"
EVENT_RECOVERY_QUIESCED: Final[str] = "kortex.recovery.quiesced"
EVENT_RECOVERY_SWAPPED: Final[str] = "kortex.recovery.swapped"
EVENT_RECOVERY_VERIFIED: Final[str] = "kortex.recovery.verified"
EVENT_RECOVERY_COMPLETED: Final[str] = "kortex.recovery.completed"
EVENT_RECOVERY_FAILED: Final[str] = "kortex.recovery.failed"
EVENT_RECOVERY_ROLLED_BACK: Final[str] = "kortex.recovery.rolled_back"
EVENT_RECOVERY_OPERATOR_INTERVENTION_REQUIRED: Final[str] = "kortex.recovery.operator_intervention_required"

# File Extensions, Names, and Paths
DEFAULT_RECOVERY_STAGING_DIR: Final[str] = "storage_data/.recovery_staging"
DEFAULT_RECOVERY_JOURNAL_DIR: Final[str] = "storage_data/.recovery"
DEFAULT_RECOVERY_JOURNAL_FILE: Final[str] = "storage_data/.recovery/journal.json"
DEFAULT_RECOVERY_LOCK_FILE: Final[str] = "storage_data/.recovery/maintenance.lock"
JOURNAL_TMP_EXTENSION: Final[str] = ".tmp"
ROLLBACK_SUFFIX: Final[str] = ".rollback_"

# Versioning
CURRENT_RECOVERY_JOURNAL_VERSION: Final[int] = 1
CURRENT_RECOVERY_FORMAT_VERSION: Final[int] = CURRENT_RECOVERY_JOURNAL_VERSION
CURRENT_ENGINE_VERSION: Final[str] = "1.0.0"

# Operational Defaults & Resource Bounds
DEFAULT_QUIESCENCE_TIMEOUT_SECONDS: Final[float] = 30.0
RECOVERY_DEFAULT_LOCK_TIMEOUT_SECONDS: Final[float] = DEFAULT_QUIESCENCE_TIMEOUT_SECONDS
DEFAULT_SAFETY_MARGIN_BYTES: Final[int] = 500 * 1024 * 1024  # 500 MB constant reserve
RECOVERY_DEFAULT_RESERVE_BYTES: Final[int] = DEFAULT_SAFETY_MARGIN_BYTES
DEFAULT_ROLLBACK_RETENTION_HOURS: Final[int] = 72  # 72 hours safety window
CHUNK_SIZE_BYTES: Final[int] = 64 * 1024  # 64 KB streaming buffer
MAX_FILE_COUNT: Final[int] = 100_000  # Maximum files in a single recovery
MAX_ARCHIVE_SIZE_BYTES: Final[int] = 50 * 1024 * 1024 * 1024  # 50 GB maximum archive
MAX_DECOMPRESSION_RATIO: Final[int] = 100  # ZIP bomb threshold ratio


class RecoveryComponentType(str, enum.Enum):
    """Component categories restored during recovery."""

    DATABASE = "database"
    STORAGE = "storage"


class RecoveryState(str, enum.Enum):
    """Explicit lifecycle states for recovery operations."""

    REQUESTED = "REQUESTED"
    PRECHECKING = "PRECHECKING"
    CHECKPOINTING = "CHECKPOINTING"
    VALIDATING = "VALIDATING"
    STAGING = "STAGING"
    PREPARING_SWAP = "PREPARING_SWAP"
    SWAPPING = "SWAPPING"
    RECONNECTING = "RECONNECTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED_NEEDS_OPERATOR = "FAILED_NEEDS_OPERATOR"


class RecoveryJournalPhase(str, enum.Enum):
    """Durable phase tracking inside storage_data/.recovery/journal.json."""

    CREATED = "CREATED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    ARTIFACT_VALIDATED = "ARTIFACT_VALIDATED"
    STAGING = "STAGING"
    STAGED = "STAGED"
    PRE_SWAP = "PRE_SWAP"
    STORAGE_SWAP_PARTIAL = "STORAGE_SWAP_PARTIAL"
    STORAGE_SWAP_COMPLETE = "STORAGE_SWAP_COMPLETE"
    DATABASE_SWAP_COMPLETE = "DATABASE_SWAP_COMPLETE"
    RECONNECTING = "RECONNECTING"
    VERIFYING = "VERIFYING"
    COMMITTED = "COMMITTED"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED_NEEDS_OPERATOR = "FAILED_NEEDS_OPERATOR"


class RecoveryTargetType(str, enum.Enum):
    """Supported recovery scope target."""

    FULL_INSTANCE = "FULL_INSTANCE"


RecoveryScope = RecoveryTargetType
