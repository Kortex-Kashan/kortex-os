"""KORTEX Backup Engine abstract interfaces and contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from kortex.engines.backup.models import (
    BackupConfig,
    BackupDiagnostics,
    BackupMetadata,
    CreateBackupRequest,
    CreateBackupResponse,
    DeleteBackupRequest,
    DeleteBackupResponse,
    GetBackupRequest,
    GetBackupResponse,
    ListBackupsRequest,
    ListBackupsResponse,
    RetentionPolicy,
    VerifyBackupRequest,
    VerifyBackupResponse,
)


class IBackupRepository(ABC):
    """Abstract storage repository managing backup artifacts and sidecar metadata."""

    @property
    @abstractmethod
    def backup_directory(self) -> Path:
        """Return the resolved absolute canonical backup directory."""

    @abstractmethod
    def resolve_artifact_path(self, filename: str) -> Path:
        """Resolve and validate that a filename remains securely within the backup directory."""

    @abstractmethod
    def list_backups(self, request: ListBackupsRequest) -> ListBackupsResponse:
        """List all discovered backups sorted chronologically descending."""

    @abstractmethod
    def get_backup(self, request: GetBackupRequest) -> GetBackupResponse:
        """Retrieve metadata and manifest for a specific backup."""

    @abstractmethod
    def save_metadata(self, metadata: BackupMetadata) -> None:
        """Persist sidecar metadata atomically to `{backup_id}.meta.json`."""

    @abstractmethod
    def delete_backup(self, request: DeleteBackupRequest) -> DeleteBackupResponse:
        """Atomically delete an artifact and its sidecar metadata."""

    @abstractmethod
    def cleanup_orphaned_temporaries(self, max_age_seconds: int = 3600) -> int:
        """Sweep and unlink any abandoned `.tmp` files older than max_age_seconds."""


class IBackupVerifier(ABC):
    """Abstract verifier performing structural, cryptographic, and schema validation."""

    @abstractmethod
    def verify_artifact(
        self,
        request: VerifyBackupRequest,
        repository: IBackupRepository,
        encryption_key: bytes | None,
    ) -> VerifyBackupResponse:
        """Verify the integrity, checksums, and envelope authentication of a backup."""


class IRetentionPolicyEngine(ABC):
    """Abstract retention manager enforcing count, age, and storage size policies."""

    @abstractmethod
    def evaluate_and_prune(
        self,
        repository: IBackupRepository,
        policy: RetentionPolicy,
        active_backup_id: str | None = None,
    ) -> list[str]:
        """Apply retention policies to prune eligible backups while preserving safety invariants.

        Returns:
            List of backup IDs that were pruned.
        """


class IBackupEngine(ABC):
    """Primary operational contract for the KORTEX Backup Engine."""

    @property
    @abstractmethod
    def config(self) -> BackupConfig:
        """Return the active engine configuration."""

    @abstractmethod
    async def create_backup(self, request: CreateBackupRequest) -> CreateBackupResponse:
        """Initiate and complete an atomic full-instance operational backup."""

    @abstractmethod
    async def verify_backup(self, request: VerifyBackupRequest) -> VerifyBackupResponse:
        """Verify an existing backup artifact."""

    @abstractmethod
    async def delete_backup(self, request: DeleteBackupRequest) -> DeleteBackupResponse:
        """Delete a backup artifact and its sidecar metadata."""

    @abstractmethod
    async def list_backups(self, request: ListBackupsRequest) -> ListBackupsResponse:
        """List all available backups."""

    @abstractmethod
    async def get_backup(self, request: GetBackupRequest) -> GetBackupResponse:
        """Retrieve backup metadata and manifest."""

    @abstractmethod
    def get_diagnostics(self) -> BackupDiagnostics:
        """Return technical self-diagnostics for the Backup Engine."""
