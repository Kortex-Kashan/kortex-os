"""KORTEX Backup Engine Pydantic data contracts and models.

Phase 7 — Production Hardening — Backup Engine.
Authoritative models for manifests, sidecar metadata, retention policies,
requests, responses, and diagnostics.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.backup.constants import (
    CURRENT_BACKUP_FORMAT_VERSION,
    CURRENT_ENGINE_VERSION,
    DEFAULT_BACKUP_ROOT,
    DEFAULT_MAX_AGE_DAYS,
    DEFAULT_MAX_COUNT,
    DEFAULT_MAX_SIZE_BYTES,
    SQLITE_ONLINE_BACKUP_PAGE_STEP,
    BackupComponentType,
    BackupScope,
    BackupState,
    RetentionPolicyType,
)


class ManifestComponentEntry(BaseModel):
    """Manifest record representing a captured constituent component."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Logical component name (e.g., 'database', 'storage/files')")
    component_type: BackupComponentType = Field(description="Categorical component type")
    relative_path: str = Field(description="Canonical path inside the backup archive")
    sha256: str = Field(description="SHA-256 digest of the component payload")
    size_bytes: int = Field(description="Total uncompressed size in bytes", ge=0)
    item_count: int | None = Field(default=None, description="Number of items bundled, if applicable")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Component-specific metadata")


class EncryptionMetadata(BaseModel):
    """Cryptographic parameters used to encrypt the payload envelope."""

    model_config = ConfigDict(frozen=True)

    algorithm: str = Field(default="AES-256-GCM", description="Cryptographic algorithm suite")
    key_id: str = Field(description="Logical key identifier used for encryption")
    nonce_hex: str = Field(description="Hex-encoded 96-bit AES-GCM nonce")
    tag_hex: str = Field(description="Hex-encoded 128-bit AES-GCM authentication tag")
    encrypted_sha256: str = Field(description="SHA-256 of the encrypted ciphertext payload")
    decrypted_sha256: str = Field(description="SHA-256 of the original unencrypted archive")
    key_version: int = Field(default=1, description="Version of the key specification")


class CompressionMetadata(BaseModel):
    """Compression algorithm and parameters."""

    model_config = ConfigDict(frozen=True)

    algorithm: str = Field(default="ZIP_DEFLATED", description="Compression algorithm")
    level: int = Field(default=6, description="Compression level", ge=0, le=9)


