"""KORTEX Recovery Engine Pydantic models and contracts.

Phase 7 — Production Hardening — Recovery Engine.
Authoritative models for recovery configuration, journals, request envelopes,
response payloads, previews, validations, and diagnostics.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.backup.constants import DEFAULT_BACKUP_ROOT
from kortex.engines.recovery.constants import (
    CURRENT_ENGINE_VERSION,
    CURRENT_RECOVERY_JOURNAL_VERSION,
    DEFAULT_QUIESCENCE_TIMEOUT_SECONDS,
    DEFAULT_RECOVERY_JOURNAL_DIR,
    DEFAULT_RECOVERY_STAGING_DIR,
    DEFAULT_ROLLBACK_RETENTION_HOURS,
    DEFAULT_SAFETY_MARGIN_BYTES,
    MAX_ARCHIVE_SIZE_BYTES,
    MAX_FILE_COUNT,
    RecoveryJournalPhase,
    RecoveryState,
)


class RecoveryConfig(BaseModel):
    """Runtime configuration for Recovery Engine."""

    model_config = ConfigDict(frozen=True)

    staging_directory: str = Field(
        default=DEFAULT_RECOVERY_STAGING_DIR,
        description="Path for isolated extraction and validation staging",
    )
    journal_directory: str = Field(
        default=DEFAULT_RECOVERY_JOURNAL_DIR,
        description="Path for write-ahead recovery journal and lockfiles",
    )
    backup_directory: str = Field(
        default=DEFAULT_BACKUP_ROOT,
        description="Canonical source path where backup archives reside",
    )
    quiescence_timeout_seconds: float = Field(
        default=DEFAULT_QUIESCENCE_TIMEOUT_SECONDS,
        description="Max seconds to wait for active workload drain before aborting",
        ge=1.0,
    )
    safety_margin_bytes: int = Field(
        default=DEFAULT_SAFETY_MARGIN_BYTES,
        description="Reserve disk space safety margin in bytes (500 MB)",
        ge=0,
    )
    rollback_retention_hours: int = Field(
        default=DEFAULT_ROLLBACK_RETENTION_HOURS,
        description="Hours to retain .rollback copies before cleanup",
        ge=1,
    )
    max_file_count: int = Field(
        default=MAX_FILE_COUNT,
        description="Maximum permitted extracted file count",
    )
    max_archive_size_bytes: int = Field(
        default=MAX_ARCHIVE_SIZE_BYTES,
        description="Maximum permitted archive file size in bytes",
    )


# -- Journal State Models ---------------------------------------------------


class TargetIdentity(BaseModel):
    """Identifies the local KORTEX instance undergoing recovery."""

    model_config = ConfigDict(frozen=True)

    instance_id: str = Field(description="Logical KORTEX instance ID")
    database_path: str = Field(description="Target SQLite database path")
    storage_root: str = Field(description="Target storage directory root")


class RollbackState(BaseModel):
    """Rollback coordination data recorded in recovery journal."""

    model_config = ConfigDict(frozen=True)

    status: str = Field(
        default="ARMED",
        description="ARMED, EXECUTING, ROLLED_BACK, or FAILED",
    )
    safety_checkpoint_id: str = Field(description="Backup ID of pre-recovery safety checkpoint")
    safety_checkpoint_sha256: str | None = Field(
        default=None,
        description="SHA-256 digest of safety checkpoint artifact",
    )
    rollback_sources: dict[str, str] = Field(
        default_factory=dict,
        description="Map of component name to .rollback filesystem path",
    )


class VerificationState(BaseModel):
    """Progressive verification results across recovery phases."""

    model_config = ConfigDict(frozen=True)

    staging_db_integrity: str = Field(default="PENDING", description="PASSED, FAILED, or PENDING")
    staged_migration_applied: bool = Field(default=False, description="Whether staged migration ran")
    live_db_integrity: str = Field(default="PENDING", description="Live SQLite integrity outcome")
    live_storage_verified: str = Field(default="PENDING", description="Live storage files check outcome")


class StagedStateLocations(BaseModel):
    """Locations of extracted and staged files."""

    model_config = ConfigDict(frozen=True)

    staging_root: str = Field(description="Root directory of this staging session")
    staged_db: str = Field(description="Path to staged kortex_snapshot.db")
    staged_storage: str = Field(description="Path to staged storage directory")


class ChecksumsMetadata(BaseModel):
    """Integrity hashes tracked during recovery."""

    model_config = ConfigDict(frozen=True)

    artifact_sha256: str = Field(description="SHA-256 of target backup artifact")
    staged_db_sha256: str | None = Field(default=None, description="SHA-256 of staged DB")
    safety_checkpoint_sha256: str | None = Field(default=None, description="SHA-256 of checkpoint")


class RecoveryJournalEntry(BaseModel):
    """Authoritative durable record stored at storage_data/.recovery/journal.json."""

    model_config = ConfigDict(frozen=True)

    journal_version: int = Field(default=CURRENT_RECOVERY_JOURNAL_VERSION)
    recovery_id: str = Field(description="Unique recovery operation ID")
    backup_id: str = Field(description="Target backup artifact ID being restored")
    target_identity: TargetIdentity = Field(description="Target instance metadata")
    created_at: str = Field(description="UTC timestamp of recovery initiation")
    updated_at: str = Field(description="UTC timestamp of last journal update")
    current_phase: RecoveryJournalPhase = Field(description="Current phase in state machine")
    completed_operations: list[str] = Field(
        default_factory=list,
        description="Chronological log of completed steps",
    )
    rollback_state: RollbackState = Field(description="Rollback source tracking")
    verification_state: VerificationState = Field(description="Verification outcomes")
    staged_state_locations: StagedStateLocations | None = Field(
        default=None,
        description="Staging directory pointers",
    )
    checksums: ChecksumsMetadata = Field(description="Cryptographic checksums")
    operator_notes: str | None = Field(default=None, description="Diagnostic notes for operator")
    error_message: str | None = Field(default=None, description="Error detail if failed")


# -- Capability Contracts (6 Canonical Capabilities) -------------------------


class CreateRecoveryRequest(BaseModel):
    """Request payload for `kortex.recovery.create` capability."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Target backup identifier to restore")
    confirm_destructive_restore: bool = Field(
        default=False,
        description="Must be explicitly True to authorize destructive live replacement",
    )
    encryption_key: str | None = Field(
        default=None,
        description="Optional hex/base64 key override for historical archives",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Optional 1-128 character client idempotency key",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional contextual audit metadata",
    )


