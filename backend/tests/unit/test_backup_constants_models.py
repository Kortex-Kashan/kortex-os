"""Unit tests for Backup Engine constants, enumerations, and Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kortex.engines.backup.constants import (
    BACKUP_ENGINE_NAME,
    BACKUP_EXTENSION,
    BACKUP_SECURITY_CLASSIFICATION,
    CAPABILITY_BACKUP_CREATE,
    CAPABILITY_BACKUP_DELETE,
    CAPABILITY_BACKUP_DIAGNOSTICS_GET,
    CAPABILITY_BACKUP_GET,
    CAPABILITY_BACKUP_LIST,
    CAPABILITY_BACKUP_VERIFY,
    CURRENT_BACKUP_FORMAT_VERSION,
    CURRENT_ENGINE_VERSION,
    DEFAULT_MAX_COUNT,
    PERMISSION_BACKUP_MANAGE,
    PERMISSION_BACKUP_READ,
    BackupComponentType,
    BackupScope,
    BackupState,
    RetentionPolicyType,
)
from kortex.engines.backup.models import (
    BackupManifest,
    BackupMetadata,
    ChecksumManifest,
    ChecksumManifestEntry,
    CompressionMetadata,
    CreateBackupRequest,
    CreateBackupResponse,
    DeleteBackupRequest,
    DeleteBackupResponse,
    EncryptionMetadata,
    GetBackupRequest,
    ListBackupsRequest,
    ListBackupsResponse,
    ManifestComponentEntry,
    RetentionPolicy,
    VerifyBackupRequest,
    VerifyBackupResponse,
)


def test_constants_and_enums() -> None:
    """Verify core engine constants and enum definitions."""
    assert BACKUP_ENGINE_NAME == "backup"
    assert BACKUP_SECURITY_CLASSIFICATION == "INTERNAL"
    assert BACKUP_EXTENSION == ".kortex-backup"
    assert CURRENT_BACKUP_FORMAT_VERSION == 1
    assert CURRENT_ENGINE_VERSION == "1.0.0"

    assert PERMISSION_BACKUP_READ == "system:backup:read"
    assert PERMISSION_BACKUP_MANAGE == "system:backup:manage"

    assert CAPABILITY_BACKUP_CREATE == "kortex.backup.create"
    assert CAPABILITY_BACKUP_LIST == "kortex.backup.list"
    assert CAPABILITY_BACKUP_GET == "kortex.backup.get"
    assert CAPABILITY_BACKUP_VERIFY == "kortex.backup.verify"
    assert CAPABILITY_BACKUP_DELETE == "kortex.backup.delete"
    assert CAPABILITY_BACKUP_DIAGNOSTICS_GET == "kortex.backup.diagnostics.get"

    assert BackupState.VALID.value == "VALID"
    assert BackupScope.FULL_INSTANCE.value == "FULL_INSTANCE"
    assert BackupComponentType.DATABASE.value == "DATABASE"
    assert RetentionPolicyType.COMPOSITE.value == "COMPOSITE"


def test_manifest_component_entry_serialization() -> None:
    """Verify ManifestComponentEntry model serialization and validation."""
    entry = ManifestComponentEntry(
        name="database",
        component_type=BackupComponentType.DATABASE,
        relative_path="database/kortex_snapshot.db",
        sha256="abc123def456",
        size_bytes=1024,
        item_count=1,
        metadata={"pages": 256},
    )
    assert entry.name == "database"
    assert entry.component_type == BackupComponentType.DATABASE
    assert entry.size_bytes == 1024

    with pytest.raises(ValidationError):
        ManifestComponentEntry(
            name="invalid",
            component_type=BackupComponentType.DATABASE,
            relative_path="invalid",
            sha256="abc",
            size_bytes=-1,  # Must be >= 0
        )


def test_backup_manifest_model() -> None:
    """Verify full BackupManifest serialization."""
    manifest = BackupManifest(
        format_version=1,
        backup_id="test_backup_001",
        created_at="2026-09-04T12:00:00Z",
        engine_version="1.0.0",
        kortex_version="1.0.0",
        scope=BackupScope.FULL_INSTANCE,
        instance_id="inst-1",
        components=[],
        encryption=EncryptionMetadata(
            algorithm="AES-256-GCM",
            key_id="kortex-master-key",
            nonce_hex="00" * 12,
            tag_hex="11" * 16,
            encrypted_sha256="enc_sha",
            decrypted_sha256="dec_sha",
            key_version=1,
        ),
        compression=CompressionMetadata(algorithm="ZIP_DEFLATED", level=6),
        state=BackupState.VALID,
        database_schema_revision="rev_001",
        total_size_bytes=5000,
    )
    dumped = manifest.model_dump(mode="json")
    assert dumped["backup_id"] == "test_backup_001"
    assert dumped["encryption"]["algorithm"] == "AES-256-GCM"

    # Round trip
    reconstructed = BackupManifest.model_validate(dumped)
    assert reconstructed.backup_id == manifest.backup_id
    assert reconstructed.encryption is not None
    assert reconstructed.encryption.key_id == "kortex-master-key"


def test_checksum_manifest_model() -> None:
    """Verify ChecksumManifest and entries."""
    entry = ChecksumManifestEntry(
        path="database/kortex_snapshot.db",
        sha256="hash123",
        size_bytes=2048,
    )
    manifest = ChecksumManifest(
        format_version=1,
        backup_id="b-123",
        created_at="2026-09-04T12:00:00Z",
        entries=[entry],
    )
    dumped = manifest.model_dump(mode="json")
    assert len(dumped["entries"]) == 1
    assert dumped["entries"][0]["path"] == "database/kortex_snapshot.db"


def test_backup_metadata_sidecar_model() -> None:
    """Verify BackupMetadata sidecar model."""
    meta = BackupMetadata(
        backup_id="b-sidecar",
        state=BackupState.VALID,
        created_at="2026-09-04T12:00:00Z",
        finalized_at="2026-09-04T12:01:00Z",
        scope=BackupScope.FULL_INSTANCE,
        filename="b-sidecar.kortex-backup",
        file_size_bytes=10000,
        sha256="sha-sidecar",
        is_encrypted=True,
        key_id="key-1",
        database_schema_revision="rev-1",
        component_counts={"database_pages": 10},
    )
    assert meta.is_encrypted is True
    assert meta.file_size_bytes == 10000


def test_retention_policy_defaults() -> None:
    """Verify RetentionPolicy defaults and constraints."""
    policy = RetentionPolicy()
    assert policy.policy_type == RetentionPolicyType.COMPOSITE
    assert policy.max_count == DEFAULT_MAX_COUNT
    assert policy.max_age_days == 30

    with pytest.raises(ValidationError):
        RetentionPolicy(max_count=0)


def test_requests_and_responses() -> None:
    """Verify capability request and response contracts."""
    create_req = CreateBackupRequest(scope=BackupScope.FULL_INSTANCE, idempotency_key="idemp-1")
    assert create_req.idempotency_key == "idemp-1"

    create_resp = CreateBackupResponse(
        backup_id="b-1",
        state=BackupState.VALID,
        created_at="2026-09-04T12:00:00Z",
        finalized_at="2026-09-04T12:00:05Z",
        filename="b-1.kortex-backup",
        file_size_bytes=1024,
        sha256="sha1",
        is_encrypted=True,
    )
    assert create_resp.backup_id == "b-1"

    verify_req = VerifyBackupRequest(backup_id="b-1")
    assert verify_req.backup_id == "b-1"
    verify_resp = VerifyBackupResponse(
        backup_id="b-1",
        is_valid=True,
        state=BackupState.VALID,
        format_version=1,
        checksum_verified=True,
        encryption_verified=True,
        schema_compatible=True,
    )
    assert verify_resp.is_valid is True

    del_req = DeleteBackupRequest(backup_id="b-1")
    assert del_req.backup_id == "b-1"
    del_resp = DeleteBackupResponse(backup_id="b-1", deleted=True, state=BackupState.DELETED)
    assert del_resp.deleted is True

    list_req = ListBackupsRequest(limit=25, offset=5)
    assert list_req.limit == 25
    list_resp = ListBackupsResponse(backups=[], total_count=0)
    assert list_resp.total_count == 0

    get_req = GetBackupRequest(backup_id="b-1")
    assert get_req.backup_id == "b-1"
