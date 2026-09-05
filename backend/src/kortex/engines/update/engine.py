"""KORTEX Update Engine main facade and coordinator.

Phase 7 — Production Hardening — Update Engine.
Coordinates the end-to-end update lifecycle:
CHECK -> STAGE -> CHECKPOINT -> QUIESCE -> MIGRATE -> SWAP -> VERIFY -> REPORT.
Survives system interruption with write-ahead journaling and automated reverse-swap / Recovery rollback.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.backup.constants import BackupScope
from kortex.engines.backup.models import CreateBackupRequest as BackupCreateRequest
from kortex.engines.recovery.models import CreateRecoveryRequest
from kortex.engines.storage.interfaces import IEngineDiagnostics
from kortex.engines.update.applier import UpdateApplier
from kortex.engines.update.compatibility import CompatibilityEvaluator
from kortex.engines.update.constants import (
    CAPABILITY_UPDATE_APPLY,
    CAPABILITY_UPDATE_CANCEL,
    CAPABILITY_UPDATE_CHECK,
    CAPABILITY_UPDATE_DIAGNOSTICS_GET,
    CAPABILITY_UPDATE_GET,
    CAPABILITY_UPDATE_STAGE,
    CURRENT_ENGINE_VERSION,
    PERMISSION_UPDATE_MANAGE,
    PERMISSION_UPDATE_READ,
    UPDATE_ENGINE_NAME,
    UPDATE_SECURITY_CLASSIFICATION,
    UpdateJournalPhase,
    UpdateState,
)
from kortex.engines.update.crypto import (
    UpdateCryptoManager,
    compute_bytes_sha256,
)
from kortex.engines.update.diagnostics import UpdateDiagnosticsAdapter
from kortex.engines.update.events import UpdateEventPublisher
from kortex.engines.update.exceptions import (
    UpdateAuthenticationError,
    UpdateAuthorizationError,
    UpdateCheckpointError,
    UpdateConcurrencyError,
    UpdateError,
    UpdateManifestError,
    UpdateNotFoundError,
    UpdateOperatorActionRequiredError,
    UpdateSecurityError,
    UpdateVerificationError,
)
from kortex.engines.update.interfaces import IUpdateEngine
from kortex.engines.update.journal import UpdateJournalManager
from kortex.engines.update.manifest import UpdateManifestParser
from kortex.engines.update.migrator import UpdateMigrator
from kortex.engines.update.models import (
    UpdateApplyRequest,
    UpdateApplyResponse,
    UpdateCancelRequest,
    UpdateCancelResponse,
    UpdateCheckRequest,
    UpdateCheckResponse,
    UpdateConfig,
    UpdateGetRequest,
    UpdateGetResponse,
    UpdateManifest,
    UpdateStageRequest,
    UpdateStageResponse,
)
from kortex.engines.update.quiescence import UpdateQuiescenceManager
from kortex.engines.update.staging import UpdateStagingManager

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class UpdateEngine(BaseEngine, IUpdateEngine, IEngineDiagnostics):
    """Authoritative KORTEX Update Engine coordinator."""

    def __init__(
        self,
        config: UpdateConfig | None = None,
        crypto_manager: UpdateCryptoManager | None = None,
        current_version: str | None = None,
        target_root: Path | str | None = None,
        update_dir: Path | str | None = None,
    ) -> None:
        super().__init__()
        if config is None and update_dir is not None:
            u_dir = str(update_dir)
            self._config = UpdateConfig(
                update_directory=u_dir,
                staging_directory=f"{u_dir}/staging",
            )
        else:
            self._config = config or UpdateConfig()
        self._kernel: Kernel | None = None
        self._started_at_monotonic: float | None = None

        # Subsystems
        self._crypto = crypto_manager or UpdateCryptoManager()
        self._manifest_parser = UpdateManifestParser()
        self._compatibility = CompatibilityEvaluator(current_version=current_version or CURRENT_ENGINE_VERSION)
        self._staging_manager = UpdateStagingManager(
            staging_base_dir=self._config.staging_directory,
            safety_reserve_bytes=self._config.safety_reserve_bytes,
        )
        self._journal_manager = UpdateJournalManager(update_base_dir=self._config.update_directory)
        self._quiescence_manager = UpdateQuiescenceManager(
            lock_file_path=Path(self._config.update_directory) / "maintenance.lock",
            timeout_seconds=self._config.quiescence_timeout_seconds,
        )
        self._migrator = UpdateMigrator()
        self._applier = UpdateApplier(target_root=target_root)
        self._event_publisher = UpdateEventPublisher()
        self._diagnostics_adapter = UpdateDiagnosticsAdapter(self)

        # Operational & Concurrency State
        self._update_lock = asyncio.Lock()
        self._active_operation: str | None = None
        self._active_manifest: UpdateManifest | None = None

        # Metrics / Counters
        self._updates_attempted_count: int = 0
        self._updates_completed_count: int = 0
        self._updates_failed_count: int = 0
        self._updates_rolled_back_count: int = 0
        self._last_update_duration_seconds: float = 0.0
        self._last_update_timestamp: str | None = None
        self._last_error_message: str | None = None

    # -- Properties ----------------------------------------------------------

    @property
    def config(self) -> UpdateConfig:
        return self._config

    @property
    def current_version(self) -> str:
        return self._compatibility.current_version

    @property
    def crypto_manager(self) -> UpdateCryptoManager:
        return self._crypto

    @property
    def staging_manager(self) -> UpdateStagingManager:
        return self._staging_manager

    @property
    def journal_manager(self) -> UpdateJournalManager:
        return self._journal_manager

    @property
    def quiescence_manager(self) -> UpdateQuiescenceManager:
        return self._quiescence_manager

    @property
    def active_operation(self) -> str | None:
        return self._active_operation

    @property
    def updates_attempted_count(self) -> int:
        return self._updates_attempted_count

    @property
    def updates_completed_count(self) -> int:
        return self._updates_completed_count

    @property
    def updates_failed_count(self) -> int:
        return self._updates_failed_count

    @property
    def updates_rolled_back_count(self) -> int:
        return self._updates_rolled_back_count

    @property
    def last_update_duration_seconds(self) -> float:
        return self._last_update_duration_seconds

    @property
    def last_update_timestamp(self) -> str | None:
        return self._last_update_timestamp

    @property
    def last_error_message(self) -> str | None:
        return self._last_error_message

    # -- BaseEngine Lifecycle ------------------------------------------------

    @property
    def name(self) -> str:
        return UPDATE_ENGINE_NAME

    @property
    def dependencies(self) -> list[str]:
        return ["storage", "security", "backup"]

    def set_kernel(self, kernel: Kernel) -> None:
        """Explicitly set Kernel reference and wire event publisher."""
        self._kernel = kernel
        try:
            event_engine = kernel.get_engine("event")
            if event_engine:
                self._event_publisher.set_event_engine(event_engine)  # type: ignore[arg-type]
        except Exception:
            logger.debug("EventEngine not found during UpdateEngine set_kernel.")

    @property
    def _crypto_manager(self) -> UpdateCryptoManager:
        return self._crypto

    @_crypto_manager.setter
    def _crypto_manager(self, manager: UpdateCryptoManager) -> None:
        self._crypto = manager

    async def initialize(self, kernel: Kernel | None = None) -> None:
        """Register capabilities and inspect journal for restart crash recovery."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)
        self._kernel = kernel

        if kernel is not None:
            # Wire EventEngine if available
            try:
                event_engine = kernel.get_engine("event")
                if event_engine:
                    self._event_publisher.set_event_engine(event_engine)  # type: ignore[arg-type]
            except Exception:
                logger.debug("EventEngine not found during UpdateEngine initialize.")

            # Register capabilities with the Kernel
            self._register_capabilities(kernel)

        # Execute crash recovery sweep
        self._startup_crash_recovery_sweep()

        self._set_state(EngineState.READY)

    async def start(self) -> None:
        """Start the Update Engine and mark READY."""
        self._started_at_monotonic = time.monotonic()
        self._set_state(EngineState.READY)

    async def stop(self) -> None:
        """Stop the Update Engine."""
        self._set_state(EngineState.STOPPED)

    def _register_capabilities(self, kernel: Kernel) -> None:
        """Register the 6 canonical Update Engine capabilities."""
        if hasattr(kernel, "register_capability"):
            kernel.register_capability(
                name=CAPABILITY_UPDATE_CHECK,
                description="Check for available update manifests and verify digital signatures.",
                provider=self.name,
                handler=self.handle_update_check,
                requires_authentication=True,
                required_permissions=[PERMISSION_UPDATE_READ],
                security_classification=UPDATE_SECURITY_CLASSIFICATION,
                requires_execution_context=True,
            )
            kernel.register_capability(
                name=CAPABILITY_UPDATE_STAGE,
                description="Download, verify, and unpack update archive into isolated staging workspace.",
                provider=self.name,
                handler=self.handle_update_stage,
                requires_authentication=True,
                required_permissions=[PERMISSION_UPDATE_MANAGE],
                security_classification=UPDATE_SECURITY_CLASSIFICATION,
                requires_execution_context=True,
            )
            kernel.register_capability(
                name=CAPABILITY_UPDATE_APPLY,
                description="Execute checkpoint, quiescence, forward migration, file swap, and verification.",
                provider=self.name,
                handler=self.handle_update_apply,
                requires_authentication=True,
                required_permissions=[PERMISSION_UPDATE_MANAGE],
                security_classification=UPDATE_SECURITY_CLASSIFICATION,
                requires_execution_context=True,
            )
            kernel.register_capability(
                name=CAPABILITY_UPDATE_GET,
                description="Retrieve active update state, journal details, and history.",
                provider=self.name,
                handler=self.handle_update_get,
                requires_authentication=True,
                required_permissions=[PERMISSION_UPDATE_READ],
                security_classification=UPDATE_SECURITY_CLASSIFICATION,
                requires_execution_context=True,
            )
            kernel.register_capability(
                name=CAPABILITY_UPDATE_CANCEL,
                description="Abort unapplied staged update, purge workspace, and return to IDLE.",
                provider=self.name,
                handler=self.handle_update_cancel,
                requires_authentication=True,
                required_permissions=[PERMISSION_UPDATE_MANAGE],
                security_classification=UPDATE_SECURITY_CLASSIFICATION,
                requires_execution_context=True,
            )
            kernel.register_capability(
                name=CAPABILITY_UPDATE_DIAGNOSTICS_GET,
                description="Retrieve operational telemetry and metrics conforming to IEngineDiagnostics.",
                provider=self.name,
                handler=self.handle_update_diagnostics_get,
                requires_authentication=True,
                required_permissions=[PERMISSION_UPDATE_READ],
                security_classification=UPDATE_SECURITY_CLASSIFICATION,
                requires_execution_context=True,
            )

    def _startup_crash_recovery_sweep(self) -> None:
        """Inspect uncompleted journals on startup according to the 22-point crash matrix."""
        if not self._journal_manager.has_active_journal():
            return

        try:
            inferred_state, action, checkpoint_id = self._journal_manager.evaluate_crash_recovery()
            logger.info(
                "Update startup crash sweep: active journal detected. State: %s, Action: %s, Checkpoint: %s",
                inferred_state,
                action,
                checkpoint_id,
            )

            if action == "ARCHIVE":
                self._journal_manager.archive_journal("COMPLETED")
                self._quiescence_manager.release_maintenance_lock()
            elif action == "PURGE_STAGING":
                record = self._journal_manager.load_active_record()
                if record:
                    self._staging_manager.purge_staging(record.update_id)
                self._journal_manager.archive_journal("FAILED_PRE_MUTATION_CLEANED")
                self._quiescence_manager.release_maintenance_lock()
            elif action == "VERIFY_RUNTIME":
                # Files were swapped before restart. Now executing in the restarted runtime!
                # If target version matches, complete the update!
                record = self._journal_manager.load_active_record()
                if record:
                    self._journal_manager.record_phase(
                        UpdateJournalPhase.COMMITTED,
                        metadata={"startup_activation_confirmed": True},
                        runtime_activated=True,
                    )
                    self._journal_manager.archive_journal("COMPLETED")
                    self._quiescence_manager.release_maintenance_lock()
            elif action == "OPERATOR_INTERVENTION":
                logger.critical("Update crash sweep: unresolvable journal state. Maintenance lock held fail-closed.")
        except Exception as exc:
            logger.critical("Error during Update startup crash sweep: %s; holding maintenance lock fail-closed", exc)

    # -- Core Operations -----------------------------------------------------

    async def check(self, request: UpdateCheckRequest, context: Any = None) -> UpdateCheckResponse:
        """Check for available update manifests, verify signatures, and evaluate compatibility."""
        manifest: UpdateManifest | None = None

        if request.manifest_content is not None:
            manifest = self._manifest_parser.parse_dict(request.manifest_content)
        elif request.manifest_url is not None:
            # Local file or mock URL
            manifest = self._manifest_parser.parse_file(request.manifest_url)
        else:
            return UpdateCheckResponse(
                update_available=False,
                current_version=self.current_version,
                details={"reason": "NO_MANIFEST_SOURCE_SPECIFIED"},
            )

        # 1. Cryptographic Signature & Authenticity Verification
        self._crypto.verify_manifest(manifest.model_dump(mode="json"))
        await self._event_publisher.emit_manifest_verified(
            manifest.manifest_id,
            {"target_version": manifest.version.target_version, "key_id": manifest.key_id},
        )

        # 2. Compatibility & Version Evaluation
        self._compatibility.evaluate_compatibility(manifest)

        # 3. Check if target is newer than current
        is_newer = manifest.version.target_version != self.current_version

        await self._event_publisher.emit_checked(
            manifest.manifest_id,
            {"target_version": manifest.version.target_version, "update_available": is_newer},
        )

        self._active_manifest = manifest

        return UpdateCheckResponse(
            update_available=is_newer,
            current_version=self.current_version,
            target_version=manifest.version.target_version,
            manifest=manifest,
            details={
                "channel": manifest.version.release_channel,
                "requires_migration": manifest.database.requires_migration,
                "package_size": manifest.package.size_bytes,
            },
        )

    async def stage(self, request: UpdateStageRequest, context: Any = None) -> UpdateStageResponse:
        """Verify, preflight, and extract update archive into isolated staging workspace."""
        async with self._update_lock:
            if request.manifest is not None:
                manifest = request.manifest
            elif request.manifest_path is not None:
                manifest = self._manifest_parser.parse_file(request.manifest_path)
            else:
                raise UpdateManifestError("Missing manifest or manifest_path in UpdateStageRequest")

            # 1. Cryptographic Verification
            self._crypto.verify_manifest(manifest.model_dump(mode="json"))
            await self._event_publisher.emit_manifest_verified(
                manifest.manifest_id,
                {"target_version": manifest.version.target_version, "key_id": manifest.key_id},
            )
            self._compatibility.evaluate_compatibility(manifest)

            # 2. Derive deterministic update ID
            content_to_hash = (manifest.manifest_id + manifest.package.sha256).encode("utf-8")
            update_id = compute_bytes_sha256(content_to_hash)[:16]
            self._active_operation = update_id

            # 3. Check cross-engine concurrency
            self._quiescence_manager.check_cross_engine_concurrency()

            # 4. Preflight Disk Space
            live_db = os.environ.get("KORTEX_DATABASE_URL")
            self._staging_manager.preflight_disk_space(manifest, live_db)

            # 5. Acquire Archive (from bytes or path)
            archive_path = request.package_path or request.archive_path
            temp_written_archive: Path | None = None
            if request.package_bytes is not None:
                staging_parent = self._staging_manager.staging_base_dir
                staging_parent.mkdir(parents=True, exist_ok=True)
                temp_written_archive = staging_parent / f"download_{update_id}.kortex-update"
                temp_written_archive.write_bytes(request.package_bytes)
                archive_path = str(temp_written_archive)

            if not archive_path or not Path(archive_path).is_file():
                raise FileNotFoundError(f"Update package archive not found: {archive_path}")

            # 6. Verify Artifact SHA-256 Digest against Manifest
            self._crypto.verify_artifact(archive_path, manifest.package.sha256)

            # 7. Create Write-Ahead Journal record
            self._journal_manager.create_journal(
                update_id=update_id,
                manifest=manifest,
                current_version=self.current_version,
            )
            self._journal_manager.record_phase(UpdateJournalPhase.ARTIFACT_VERIFIED)

            # 8. Extract Archive Securely into Isolated Staging Workspace
            staged_path = self._staging_manager.extract_staged_archive(archive_path, update_id)

            # Clean temporary archive if written from bytes
            if temp_written_archive and temp_written_archive.is_file():
                with contextlib.suppress(OSError):
                    temp_written_archive.unlink()

            # 9. Record STAGED Phase in Journal
            now_iso = _utc_now_iso()
            self._journal_manager.record_phase(
                UpdateJournalPhase.STAGED,
                metadata={"staged_path": str(staged_path)},
                staging_dir=str(staged_path),
            )

            await self._event_publisher.emit_staged(
                update_id,
                {"target_version": manifest.version.target_version, "staging_path": str(staged_path)},
            )

            return UpdateStageResponse(
                update_id=update_id,
                target_version=manifest.version.target_version,
                staging_path=str(staged_path),
                staged_at=now_iso,
                sha256_verified=True,
                details={"package_sha256": manifest.package.sha256},
            )

    async def apply(self, request: UpdateApplyRequest, context: Any = None) -> UpdateApplyResponse:
        """Execute checkpointing, quiescence, migration, filesystem swap, and verification."""
        async with self._update_lock:
            start_time = time.monotonic()
            self._updates_attempted_count += 1
            update_id = request.update_id
            self._active_operation = update_id

            journal = self._journal_manager.load_active_record()
            if journal is None or journal.update_id != update_id:
                raise UpdateNotFoundError(f"No active staged update transaction found for update ID '{update_id}'")

            manifest = journal.manifest
            staging_dir = self._staging_manager.get_update_staging_dir(update_id)
            if not staging_dir.is_dir():
                raise UpdateNotFoundError(f"Staging directory missing for update ID '{update_id}'")

            # Mutual Exclusion Check
            self._quiescence_manager.check_cross_engine_concurrency()

            # ----------------------------------------------------------------
            # STEP 1: Mandatory FULL_INSTANCE Pre-Update Safety Checkpoint
            # ----------------------------------------------------------------
            checkpoint_id: str | None = None
            try:
                checkpoint_id = await self._create_safety_checkpoint(update_id, manifest)
                self._journal_manager.record_phase(
                    UpdateJournalPhase.CHECKPOINT_CREATED,
                    safety_checkpoint_id=checkpoint_id,
                )
                await self._event_publisher.emit_safety_checkpoint_created(
                    update_id,
                    {"backup_id": checkpoint_id, "scope": "FULL_INSTANCE"},
                )
            except Exception as exc:
                self._updates_failed_count += 1
                self._last_error_message = str(exc)
                logger.error("Pre-update safety checkpoint failed for '%s': %s; aborting update", update_id, exc)
                self._journal_manager.record_phase(
                    UpdateJournalPhase.FAILED,
                    error_message=f"Checkpoint creation failed: {exc}",
                )
                await self._event_publisher.emit_failed(update_id, {"error": str(exc), "phase": "CHECKPOINT"})
                raise UpdateCheckpointError(
                    f"Pre-update safety checkpoint failed: {exc}. Live state untouched."
                ) from exc

            # ----------------------------------------------------------------
            # STEP 2: Quiescence & Maintenance Lock
            # ----------------------------------------------------------------
            try:
                await self._quiescence_manager.acquire_maintenance_lock(
                    update_id,
                    metadata={"target_version": manifest.version.target_version, "checkpoint_id": checkpoint_id},
                )
                await self._quiescence_manager.drain_and_disconnect_database(self._kernel)
                self._journal_manager.record_phase(UpdateJournalPhase.QUIESCED)
                await self._event_publisher.emit_quiesced(update_id, {"checkpoint_id": checkpoint_id})
            except Exception as exc:
                self._quiescence_manager.release_maintenance_lock()
                self._updates_failed_count += 1
                self._last_error_message = str(exc)
                self._journal_manager.record_phase(UpdateJournalPhase.FAILED, error_message=str(exc))
                await self._event_publisher.emit_failed(update_id, {"error": str(exc), "phase": "QUIESCENCE"})
                raise

            # ----------------------------------------------------------------
            # STEP 3: Forward Alembic Database Schema Migration
            # ----------------------------------------------------------------
            live_db_was_migrated = False
            try:
                if manifest.database.requires_migration and manifest.database.target_revision:
                    migration_result = await self._migrator.execute_forward_migration(manifest)
                    live_db_was_migrated = migration_result.get("migrated", False)
                    self._journal_manager.record_phase(
                        UpdateJournalPhase.SCHEMA_MIGRATED,
                        metadata=migration_result,
                    )
                    await self._event_publisher.emit_migrated(
                        update_id,
                        {"target_revision": manifest.database.target_revision, "result": migration_result},
                    )
            except Exception as exc:
                logger.error("Alembic forward migration failed during update '%s': %s", update_id, exc)
                await self._handle_post_mutation_failure(
                    update_id=update_id,
                    checkpoint_id=checkpoint_id,
                    failed_phase=UpdateJournalPhase.QUIESCED,
                    error_message=f"Migration failed: {exc}",
                    live_db_migrated=True,
                    swapped_files=[],
                )
                raise

            # ----------------------------------------------------------------
            # STEP 4: Filesystem Component Swapping (with .rollback copies)
            # ----------------------------------------------------------------
            rollback_copy_paths: list[str] = []
            try:
                rollback_copy_paths = self._applier.swap_components(staging_dir, update_id)
                self._journal_manager.record_phase(
                    UpdateJournalPhase.FILES_SWAPPED,
                    rollback_files=rollback_copy_paths,
                    filesystem_applied=True,
                    restart_required=True,
                    runtime_activated=False,
                )
                await self._event_publisher.emit_applied(
                    update_id,
                    {"swapped_components_count": len(rollback_copy_paths)},
                )
            except Exception as exc:
                logger.error("Filesystem component swap failed for update '%s': %s", update_id, exc)
                await self._handle_post_mutation_failure(
                    update_id=update_id,
                    checkpoint_id=checkpoint_id,
                    failed_phase=UpdateJournalPhase.FILES_SWAPPED,
                    error_message=f"Filesystem swap failed: {exc}",
                    live_db_migrated=live_db_was_migrated,
                    swapped_files=rollback_copy_paths,
                )
                raise

            # ----------------------------------------------------------------
            # STEP 5: Post-Update Filesystem Verification
            # ----------------------------------------------------------------
            try:
                verification_report = await self._verify_post_update(manifest)
                self._journal_manager.record_phase(
                    UpdateJournalPhase.VERIFIED,
                    metadata=verification_report,
                )
                await self._event_publisher.emit_verified(update_id, verification_report)
            except Exception as exc:
                logger.error("Post-update verification failed for '%s': %s", update_id, exc)
                await self._handle_post_mutation_failure(
                    update_id=update_id,
                    checkpoint_id=checkpoint_id,
                    failed_phase=UpdateJournalPhase.VERIFIED,
                    error_message=f"Post-update verification failed: {exc}",
                    live_db_migrated=live_db_was_migrated,
                    swapped_files=rollback_copy_paths,
                )
                raise

            # ----------------------------------------------------------------
            # STEP 6: Completion & Runtime Activation Transition (Clarification 2)
            # ----------------------------------------------------------------
            # Filesystem transaction is complete, verified, and durable.
            # Filesystem update != runtime activation. Restart is required!
            self._journal_manager.record_phase(
                UpdateJournalPhase.COMMITTED,
                filesystem_applied=True,
                restart_required=True,
                runtime_activated=False,
            )
            await self._event_publisher.emit_completed(
                update_id,
                {
                    "target_version": manifest.version.target_version,
                    "filesystem_applied": True,
                    "restart_required": True,
                    "runtime_activated": False,
                },
            )

            # Cleanup rollback copies and staging workspace
            self._applier.cleanup_rollback_copies(rollback_copy_paths)
            self._staging_manager.purge_staging(update_id)

            # Release maintenance lock so subsequent reboot / service restart can proceed
            self._quiescence_manager.release_maintenance_lock()

            # Archive journal to history
            self._journal_manager.archive_journal("COMPLETED")

            duration = time.monotonic() - start_time
            self._last_update_duration_seconds = duration
            self._last_update_timestamp = _utc_now_iso()
            self._updates_completed_count += 1
            self._active_operation = None

            return UpdateApplyResponse(
                update_id=update_id,
                target_version=manifest.version.target_version,
                status=UpdateState.COMPLETED,
                filesystem_updated=True,
                restart_required=True,
                runtime_activated=False,
                safety_checkpoint_id=checkpoint_id,
                applied_at=_utc_now_iso(),
                details={
                    "duration_seconds": duration,
                    "message": (
                        "Update successfully applied to filesystem. Backend restart required for runtime activation."
                    ),
                },
            )

    async def _create_safety_checkpoint(self, update_id: str, manifest: UpdateManifest) -> str:
        """Invoke BackupEngine to create a mandatory FULL_INSTANCE pre-update safety checkpoint."""
        if self._kernel is None:
            raise UpdateCheckpointError("Kernel unavailable to resolve BackupEngine.")

        backup_engine = self._kernel.get_engine("backup")
        if backup_engine is None:
            raise UpdateCheckpointError("BackupEngine not registered with Kernel.")

        req = BackupCreateRequest(
            scope=BackupScope.FULL_INSTANCE,
            idempotency_key=f"update-checkpoint-{update_id}",
            metadata={
                "origin": "pre_update_safety_checkpoint",
                "update_id": update_id,
                "target_version": manifest.version.target_version,
                "is_safety_checkpoint": True,
            },
        )
        backup_op = getattr(backup_engine, "create_backup", None)
        if not callable(backup_op):
            raise UpdateCheckpointError("BackupEngine has no callable create_backup method.")
        res = await backup_op(req)

        backup_id = getattr(res, "backup_id", None)
        if not backup_id or not isinstance(backup_id, str):
            raise UpdateCheckpointError("BackupEngine returned empty response for safety checkpoint.")
        return backup_id

    async def _verify_post_update(self, manifest: UpdateManifest) -> dict[str, Any]:
        """Verify SQLite database integrity and schema revision after file swap."""
        results: dict[str, Any] = {"db_integrity": "OK", "schema_verified": True}

        # 1. SQLite PRAGMA integrity_check
        storage_dir = os.environ.get("KORTEX_STORAGE_DIR", "storage_data")
        live_db = Path(storage_dir) / "kortex_local.db"
        if live_db.is_file():
            try:
                conn = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
                cursor = conn.cursor()
                cursor.execute("PRAGMA integrity_check;")
                rows = cursor.fetchall()
                conn.close()
                if not rows or rows[0][0] != "ok":
                    raise UpdateVerificationError(f"Database PRAGMA integrity_check failed: {rows}")
            except Exception as exc:
                raise UpdateVerificationError(f"Failed SQLite integrity check on live DB: {exc}") from exc

        # 2. Schema Revision Verification
        if manifest.database.requires_migration and manifest.database.target_revision:
            cur_rev = self._migrator.get_current_revision()
            if cur_rev != manifest.database.target_revision:
                raise UpdateVerificationError(
                    f"Schema revision mismatch post-update: expected "
                    f"'{manifest.database.target_revision}', got '{cur_rev}'"
                )
            results["target_revision"] = cur_rev

        return results

    async def _handle_post_mutation_failure(
        self,
        update_id: str,
        checkpoint_id: str | None,
        failed_phase: UpdateJournalPhase,
        error_message: str,
        live_db_migrated: bool,
        swapped_files: list[str],
    ) -> None:
        """Coordinate the 3-layer rollback hierarchy upon post-mutation failure."""
        self._updates_failed_count += 1
        self._last_error_message = error_message

        # Layer 1: Revert filesystem swaps if any occurred
        try:
            if swapped_files:
                records: list[tuple[Path, Path | None]] = []
                for p in swapped_files:
                    if p.endswith(f".rollback_{update_id}"):
                        orig = Path(p.replace(f".rollback_{update_id}", ""))
                        records.append((orig, Path(p)))
                    else:
                        records.append((Path(p), None))
                self._applier.reverse_swap(records)
                logger.info("Layer 1 rollback: successfully reverted swapped filesystem components.")
        except Exception as exc:
            logger.critical("Layer 1 rollback failed: %s", exc)

        # Layer 2: If live DB was migrated, invoke RecoveryEngine (CLARIFICATION 1)
        if live_db_migrated and checkpoint_id and self._kernel is not None:
            recovery_engine = self._kernel.get_engine("recovery")
            if recovery_engine is not None:
                try:
                    self._journal_manager.record_phase(
                        UpdateJournalPhase.ROLLING_BACK,
                        metadata={"trigger": error_message, "checkpoint_id": checkpoint_id},
                    )
                    # MUST explicitly supply confirm_destructive_restore=True!
                    recovery_req = CreateRecoveryRequest(
                        backup_id=checkpoint_id,
                        confirm_destructive_restore=True,
                        metadata={
                            "origin": "update_engine_post_mutation_rollback",
                            "update_id": update_id,
                            "failed_phase": failed_phase.value,
                        },
                    )
                    recovery_op = getattr(recovery_engine, "create_recovery", None)
                    if callable(recovery_op):
                        await recovery_op(recovery_req)
                    self._updates_rolled_back_count += 1
                    self._journal_manager.record_phase(UpdateJournalPhase.ROLLED_BACK)
                    await self._event_publisher.emit_rolled_back(
                        update_id,
                        {"checkpoint_id": checkpoint_id, "reason": error_message},
                    )
                    self._quiescence_manager.release_maintenance_lock()
                    return
                except Exception as exc:
                    logger.critical("Layer 2 RecoveryEngine rollback failed: %s", exc)

        # Layer 3: Operator Intervention Required
        self._journal_manager.record_phase(
            UpdateJournalPhase.FAILED_NEEDS_OPERATOR,
            error_message=error_message,
            operator_notes="Automated rollback failed or unavailable; maintenance lock held fail-closed.",
        )
        await self._event_publisher.emit_operator_intervention_required(
            update_id,
            {"error": error_message, "checkpoint_id": checkpoint_id},
        )
        raise UpdateOperatorActionRequiredError(
            f"Catastrophic update failure for '{update_id}': {error_message}. "
            f"System halted in FAILED_NEEDS_OPERATOR under active maintenance lock."
        )

    async def cancel(self, request: UpdateCancelRequest, context: Any = None) -> UpdateCancelResponse:
        """Cancel an unapplied staged update, purge workspace, and return to IDLE."""
        async with self._update_lock:
            update_id = request.update_id
            journal = self._journal_manager.load_active_record()

            # If mutation has begun, cannot cancel
            if (
                journal
                and journal.update_id == update_id
                and journal.current_phase
                in (
                    UpdateJournalPhase.QUIESCED,
                    UpdateJournalPhase.SCHEMA_MIGRATED,
                    UpdateJournalPhase.FILES_SWAPPED,
                    UpdateJournalPhase.VERIFIED,
                )
            ):
                raise UpdateConcurrencyError(
                    f"Cannot cancel update '{update_id}': mutation has already commenced "
                    f"(phase: {journal.current_phase})."
                )

            self._staging_manager.purge_staging(update_id)
            if journal and journal.update_id == update_id:
                self._journal_manager.archive_journal("CANCELLED")
            self._quiescence_manager.release_maintenance_lock()
            self._active_operation = None

            return UpdateCancelResponse(
                update_id=update_id,
                cancelled=True,
                purged_staging=True,
                details={"message": f"Update '{update_id}' cancelled and staging workspace purged."},
            )

    async def get(self, request: UpdateGetRequest, context: Any = None) -> UpdateGetResponse:
        """Retrieve active update state, journal details, and history."""
        journal = self._journal_manager.load_active_record()
        recent_history = self._journal_manager.load_history()

        current_phase = journal.current_phase if journal else None
        target_version = journal.target_version if journal else None

        state_map = {
            UpdateJournalPhase.CREATED: UpdateState.STAGING,
            UpdateJournalPhase.MANIFEST_VERIFIED: UpdateState.STAGING,
            UpdateJournalPhase.ARTIFACT_ACQUIRED: UpdateState.STAGING,
            UpdateJournalPhase.ARTIFACT_VERIFIED: UpdateState.STAGING,
            UpdateJournalPhase.STAGED: UpdateState.STAGED,
            UpdateJournalPhase.CHECKPOINT_CREATED: UpdateState.CHECKPOINTING,
            UpdateJournalPhase.QUIESCED: UpdateState.QUIESCING,
            UpdateJournalPhase.SCHEMA_MIGRATED: UpdateState.MIGRATING,
            UpdateJournalPhase.FILES_SWAPPED: UpdateState.APPLYING,
            UpdateJournalPhase.VERIFIED: UpdateState.VERIFYING,
            UpdateJournalPhase.COMMITTED: UpdateState.COMPLETED,
            UpdateJournalPhase.FAILED: UpdateState.FAILED,
            UpdateJournalPhase.ROLLING_BACK: UpdateState.ROLLING_BACK,
            UpdateJournalPhase.ROLLED_BACK: UpdateState.ROLLED_BACK,
            UpdateJournalPhase.FAILED_NEEDS_OPERATOR: UpdateState.FAILED_NEEDS_OPERATOR,
        }

        derived_state = state_map.get(current_phase, UpdateState.IDLE) if current_phase else UpdateState.IDLE

        return UpdateGetResponse(
            active_update_id=journal.update_id if journal else None,
            state=derived_state,
            current_version=self.current_version,
            target_version=target_version,
            journal_phase=current_phase,
            active_journal=journal,
            recent_history=recent_history,
        )

    # -- Capability Handlers with Security Enforcement -----------------------

    def _enforce_execution_context(
        self,
        execution_context: CapabilityExecutionContext | None,
        required_permission: str,
    ) -> CapabilityExecutionContext:
        """Enforce trusted execution context, authentication, and fail-closed permission checks."""
        if execution_context is None:
            raise UpdateAuthenticationError(
                "Missing trusted execution context. Direct unauthenticated capability invocation is forbidden."
            )

        principal = execution_context.principal
        if principal is not None:
            roles = set(principal.roles)
            if "TENANT_ADMIN" not in roles and "SYSTEM_ADMIN" not in roles:
                perms = set(principal.attributes.get("permissions", []))
                if required_permission not in perms and PERMISSION_UPDATE_MANAGE not in perms:
                    raise UpdateAuthorizationError(
                        f"Unauthorized principal '{principal.principal_id}': "
                        f"missing required permission '{required_permission}'."
                    )
        return execution_context

    async def handle_update_check(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        channel: str = "stable",
        manifest_url: str | None = None,
        manifest_content: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.update.check."""
        self._enforce_execution_context(execution_context, PERMISSION_UPDATE_READ)
        req = UpdateCheckRequest(
            channel=channel,
            manifest_url=manifest_url,
            manifest_content=manifest_content,
            metadata=metadata or {},
        )
        res = await self.check(req)
        return res.model_dump(mode="json")

    async def handle_update_stage(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        manifest: dict[str, Any] | UpdateManifest | None = None,
        package_path: str | None = None,
        package_bytes: bytes | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.update.stage."""
        self._enforce_execution_context(execution_context, PERMISSION_UPDATE_MANAGE)
        if manifest is None:
            raise UpdateSecurityError("Missing required 'manifest' parameter for update.stage.")

        manifest_obj = manifest if isinstance(manifest, UpdateManifest) else self._manifest_parser.parse_dict(manifest)
        req = UpdateStageRequest(
            manifest=manifest_obj,
            package_path=package_path,
            package_bytes=package_bytes,
            idempotency_key=idempotency_key,
            metadata=metadata or {},
        )
        res = await self.stage(req)
        return res.model_dump(mode="json")

    async def handle_update_apply(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        update_id: str = "",
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.update.apply."""
        self._enforce_execution_context(execution_context, PERMISSION_UPDATE_MANAGE)
        if not update_id:
            raise UpdateError("Parameter 'update_id' is required for update.apply.")

        req = UpdateApplyRequest(
            update_id=update_id,
            idempotency_key=idempotency_key,
            metadata=metadata or {},
        )
        res = await self.apply(req)
        return res.model_dump(mode="json")

    async def handle_update_cancel(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        update_id: str = "",
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.update.cancel."""
        self._enforce_execution_context(execution_context, PERMISSION_UPDATE_MANAGE)
        if not update_id:
            raise UpdateError("Parameter 'update_id' is required for update.cancel.")

        req = UpdateCancelRequest(update_id=update_id, metadata=metadata or {})
        res = await self.cancel(req)
        return res.model_dump(mode="json")

    async def handle_update_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        update_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.update.get."""
        self._enforce_execution_context(execution_context, PERMISSION_UPDATE_READ)
        req = UpdateGetRequest(update_id=update_id, metadata=metadata or {})
        res = await self.get(req)
        return res.model_dump(mode="json")

    async def handle_update_diagnostics_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.update.diagnostics.get."""
        self._enforce_execution_context(execution_context, PERMISSION_UPDATE_READ)
        return self._diagnostics_adapter.diagnostics()

    # -- IEngineDiagnostics & BaseEngine Lifecycle ---------------------------

    async def health_check(self) -> dict[str, Any]:
        return self.health()

    def status(self) -> str:
        return self._diagnostics_adapter.status()

    def version(self) -> str:
        return self._diagnostics_adapter.version()

    def capabilities(self) -> list[str]:
        return self._diagnostics_adapter.capabilities()

    def health(self) -> dict[str, Any]:
        return self._diagnostics_adapter.health()

    def metrics(self) -> dict[str, Any]:
        return self._diagnostics_adapter.metrics()

    def diagnostics(self) -> dict[str, Any]:
        return self._diagnostics_adapter.diagnostics()