class CreateRecoveryResponse(BaseModel):
    """Response payload for `kortex.recovery.create` capability."""

    model_config = ConfigDict(frozen=True)

    recovery_id: str = Field(description="Assigned unique recovery identifier")
    backup_id: str = Field(description="Target backup identifier")
    state: RecoveryState = Field(description="Final recovery lifecycle state")
    created_at: str = Field(description="Initiation timestamp in ISO-8601 UTC")
    completed_at: str | None = Field(default=None, description="Completion timestamp")
    safety_checkpoint_id: str = Field(description="Backup ID of created pre-recovery checkpoint")
    database_restored: bool = Field(description="Whether primary SQLite database was replaced")
    storage_files_restored: int = Field(default=0, description="Count of restored storage files", ge=0)
    duration_seconds: float = Field(default=0.0, description="Total execution duration in seconds")
    is_success: bool = Field(description="True if all verification tiers passed")
    error_message: str | None = Field(default=None, description="Error detail if failed")


class ListRecoveriesRequest(BaseModel):
    """Request payload for `kortex.recovery.list` capability."""

    model_config = ConfigDict(frozen=True)

    limit: int = Field(default=50, description="Maximum entries to return", ge=1, le=500)
    offset: int = Field(default=0, description="Pagination offset", ge=0)


class GetRecoveryRequest(BaseModel):
    """Request payload for `kortex.recovery.get` capability."""

    model_config = ConfigDict(frozen=True)

    recovery_id: str | None = Field(
        default=None,
        description="Identifier of recovery operation to query (None for latest/active)",
    )


