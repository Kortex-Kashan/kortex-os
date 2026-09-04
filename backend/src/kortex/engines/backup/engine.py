"""KORTEX Backup Engine main facade and coordinator.

Phase 7 — Production Hardening — Backup Engine.
Coordinates the 8-stage backup lifecycle:
BACKUP -> CAPTURE -> PACKAGE -> PROTECT -> VALIDATE -> RETAIN -> INDEX -> REPORT.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.backup.capture import DatabaseSnapshotCapture, StoragePayloadCapture
from kortex.engines.backup.constants import (
    BACKUP_ENGINE_NAME,
    BACKUP_EXTENSION,
    BACKUP_SECURITY_CLASSIFICATION,
    BACKUP_TMP_EXTENSION,
    CAPABILITY_BACKUP_CREATE,
    CAPABILITY_BACKUP_DELETE,
    CAPABILITY_BACKUP_DIAGNOSTICS_GET,
    CAPABILITY_BACKUP_GET,
    CAPABILITY_BACKUP_LIST,
    CAPABILITY_BACKUP_VERIFY,
    DEFAULT_PREFLIGHT_DISK_MARGIN_BYTES,
    PERMISSION_BACKUP_MANAGE,
    PERMISSION_BACKUP_READ,
    BackupScope,
    BackupState,
)
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.diagnostics import BackupDiagnosticsAdapter
from kortex.engines.backup.events import BackupEventPublisher
from kortex.engines.backup.exceptions import (
    BackupConcurrencyError,
    BackupEncryptionError,
    BackupScopeError,
    BackupStorageError,
    BackupValidationError,
)
from kortex.engines.backup.interfaces import IBackupEngine
from kortex.engines.backup.models import (
    BackupConfig,
    BackupDiagnostics,
    CreateBackupRequest,
    CreateBackupResponse,
    DeleteBackupRequest,
    DeleteBackupResponse,
    GetBackupRequest,
    GetBackupResponse,
    ListBackupsRequest,
    ListBackupsResponse,
    VerifyBackupRequest,
    VerifyBackupResponse,
)
from kortex.engines.backup.packager import BackupPackager
from kortex.engines.backup.repository import BackupRepository
from kortex.engines.backup.retention import RetentionEngine
from kortex.engines.backup.verifier import BackupVerifier
from kortex.engines.storage.interfaces import IEngineDiagnostics

if TYPE_CHECKING:
    from kortex.core.dispatch import CapabilityExecutionContext
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.backup")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class BackupEngine(BaseEngine, IBackupEngine, IEngineDiagnostics):
    """Authoritative KORTEX Backup Engine."""

    def __init__(
        self,
        config: BackupConfig | None = None,
        crypto_manager: BackupCryptoManager | None = None,
    ) -> None:
        super().__init__()
        self._config = config or BackupConfig()
        self._kernel: Kernel | None = None
        self._started_at_monotonic: float | None = None

        # Repositories & Subsystems
        self._repository = BackupRepository(self._config.backup_directory)
        self._db_capture = DatabaseSnapshotCapture(self._config.sqlite_page_step)
        self._verifier = BackupVerifier()
        self._retention = RetentionEngine()
        self._event_publisher = BackupEventPublisher()
        self._diagnostics_adapter = BackupDiagnosticsAdapter(self)

        # Cryptographic manager
        if crypto_manager is not None:
            self._crypto = crypto_manager
        else:
            try:
                self._crypto = BackupCryptoManager(
                    key_id=self._config.key_id,
                    encryption_required=self._config.encryption_required,
                )
            except BackupEncryptionError:
                if self._config.encryption_required:
                    # Will be re-checked or fail closed during capture
                    pass
                self._crypto = None  # type: ignore[assignment]

        self._packager: BackupPackager | None = None
        if self._crypto is not None:
            self._packager = BackupPackager(self._crypto)

        # Concurrency & Operational State
        self._backup_lock = asyncio.Lock()
        self._active_operation: str | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()

        # Telemetry & Metrics
        self._backups_created_count = 0
        self._backups_failed_count = 0
        self._backups_verified_count = 0
        self._backups_pruned_count = 0
        self._last_backup_duration_seconds = 0.0
        self._last_successful_backup_at: str | None = None
        self._last_failed_backup_at: str | None = None
        self._last_error_message: str | None = None

    @property
    def name(self) -> str:
        return BACKUP_ENGINE_NAME

    @property
    def dependencies(self) -> list[str]:
        return ["storage", "security"]

    @property
    def config(self) -> BackupConfig:
        return self._config

    @property
    def repository(self) -> BackupRepository:
        return self._repository

    @property
    def crypto_manager(self) -> BackupCryptoManager:
        if self._crypto is None:
            # Re-attempt resolution
            self._crypto = BackupCryptoManager(
                key_id=self._config.key_id,
                encryption_required=self._config.encryption_required,
            )
            self._packager = BackupPackager(self._crypto)
        return self._crypto

    @property
    def background_tasks(self) -> frozenset[asyncio.Task[Any]]:
        return frozenset(self._background_tasks)

    @property
    def total_backups_count(self) -> int:
        try:
            return self._repository.list_backups(ListBackupsRequest(limit=500)).total_count
        except Exception:
            return 0

    @property
    def backups_created_count(self) -> int:
        return self._backups_created_count

    @property
    def backups_failed_count(self) -> int:
        return self._backups_failed_count

    @property
    def backups_verified_count(self) -> int:
        return self._backups_verified_count

    @property
    def backups_pruned_count(self) -> int:
        return self._backups_pruned_count

    @property
    def last_backup_duration_seconds(self) -> float:
        return self._last_backup_duration_seconds

    @property
    def last_error_message(self) -> str | None:
        return self._last_error_message

    @property
    def cumulative_storage_bytes(self) -> int:
        try:
            backups = self._repository.list_backups(ListBackupsRequest(limit=500)).backups
            return sum(b.file_size_bytes for b in backups)
        except Exception:
            return 0

    # -- Lifecycle -----------------------------------------------------------

    async def initialize(self, kernel: Kernel | None = None) -> None:
        """Initialize resources and register capabilities with Kernel."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)

        try:
            self._kernel = kernel
            if kernel is not None:
                self._event_publisher.set_kernel(kernel)

                # IoC container registration
                container = getattr(kernel, "container", None)
                if (
                    container is not None
                    and hasattr(container, "register_instance")
                    and hasattr(container, "has")
                    and not container.has("engine.backup")
                ):
                    container.register_instance("engine.backup", self)

                # Register the 6 approved capabilities
                if hasattr(kernel, "register_capability"):
                    kernel.register_capability(
                        name=CAPABILITY_BACKUP_CREATE,
                        description="Initiate and complete an atomic full-instance operational backup.",
                        provider=self.name,
                        handler=self.handle_backup_create,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_BACKUP_MANAGE],
                        security_classification=BACKUP_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_BACKUP_LIST,
                        description="List all available backup artifacts and sidecar metadata.",
                        provider=self.name,
                        handler=self.handle_backup_list,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_BACKUP_READ],
                        security_classification=BACKUP_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_BACKUP_GET,
                        description="Retrieve metadata and manifest for a specific backup.",
                        provider=self.name,
                        handler=self.handle_backup_get,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_BACKUP_READ],
                        security_classification=BACKUP_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_BACKUP_VERIFY,
                        description="Verify structural, cryptographic, and database integrity of a backup.",
                        provider=self.name,
                        handler=self.handle_backup_verify,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_BACKUP_READ],
                        security_classification=BACKUP_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_BACKUP_DELETE,
                        description="Atomically delete a backup artifact and its sidecar metadata.",
                        provider=self.name,
                        handler=self.handle_backup_delete,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_BACKUP_MANAGE],
                        security_classification=BACKUP_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_BACKUP_DIAGNOSTICS_GET,
                        description="Retrieve technical self-diagnostics for the Backup Engine.",
                        provider=self.name,
                        handler=self.handle_backup_diagnostics_get,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_BACKUP_READ],
                        security_classification=BACKUP_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )

            # Startup routine: clean up orphaned temporary archives
            cleaned = self._repository.cleanup_orphaned_temporaries(max_age_seconds=3600)
            if cleaned > 0:
                logger.info("Startup sweep cleaned %d orphaned temporary backup files.", cleaned)

            self._set_state(EngineState.READY)
            logger.info("Backup Engine initialized successfully.")
        except Exception as exc:
            self._set_state(EngineState.FAILED)
            logger.error("Failed to initialize Backup Engine: %s", exc)
            raise

    async def start(self) -> None:
        """Start background scheduling loop if configured."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self._started_at_monotonic = time.monotonic()

        if self._config.scheduled_interval_seconds > 0:
            task = asyncio.create_task(self._scheduler_loop(), name="kortex_backup_scheduler_loop")
            self._background_tasks.add(task)
            task.add_done_callback(self._on_background_task_done)

        logger.info("Backup Engine started cleanly.")

    async def stop(self) -> None:
        """Gracefully stop background loop and cancel tasks."""
        self.ensure_state(EngineState.RUNNING, EngineState.READY)
        self._set_state(EngineState.STOPPING)

        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()

        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        self._set_state(EngineState.STOPPED)
        logger.info("Backup Engine stopped cleanly.")

    def _on_background_task_done(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Background task '%s' raised exception: %s", task.get_name(), task.exception())

    async def _scheduler_loop(self) -> None:
        """Background loop executing scheduled automated backups."""
        interval = self._config.scheduled_interval_seconds
        while self.state == EngineState.RUNNING:
            try:
                await asyncio.sleep(interval)
                if self.state != EngineState.RUNNING:
                    break
                logger.info("Executing scheduled background backup...")
                req = CreateBackupRequest(
                    scope=self._config.scope,
                    metadata={"source": "scheduled_background"},
                )
                await self.create_backup(req)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error during scheduled background backup execution: %s", exc)

    # -- Resolution Helpers --------------------------------------------------

    def _resolve_live_db_path(self) -> Path:
        """Resolve the active SQLite database path."""
        # Check environment override
        env_url = os.environ.get("KORTEX_DATABASE_URL")
        if env_url and "sqlite" in env_url:
            cleaned = env_url.split("sqlite+aiosqlite:///")[-1].split("sqlite:///")[-1]
            return Path(cleaned).resolve()

        if self._kernel is not None and hasattr(self._kernel, "db") and self._kernel.db:
            db_url = getattr(self._kernel.db, "_url", None)
            if db_url and "sqlite" in db_url:
                cleaned = db_url.split("sqlite+aiosqlite:///")[-1].split("sqlite:///")[-1]
                return Path(cleaned).resolve()

        # Default standard app data directory
        if os.name == "nt":
            base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        else:
            base = str(Path.home() / ".local" / "share")
        return (Path(base) / "KORTEX" / "kortex_local.db").resolve()

    def _resolve_storage_root(self) -> Path:
        """Resolve the active storage root directory."""
        if self._kernel is not None:
            storage_engine = self._kernel.get_engine("storage")
            if storage_engine is not None and hasattr(storage_engine, "base_directory"):
                return Path(storage_engine.base_directory).resolve()

        env_dir = os.environ.get("KORTEX_STORAGE_DIR", "storage_data")
        return Path(env_dir).resolve()

    def _preflight_disk_check(self, estimated_bytes: int = 0) -> None:
        """Ensure sufficient disk space exists on the backup volume."""
        backup_dir = self._repository.backup_directory
        try:
            usage = shutil.disk_usage(backup_dir)
            required = estimated_bytes * 2 + DEFAULT_PREFLIGHT_DISK_MARGIN_BYTES
            if usage.free < required:
                raise BackupStorageError(
                    f"Insufficient disk space on backup volume: required at least {required} bytes, "
                    f"but only {usage.free} bytes free."
                )
        except OSError as exc:
            raise BackupStorageError(f"Preflight disk check failed: {exc}") from exc

    # -- Primary Operations (IBackupEngine) ----------------------------------

    async def create_backup(self, request: CreateBackupRequest) -> CreateBackupResponse:
        """Execute the 8-stage operational backup lifecycle."""
        if request.scope != BackupScope.FULL_INSTANCE:
            raise BackupScopeError(f"Unsupported backup scope: '{request.scope}'. Only FULL_INSTANCE is supported.")

        # Stage 1: BACKUP (Request & Concurrency Guard)
        if self._backup_lock.locked():
            raise BackupConcurrencyError("Another backup operation is currently in progress.")

        async with self._backup_lock:
            start_time = time.monotonic()
            now_iso = _utc_now_iso()
            backup_id = f"kortex_backup_{int(time.time())}_{uuid.uuid4().hex[:8]}"

            await self._event_publisher.emit_requested(backup_id, request.scope)
            self._active_operation = f"create_backup:{backup_id}"

            # Prepare workspace paths
            workspace_dir = self._repository.backup_directory / ".workspace"
            workspace_dir.mkdir(parents=True, exist_ok=True)
            db_snap_path = workspace_dir / f"{backup_id}_kortex_snapshot.db"
            tmp_unencrypted_zip = workspace_dir / f"{backup_id}_raw.zip"
            tmp_final_path = self._repository.resolve_artifact_path(f"{backup_id}{BACKUP_TMP_EXTENSION}")
            final_target_path = self._repository.resolve_artifact_path(f"{backup_id}{BACKUP_EXTENSION}")

            try:
                # Stage 2: PREFLIGHT DISK CHECK
                self._preflight_disk_check()

                # Stage 3: CAPTURE
                await self._event_publisher.emit_started(backup_id, request.scope)

                # 3.a Capture Database
                live_db = self._resolve_live_db_path()
                db_entry, schema_rev = await self._db_capture.capture_database(
                    source_db_path=live_db,
                    destination_db_path=db_snap_path,
                )

                # 3.b Capture Storage Blobs
                storage_root = self._resolve_storage_root()
                storage_capture = StoragePayloadCapture(storage_root)
                storage_files, storage_checksums, _ = await asyncio.to_thread(storage_capture.scan_storage_files)

                # Stage 4 & 5: PACKAGE & PROTECT
                # Enforce fail-closed cryptographic requirements
                crypto = self.crypto_manager
                if not crypto.is_key_available:
                    raise BackupEncryptionError(
                        "Backup encryption required by default, but no valid key is available. Failing closed."
                    )

                packager = BackupPackager(crypto)
                instance_id = "kortex-instance"
                if self._kernel is not None and hasattr(self._kernel, "instance_id"):
                    instance_id = str(self._kernel.instance_id)

                _manifest, sidecar_meta = await asyncio.to_thread(
                    packager.assemble_backup,
                    backup_id=backup_id,
                    instance_id=instance_id,
                    kortex_version="1.0.0",
                    scope=request.scope,
                    created_at_iso=now_iso,
                    db_snapshot_path=db_snap_path,
                    db_manifest_entry=db_entry,
                    schema_revision=schema_rev,
                    storage_files=storage_files,
                    storage_checksums=storage_checksums,
                    tmp_unencrypted_zip=tmp_unencrypted_zip,
                    tmp_final_path=tmp_final_path,
                    final_target_path=final_target_path,
                    extra_metadata=request.metadata,
                )

                # Save sidecar metadata
                self._repository.save_metadata(sidecar_meta)

                # Stage 6: VALIDATE
                # Perform self-verification of the finalized archive
                verify_res = self._verifier.verify_artifact(
                    request=VerifyBackupRequest(backup_id=backup_id),
                    repository=self._repository,
                    encryption_key=crypto._key,
                )
                if not verify_res.is_valid:
                    # Remove invalid artifact and sidecar metadata
                    final_target_path.unlink(missing_ok=True)
                    meta_path = self._repository.resolve_artifact_path(f"{backup_id}.meta.json")
                    meta_path.unlink(missing_ok=True)
                    raise BackupValidationError(
                        f"Newly assembled backup artifact failed self-verification: {verify_res.error_message}"
                    )

                # Stage 7: INDEX & RETAIN
                # Apply retention policy
                pruned_ids = self._retention.evaluate_and_prune(
                    repository=self._repository,
                    policy=self._config.retention_policy,
                    active_backup_id=backup_id,
                )
                self._backups_pruned_count += len(pruned_ids)

                # Stage 8: REPORT & METRICS
                duration = time.monotonic() - start_time
                self._last_backup_duration_seconds = duration
                self._backups_created_count += 1
                self._last_successful_backup_at = sidecar_meta.finalized_at

                await self._event_publisher.emit_completed(
                    backup_id=backup_id,
                    scope=request.scope,
                    file_size_bytes=sidecar_meta.file_size_bytes,
                    is_encrypted=sidecar_meta.is_encrypted,
                )

                return CreateBackupResponse(
                    backup_id=backup_id,
                    state=BackupState.VALID,
                    created_at=sidecar_meta.created_at,
                    finalized_at=sidecar_meta.finalized_at,
                    filename=sidecar_meta.filename,
                    file_size_bytes=sidecar_meta.file_size_bytes,
                    sha256=sidecar_meta.sha256,
                    is_encrypted=sidecar_meta.is_encrypted,
                    key_id=sidecar_meta.key_id,
                )

            except Exception as exc:
                self._backups_failed_count += 1
                self._last_failed_backup_at = _utc_now_iso()
                self._last_error_message = str(exc)
                logger.error("Backup creation failed for '%s': %s", backup_id, exc)

                # Cleanup temporaries
                db_snap_path.unlink(missing_ok=True)
                tmp_unencrypted_zip.unlink(missing_ok=True)
                tmp_final_path.unlink(missing_ok=True)

                await self._event_publisher.emit_failed(
                    backup_id=backup_id,
                    scope=request.scope,
                    error_message=str(exc),
                )
                raise
            finally:
                self._active_operation = None

    async def verify_backup(self, request: VerifyBackupRequest) -> VerifyBackupResponse:
        """Verify an existing backup artifact."""
        key = self._crypto._key if self._crypto else None
        res = self._verifier.verify_artifact(
            request=request,
            repository=self._repository,
            encryption_key=key,
        )
        if res.is_valid:
            self._backups_verified_count += 1
        else:
            await self._event_publisher.emit_validation_failed(
                backup_id=request.backup_id,
                error_message=res.error_message or "Unknown verification error",
            )
        return res

    async def delete_backup(self, request: DeleteBackupRequest) -> DeleteBackupResponse:
        """Atomically delete an artifact and its sidecar metadata."""
        res = self._repository.delete_backup(request)
        await self._event_publisher.emit_deleted(request.backup_id)
        return res

    async def list_backups(self, request: ListBackupsRequest) -> ListBackupsResponse:
        """List all discovered backups."""
        return self._repository.list_backups(request)

    async def get_backup(self, request: GetBackupRequest) -> GetBackupResponse:
        """Retrieve metadata for a specific backup."""
        return self._repository.get_backup(request)

    def get_diagnostics(self) -> BackupDiagnostics:
        """Return technical self-diagnostics for the Backup Engine."""
        uptime = (time.monotonic() - self._started_at_monotonic) if self._started_at_monotonic else 0.0
        backups_res = self._repository.list_backups(ListBackupsRequest(limit=500))
        valid_count = sum(1 for b in backups_res.backups if b.state == BackupState.VALID)

        key_avail = self._crypto.is_key_available if self._crypto else False

        return BackupDiagnostics(
            engine_name=BACKUP_ENGINE_NAME,
            state=self.state.value,
            backup_root=str(self._repository.backup_directory),
            total_backups=backups_res.total_count,
            valid_backups=valid_count,
            last_successful_backup_at=self._last_successful_backup_at,
            last_failed_backup_at=self._last_failed_backup_at,
            last_error_message=self._last_error_message,
            cumulative_size_bytes=sum(b.file_size_bytes for b in backups_res.backups),
            encryption_enabled=True,
            key_available=key_avail,
            active_operation=self._active_operation,
            uptime_seconds=uptime,
        )

    # -- IEngineDiagnostics --------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        return self.health()

    def health(self) -> dict[str, Any]:
        return self._diagnostics_adapter.health()

    def metrics(self) -> dict[str, Any]:
        return self._diagnostics_adapter.metrics()

    def diagnostics(self) -> dict[str, Any]:
        return self._diagnostics_adapter.diagnostics()

    def status(self) -> str:
        return self._diagnostics_adapter.status()

    def version(self) -> str:
        return self._diagnostics_adapter.version()

    def capabilities(self) -> list[str]:
        return self._diagnostics_adapter.capabilities()

    # -- Capability Handlers -------------------------------------------------

    async def handle_backup_create(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        scope: str = "FULL_INSTANCE",
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Capability handler for kortex.backup.create."""
        try:
            req_scope = BackupScope(scope)
        except ValueError:
            raise BackupScopeError(f"Invalid backup scope: '{scope}'.") from None

        req = CreateBackupRequest(
            scope=req_scope,
            idempotency_key=idempotency_key,
            metadata=metadata or {},
        )
        res = await self.create_backup(req)
        return res.model_dump(mode="json")

    async def handle_backup_list(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Capability handler for kortex.backup.list."""
        req = ListBackupsRequest(limit=limit, offset=offset)
        res = await self.list_backups(req)
        return res.model_dump(mode="json")

    async def handle_backup_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        backup_id: str = "",
    ) -> dict[str, Any]:
        """Capability handler for kortex.backup.get."""
        req = GetBackupRequest(backup_id=backup_id)
        res = await self.get_backup(req)
        return res.model_dump(mode="json")

    async def handle_backup_verify(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        backup_id: str = "",
    ) -> dict[str, Any]:
        """Capability handler for kortex.backup.verify."""
        req = VerifyBackupRequest(backup_id=backup_id)
        res = await self.verify_backup(req)
        return res.model_dump(mode="json")

    async def handle_backup_delete(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        backup_id: str = "",
    ) -> dict[str, Any]:
        """Capability handler for kortex.backup.delete."""
        req = DeleteBackupRequest(backup_id=backup_id)
        res = await self.delete_backup(req)
        return res.model_dump(mode="json")

    async def handle_backup_diagnostics_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
    ) -> dict[str, Any]:
        """Capability handler for kortex.backup.diagnostics.get."""
        return self.diagnostics()
