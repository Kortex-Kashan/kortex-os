"""KORTEX Backup Engine constants and enumerations.

Phase 7 — Production Hardening — Backup Engine.
Authoritative constants governing state, permissions, capability names,
events, limits, and defaults.
"""

from __future__ import annotations

import enum
from typing import Final

# Engine & Security Identity
BACKUP_ENGINE_NAME: Final[str] = "backup"
BACKUP_SECURITY_CLASSIFICATION: Final[str] = "INTERNAL"
BACKUP_SYSTEM_PRINCIPAL: Final[str] = "kortex-backup-system"

# Capabilities
CAPABILITY_BACKUP_CREATE: Final[str] = "kortex.backup.create"
CAPABILITY_BACKUP_LIST: Final[str] = "kortex.backup.list"
CAPABILITY_BACKUP_GET: Final[str] = "kortex.backup.get"
CAPABILITY_BACKUP_VERIFY: Final[str] = "kortex.backup.verify"
CAPABILITY_BACKUP_DELETE: Final[str] = "kortex.backup.delete"
CAPABILITY_BACKUP_DIAGNOSTICS_GET: Final[str] = "kortex.backup.diagnostics.get"

# Permissions
PERMISSION_BACKUP_READ: Final[str] = "system:backup:read"
PERMISSION_BACKUP_MANAGE: Final[str] = "system:backup:manage"

# Events
EVENT_BACKUP_REQUESTED: Final[str] = "backup.requested"
EVENT_BACKUP_STARTED: Final[str] = "backup.started"
EVENT_BACKUP_COMPLETED: Final[str] = "backup.completed"
EVENT_BACKUP_FAILED: Final[str] = "backup.failed"
EVENT_BACKUP_DELETED: Final[str] = "backup.deleted"
EVENT_BACKUP_VALIDATION_FAILED: Final[str] = "backup.validation_failed"

# File Extensions & Layout
BACKUP_EXTENSION: Final[str] = ".kortex-backup"
BACKUP_TMP_EXTENSION: Final[str] = ".kortex-backup.tmp"
BACKUP_METADATA_EXTENSION: Final[str] = ".meta.json"

# Versioning
CURRENT_BACKUP_FORMAT_VERSION: Final[int] = 1
CURRENT_ENGINE_VERSION: Final[str] = "1.0.0"

# Defaults
DEFAULT_BACKUP_ROOT: Final[str] = "storage_data/backups"
DEFAULT_MAX_COUNT: Final[int] = 10
DEFAULT_MAX_AGE_DAYS: Final[int] = 30
DEFAULT_MAX_SIZE_BYTES: Final[int] = 50 * 1024 * 1024 * 1024  # 50 GB
DEFAULT_RETENTION_INTERVAL_SECONDS: Final[int] = 3600  # 1 hour
DEFAULT_PREFLIGHT_DISK_MARGIN_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MB

# Operational & Resource Bounds
CHUNK_SIZE_BYTES: Final[int] = 64 * 1024  # 64 KB streaming buffer
MAX_METADATA_SIZE_BYTES: Final[int] = 10 * 1024 * 1024  # 10 MB maximum metadata entry
MAX_FILE_COUNT: Final[int] = 100_000  # Maximum files in a single backup
SQLITE_ONLINE_BACKUP_PAGE_STEP: Final[int] = 100  # Step size for SQLite online backup


class BackupState(str, enum.Enum):
    """Explicit lifecycle states for backup artifact generation."""

    REQUESTED = "REQUESTED"
    CAPTURING = "CAPTURING"
    PACKAGING = "PACKAGING"
    PROTECTING = "PROTECTING"
    VALIDATING = "VALIDATING"
    VALID = "VALID"
    FAILED = "FAILED"
    INCOMPLETE = "INCOMPLETE"
    DELETING = "DELETING"
    DELETED = "DELETED"


class BackupScope(str, enum.Enum):
    """Supported operational backup scopes."""

    FULL_INSTANCE = "FULL_INSTANCE"


class BackupComponentType(str, enum.Enum):
    """Categories of components bundled inside an artifact."""

    DATABASE = "DATABASE"
    STORAGE = "STORAGE"
    METADATA = "METADATA"
    SYSTEM = "SYSTEM"


class RetentionPolicyType(str, enum.Enum):
    """Retention policy evaluation strategies."""

    COUNT = "COUNT"
    AGE = "AGE"
    SIZE = "SIZE"
    COMPOSITE = "COMPOSITE"
