"""KORTEX Recovery Engine — Disaster recovery and system restoration.

Phase 7 — Production Hardening — Recovery Engine.
Authoritative system engine responsible for safe, staged, journaled,
and verifiable reconstitution of persistent database and storage state.
"""

from __future__ import annotations

from kortex.engines.recovery.constants import (
    CAPABILITY_RECOVERY_CREATE,
    CAPABILITY_RECOVERY_DELETE,
    CAPABILITY_RECOVERY_DIAGNOSTICS_GET,
    CAPABILITY_RECOVERY_GET,
    CAPABILITY_RECOVERY_LIST,
    CAPABILITY_RECOVERY_VERIFY,
    PERMISSION_RECOVERY_MANAGE,
    PERMISSION_RECOVERY_READ,
    RECOVERY_ENGINE_NAME,
    RecoveryJournalPhase,
    RecoveryState,
    RecoveryTargetType,
)
from kortex.engines.recovery.engine import RecoveryEngine
from kortex.engines.recovery.exceptions import (
    PreRecoveryCheckpointError,
    RecoveryArtifactCorruptError,
    RecoveryCompatibilityError,
    RecoveryConcurrencyError,
    RecoveryConsistencyError,
    RecoveryDatabaseError,
    RecoveryEncryptionError,
    RecoveryError,
    RecoveryInsufficientDiskSpaceError,
    RecoveryKeyError,
    RecoveryNotFoundError,
    RecoveryOperatorActionRequiredError,
    RecoveryPreflightError,
    RecoveryRollbackError,
    RecoverySecurityError,
    RecoveryStorageError,
    RecoveryValidationError,
    RecoveryVerificationError,
)
from kortex.engines.recovery.models import (
    CreateRecoveryRequest,
    CreateRecoveryResponse,
    DeleteRecoveryRequest,
    DeleteRecoveryResponse,
    GetRecoveryRequest,
    GetRecoveryResponse,
    ListRecoveriesRequest,
    ListRecoveriesResponse,
    RecoveryConfig,
    RecoveryDiagnostics,
    RecoveryJournalEntry,
    VerifyRecoveryRequest,
    VerifyRecoveryResponse,
)

__all__ = [
    # Capabilities & Permissions
    "CAPABILITY_RECOVERY_CREATE",
    "CAPABILITY_RECOVERY_DELETE",
    "CAPABILITY_RECOVERY_DIAGNOSTICS_GET",
    "CAPABILITY_RECOVERY_GET",
    "CAPABILITY_RECOVERY_LIST",
    "CAPABILITY_RECOVERY_VERIFY",
    "PERMISSION_RECOVERY_MANAGE",
    "PERMISSION_RECOVERY_READ",
    "RECOVERY_ENGINE_NAME",
    # Request / Response Models
    "CreateRecoveryRequest",
    "CreateRecoveryResponse",
    "DeleteRecoveryRequest",
    "DeleteRecoveryResponse",
    "GetRecoveryRequest",
    "GetRecoveryResponse",
    "ListRecoveriesRequest",
    "ListRecoveriesResponse",
    "PreRecoveryCheckpointError",
    "RecoveryArtifactCorruptError",
    "RecoveryCompatibilityError",
    "RecoveryConcurrencyError",
    "RecoveryConfig",
    "RecoveryConsistencyError",
    "RecoveryDatabaseError",
    "RecoveryDiagnostics",
    "RecoveryEncryptionError",
    # Engine
    "RecoveryEngine",
    # Exceptions
    "RecoveryError",
    "RecoveryInsufficientDiskSpaceError",
    "RecoveryJournalEntry",
    "RecoveryJournalPhase",
    "RecoveryKeyError",
    "RecoveryNotFoundError",
    "RecoveryOperatorActionRequiredError",
    "RecoveryPreflightError",
    "RecoveryRollbackError",
    "RecoverySecurityError",
    # Enums
    "RecoveryState",
    "RecoveryStorageError",
    "RecoveryTargetType",
    "RecoveryValidationError",
    "RecoveryVerificationError",
    "VerifyRecoveryRequest",
    "VerifyRecoveryResponse",
]