class BackupManifest(BaseModel):
    """Authoritative self-describing manifest embedded in the root of each backup artifact."""

    model_config = ConfigDict(frozen=True)

    format_version: int = Field(default=CURRENT_BACKUP_FORMAT_VERSION, description="Format version")
    backup_id: str = Field(description="Unique deterministic or generated backup identifier")
    created_at: str = Field(description="UTC timestamp of backup initiation in ISO-8601")
    engine_version: str = Field(default=CURRENT_ENGINE_VERSION, description="Backup engine version")
    kortex_version: str = Field(default="1.0.0", description="KORTEX OS application version")
    scope: BackupScope = Field(default=BackupScope.FULL_INSTANCE, description="Operational scope")
    instance_id: str = Field(description="Originating KORTEX instance identifier")
    components: list[ManifestComponentEntry] = Field(default_factory=list, description="Bundled components")
    encryption: EncryptionMetadata | None = Field(default=None, description="Cryptographic envelope metadata")
    compression: CompressionMetadata = Field(default_factory=CompressionMetadata, description="Compression metadata")
    state: BackupState = Field(default=BackupState.VALID, description="Final artifact state")
    database_schema_revision: str | None = Field(default=None, description="Alembic schema revision")
    total_size_bytes: int = Field(default=0, description="Total byte size of artifact", ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary instance-level metadata")


class ChecksumManifestEntry(BaseModel):
    """Digest record for a single constituent file in the archive."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Relative POSIX path inside the archive")
    sha256: str = Field(description="SHA-256 hex digest of file")
    size_bytes: int = Field(description="Size in bytes", ge=0)


class ChecksumManifest(BaseModel):
    """Comprehensive integrity inventory for every file contained in the artifact."""

    model_config = ConfigDict(frozen=True)

    format_version: int = Field(default=CURRENT_BACKUP_FORMAT_VERSION, description="Format version")
    backup_id: str = Field(description="Unique backup identifier")
    created_at: str = Field(description="UTC timestamp in ISO-8601")
    entries: list[ChecksumManifestEntry] = Field(default_factory=list, description="File checksum entries")


class BackupMetadata(BaseModel):
    """Filesystem sidecar metadata stored as `{backup_id}.meta.json`."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Unique backup identifier")
    state: BackupState = Field(description="Current lifecycle state")
    created_at: str = Field(description="UTC timestamp of initiation")
    finalized_at: str | None = Field(default=None, description="UTC timestamp of finalization")
    scope: BackupScope = Field(default=BackupScope.FULL_INSTANCE, description="Operational scope")
    filename: str = Field(description="Archive filename")
    file_size_bytes: int = Field(default=0, description="Size of artifact file in bytes", ge=0)
    sha256: str = Field(description="SHA-256 hex digest of the completed artifact")
    is_encrypted: bool = Field(default=True, description="True if payload is encrypted")
    key_id: str | None = Field(default=None, description="Key identifier if encrypted")
    database_schema_revision: str | None = Field(default=None, description="Schema revision")
    component_counts: dict[str, int] = Field(default_factory=dict, description="Counts of components")
    extra_metadata: dict[str, Any] = Field(default_factory=dict, description="Custom or contextual metadata")
    error_message: str | None = Field(default=None, description="Error detail if failed")


class RetentionPolicy(BaseModel):
    """Policy rules governing automatic pruning of expired backup artifacts."""

    model_config = ConfigDict(frozen=True)

    policy_type: RetentionPolicyType = Field(
        default=RetentionPolicyType.COMPOSITE,
        description="Evaluation strategy",
    )
    max_count: int = Field(default=DEFAULT_MAX_COUNT, description="Maximum valid backups to keep", ge=1)
    max_age_days: int = Field(default=DEFAULT_MAX_AGE_DAYS, description="Maximum retention age in days", ge=1)
    max_size_bytes: int = Field(
        default=DEFAULT_MAX_SIZE_BYTES,
        description="Maximum cumulative byte capacity for backups",
        ge=1,
    )


class BackupConfig(BaseModel):
    """Runtime configuration for the Backup Engine."""

    model_config = ConfigDict(frozen=True)

    backup_directory: str = Field(default=DEFAULT_BACKUP_ROOT, description="Canonical destination path")
    encryption_required: bool = Field(default=True, description="Fail closed if encryption key unavailable")
    key_id: str = Field(default="kortex-master-key", description="Key ID for encryption")
    scope: BackupScope = Field(default=BackupScope.FULL_INSTANCE, description="Default backup scope")
    retention_policy: RetentionPolicy = Field(default_factory=RetentionPolicy, description="Retention policy")
    scheduled_interval_seconds: int = Field(
        default=86400,
        description="Interval between automated background backups",
        ge=60,
    )
    max_concurrent_backups: int = Field(default=1, description="Maximum concurrent backups allowed", ge=1, le=1)
    sqlite_page_step: int = Field(
        default=SQLITE_ONLINE_BACKUP_PAGE_STEP,
        description="SQLite online backup page chunk size",
        ge=10,
    )


# Capability Input / Output Contracts


class CreateBackupRequest(BaseModel):
    """Request payload for `kortex.backup.create` capability."""

    model_config = ConfigDict(frozen=True)

    scope: BackupScope = Field(default=BackupScope.FULL_INSTANCE, description="Requested backup scope")
    idempotency_key: str | None = Field(default=None, description="Optional caller idempotency key")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional contextual metadata")


