"""KORTEX Update Engine data contracts, manifest schemas, and journal models.

Phase 7 — Production Hardening — Update Engine.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.update.constants import (
    DEFAULT_POST_UPDATE_VERIFICATION_TIMEOUT_SECONDS,
    DEFAULT_QUIESCENCE_TIMEOUT_SECONDS,
    DEFAULT_UPDATE_DIR,
    MAX_ARCHIVE_SIZE_BYTES,
    MAX_UNCOMPRESSED_SIZE_BYTES,
    SAFETY_RESERVE_BYTES,
    UpdateJournalPhase,
    UpdateState,
)


class UpdateManifestVersion(BaseModel):
    """Version metadata within an update manifest."""

    model_config = ConfigDict(extra="forbid")

    target_version: str
    min_supported_version: str
    release_channel: str = "stable"
    release_notes: str = ""


class UpdateManifestPackage(BaseModel):
    """Archive metadata within an update manifest."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    sha256: str
    size_bytes: int
    uncompressed_bytes: int
    file_count: int


class UpdateManifestDatabase(BaseModel):
    """Database schema expectations within an update manifest."""

    model_config = ConfigDict(extra="forbid")

    requires_migration: bool = False
    target_revision: str | None = None
    expected_current_revision: str | None = None
    supported_source_revisions: list[str] = Field(default_factory=list)
    reversible: bool = False


class UpdateManifestCompatibility(BaseModel):
    """Host environment requirements within an update manifest."""

    model_config = ConfigDict(extra="forbid")

    platforms: list[str] = Field(default_factory=lambda: ["win32", "linux", "darwin"])
    architectures: list[str] = Field(default_factory=lambda: ["x86_64", "AMD64", "arm64", "aarch64"])
    python_version_min: str | None = "3.11"


class UpdateManifest(BaseModel):
    """Canonical cryptographically bound KORTEX update manifest."""

    model_config = ConfigDict(extra="forbid")

    manifest_version: str = "kortex-update-manifest-v1.0"
    manifest_id: str
    created_at: str
    expires_at: str
    key_id: str = "kortex-root-key"
    signature: str = ""
    version: UpdateManifestVersion
    package: UpdateManifestPackage
    database: UpdateManifestDatabase = Field(default_factory=UpdateManifestDatabase)
    compatibility: UpdateManifestCompatibility = Field(default_factory=UpdateManifestCompatibility)
    components: list[Any] = Field(default_factory=list)
    signatures: list[Any] = Field(default_factory=list)
    format_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateCheckRequest(BaseModel):
    """Request model for kortex.update.check."""

    model_config = ConfigDict(extra="forbid")

    channel: str = "stable"
    manifest_url: str | None = None
    manifest_content: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateCheckResponse(BaseModel):
    """Response model for kortex.update.check."""

    model_config = ConfigDict(extra="forbid")

    update_available: bool
    current_version: str
    target_version: str | None = None
    manifest: UpdateManifest | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class UpdateStageRequest(BaseModel):
    """Request model for kortex.update.stage."""

    model_config = ConfigDict(extra="forbid")

    manifest: UpdateManifest | None = None
    manifest_path: str | None = None
    package_path: str | None = None
    archive_path: str | None = None
    package_bytes: bytes | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateStageResponse(BaseModel):
    """Response model for kortex.update.stage."""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    target_version: str
    staging_path: str
    staged_at: str
    sha256_verified: bool
    staged: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class UpdateApplyRequest(BaseModel):
    """Request model for kortex.update.apply."""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateApplyResponse(BaseModel):
    """Response model for kortex.update.apply."""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    target_version: str
    status: UpdateState
    filesystem_updated: bool
    restart_required: bool
    runtime_activated: bool
    safety_checkpoint_id: str
    applied_at: str
    details: dict[str, Any] = Field(default_factory=dict)


class UpdateCancelRequest(BaseModel):
    """Request model for kortex.update.cancel."""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateCancelResponse(BaseModel):
    """Response model for kortex.update.cancel."""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    cancelled: bool
    purged_staging: bool
    details: dict[str, Any] = Field(default_factory=dict)


class UpdateJournalPhaseRecord(BaseModel):
    """Individual phase transition within durable journal."""

    model_config = ConfigDict(extra="forbid")

    phase: UpdateJournalPhase
    timestamp: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateJournalRecord(BaseModel):
    """Durable write-ahead journal for Update transactions."""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    manifest: UpdateManifest
    current_phase: UpdateJournalPhase
    created_at: str
    updated_at: str
    safety_checkpoint_id: str | None = None
    staging_directory: str | None = None
    target_version: str
    current_version: str
    phases: list[UpdateJournalPhaseRecord] = Field(default_factory=list)
    rollback_files: list[str] = Field(default_factory=list)
    error_message: str | None = None
    operator_notes: str | None = None
    filesystem_applied: bool = False
    restart_required: bool = False
    runtime_activated: bool = False


class UpdateHistoryEntry(BaseModel):
    """Audit entry for historical update operations."""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    target_version: str
    status: str
    started_at: str
    completed_at: str
    safety_checkpoint_id: str | None = None
    error_message: str | None = None


class UpdateGetRequest(BaseModel):
    """Request model for kortex.update.get."""

    model_config = ConfigDict(extra="forbid")

    update_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateGetResponse(BaseModel):
    """Response model for kortex.update.get."""

    model_config = ConfigDict(extra="forbid")

    active_update_id: str | None = None
    state: UpdateState
    current_version: str
    target_version: str | None = None
    journal_phase: UpdateJournalPhase | None = None
    active_journal: UpdateJournalRecord | None = None
    recent_history: list[UpdateHistoryEntry] = Field(default_factory=list)


class UpdateConfig(BaseModel):
    """Runtime configuration for Update Engine."""

    model_config = ConfigDict(extra="forbid")

    update_directory: str = DEFAULT_UPDATE_DIR
    staging_directory: str = f"{DEFAULT_UPDATE_DIR}/staging"
    max_archive_size_bytes: int = MAX_ARCHIVE_SIZE_BYTES
    max_uncompressed_size_bytes: int = MAX_UNCOMPRESSED_SIZE_BYTES
    safety_reserve_bytes: int = SAFETY_RESERVE_BYTES
    quiescence_timeout_seconds: float = DEFAULT_QUIESCENCE_TIMEOUT_SECONDS
    verification_timeout_seconds: float = DEFAULT_POST_UPDATE_VERIFICATION_TIMEOUT_SECONDS


# Convenience schema aliases
VersionMetadata = UpdateManifestVersion
PackageMetadata = UpdateManifestPackage
DatabaseMigrationMetadata = UpdateManifestDatabase
CompatibilityMetadata = UpdateManifestCompatibility