class GetRecoveryResponse(BaseModel):
    """Response payload for `kortex.recovery.get` capability."""

    model_config = ConfigDict(frozen=True)

    recovery_id: str = Field(description="Recovery operation identifier")
    backup_id: str = Field(description="Associated backup identifier")
    state: RecoveryState = Field(description="Current state of operation")
    phase: RecoveryJournalPhase = Field(description="Current journal phase")
    created_at: str = Field(description="Initiation timestamp")
    updated_at: str = Field(description="Last update timestamp")
    safety_checkpoint_id: str | None = Field(default=None, description="Safety checkpoint ID")
    completed_operations: list[str] = Field(default_factory=list, description="Completed steps")
    is_active: bool = Field(default=False, description="True if operation is currently running")
    error_message: str | None = Field(default=None, description="Error detail if failed")
    journal: dict[str, Any] | None = Field(default=None, description="Raw journal summary if present")


class ListRecoveriesResponse(BaseModel):
    """Response payload for `kortex.recovery.list` capability."""

    model_config = ConfigDict(frozen=True)

    recoveries: list[GetRecoveryResponse] = Field(default_factory=list)
    total_count: int = Field(default=0, ge=0)


class VerifyRecoveryRequest(BaseModel):
    """Request payload for `kortex.recovery.verify` capability (non-destructive preflight)."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Identifier of backup artifact to preflight/verify")
    encryption_key: str | None = Field(default=None, description="Optional key override")


class VerifyRecoveryResponse(BaseModel):
    """Response payload for `kortex.recovery.verify` capability."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Target backup identifier")
    is_valid: bool = Field(description="True if artifact satisfies crypto, structural, & DB checks")
    checksum_verified: bool = Field(description="Whether all internal checksums matched")
    encryption_verified: bool = Field(description="Whether AEAD tag and key verified")
    schema_compatible: bool = Field(description="Whether schema is directly compatible or migratable")
    database_integrity_passed: bool = Field(description="Whether SQLite integrity_check passed")
    storage_referential_integrity_passed: bool = Field(description="Whether DB-referenced files exist")
    required_free_bytes: int = Field(default=0, description="Computed capacity requirement", ge=0)
    available_free_bytes: int = Field(default=0, description="Current volume free bytes", ge=0)
    has_sufficient_disk_space: bool = Field(description="True if free space >= required")
    schema_revision: str | None = Field(default=None, description="Backup Alembic schema revision")
    app_schema_revision: str | None = Field(default=None, description="Running app schema revision")
    requires_staged_migration: bool = Field(
        default=False,
        description="True if backup schema is older and requires staged Alembic upgrade",
    )
    error_message: str | None = Field(default=None, description="Failure detail if invalid")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings (e.g. orphan files)")


class DeleteRecoveryRequest(BaseModel):
    """Request payload for `kortex.recovery.delete` capability."""

    model_config = ConfigDict(frozen=True)

    recovery_id: str = Field(description="Identifier of recovery to clean or cancel")


class DeleteRecoveryResponse(BaseModel):
    """Response payload for `kortex.recovery.delete` capability."""

    model_config = ConfigDict(frozen=True)

    recovery_id: str = Field(description="Target recovery identifier")
    deleted: bool = Field(description="True if operation was cancelled or journal cleaned")
    message: str = Field(description="Summary of action taken")


class RecoveryDiagnostics(BaseModel):
    """Operational diagnostics for `kortex.recovery.diagnostics.get`."""

    model_config = ConfigDict(frozen=True)

    engine_name: str = Field(default="recovery")
    engine_version: str = Field(default=CURRENT_ENGINE_VERSION)
    state: str = Field(description="Engine lifecycle state")
    active_operation: str | None = Field(default=None, description="Active operation descriptor")
    recoveries_attempted: int = Field(default=0, ge=0)
    recoveries_completed: int = Field(default=0, ge=0)
    recoveries_failed: int = Field(default=0, ge=0)
    recoveries_rolled_back: int = Field(default=0, ge=0)
    last_recovery_duration_seconds: float = Field(default=0.0, ge=0.0)
    last_recovery_timestamp: str | None = Field(default=None)
    last_error_message: str | None = Field(default=None)
    journal_path: str = Field(description="Path to active or default journal file")
    staging_path: str = Field(description="Path to staging root")
    uptime_seconds: float = Field(default=0.0, ge=0.0)