class CreateBackupResponse(BaseModel):
    """Response payload for `kortex.backup.create` capability."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Assigned unique backup identifier")
    state: BackupState = Field(description="Lifecycle state")
    created_at: str = Field(description="Initiation timestamp")
    finalized_at: str | None = Field(default=None, description="Finalization timestamp")
    filename: str = Field(description="Generated artifact filename")
    file_size_bytes: int = Field(description="Artifact size in bytes", ge=0)
    sha256: str = Field(description="Artifact SHA-256 digest")
    is_encrypted: bool = Field(description="Whether artifact is encrypted")
    key_id: str | None = Field(default=None, description="Encryption key ID")


class VerifyBackupRequest(BaseModel):
    """Request payload for `kortex.backup.verify` capability."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Identifier of the backup artifact to verify")


class VerifyBackupResponse(BaseModel):
    """Response payload for `kortex.backup.verify` capability."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Target backup identifier")
    is_valid: bool = Field(description="True if artifact satisfies all structural and crypto checks")
    state: BackupState = Field(description="State of verified backup")
    format_version: int = Field(description="Artifact format version")
    checksum_verified: bool = Field(description="Whether all internal checksums matched")
    encryption_verified: bool = Field(description="Whether AEAD tags and digests verified")
    schema_compatible: bool = Field(description="Whether database schema metadata is compatible")
    error_message: str | None = Field(default=None, description="Verification failure details if invalid")


class DeleteBackupRequest(BaseModel):
    """Request payload for `kortex.backup.delete` capability."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Identifier of the backup artifact to delete")


class DeleteBackupResponse(BaseModel):
    """Response payload for `kortex.backup.delete` capability."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Target backup identifier")
    deleted: bool = Field(description="True if artifact and sidecar metadata were deleted")
    state: BackupState = Field(default=BackupState.DELETED, description="Final state")


class ListBackupsRequest(BaseModel):
    """Request payload for `kortex.backup.list` capability."""

    model_config = ConfigDict(frozen=True)

    limit: int = Field(default=50, description="Maximum backups to return", ge=1, le=500)
    offset: int = Field(default=0, description="Pagination offset", ge=0)


class ListBackupsResponse(BaseModel):
    """Response payload for `kortex.backup.list` capability."""

    model_config = ConfigDict(frozen=True)

    backups: list[BackupMetadata] = Field(default_factory=list, description="List of backup records")
    total_count: int = Field(description="Total count of discovered backups", ge=0)


class GetBackupRequest(BaseModel):
    """Request payload for `kortex.backup.get` capability."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Unique backup identifier")


class GetBackupResponse(BaseModel):
    """Response payload for `kortex.backup.get` capability."""

    model_config = ConfigDict(frozen=True)

    backup: BackupMetadata = Field(description="Detailed backup metadata")
    manifest: BackupManifest | None = Field(default=None, description="Full manifest if accessible")


class BackupDiagnostics(BaseModel):
    """Technical self-diagnostics report implementing IEngineDiagnostics contract."""

    model_config = ConfigDict(frozen=True)

    engine_name: str = Field(default="backup", description="Engine identifier")
    state: str = Field(description="Engine lifecycle state")
    backup_root: str = Field(description="Canonical backup directory")
    total_backups: int = Field(description="Total existing backups", ge=0)
    valid_backups: int = Field(description="Count of valid backups", ge=0)
    last_successful_backup_at: str | None = Field(default=None, description="Timestamp of last success")
    last_failed_backup_at: str | None = Field(default=None, description="Timestamp of last failure")
    last_error_message: str | None = Field(default=None, description="Last recorded error")
    cumulative_size_bytes: int = Field(description="Total disk space consumed by backups", ge=0)
    encryption_enabled: bool = Field(description="Whether encryption is active")
    key_available: bool = Field(description="Whether encryption key is resolved")
    active_operation: str | None = Field(default=None, description="Currently running operation, if any")
    uptime_seconds: float = Field(description="Engine uptime in seconds", ge=0.0)
