"""KORTEX Recovery Engine main facade and coordinator.

Phase 7 — Production Hardening — Recovery Engine.
Coordinates the end-to-end recovery lifecycle:
DISCOVER -> PRECHECK -> CHECKPOINT -> VALIDATE -> STAGE -> QUIESCE -> SWAP -> RECONNECT -> VERIFY -> REPORT.
Survives system interruption with write-ahead journaling and automated reverse-swap rollback.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.backup.constants import (
    BACKUP_EXTENSION,
    BackupScope,
)
from kortex.engines.backup.models import CreateBackupRequest as BackupCreateRequest
from kortex.engines.recovery.constants import (
    CAPABILITY_RECOVERY_CREATE,
    CAPABILITY_RECOVERY_DELETE,
    CAPABILITY_RECOVERY_DIAGNOSTICS_GET,
    CAPABILITY_RECOVERY_GET,
    CAPABILITY_RECOVERY_LIST,
    CAPABILITY_RECOVERY_VERIFY,
    CURRENT_ENGINE_VERSION,
    PERMISSION_RECOVERY_MANAGE,
    PERMISSION_RECOVERY_READ,
    RECOVERY_ENGINE_NAME,
    RECOVERY_SECURITY_CLASSIFICATION,
    RecoveryJournalPhase,
    RecoveryState,
)
from kortex.engines.recovery.crypto import RecoveryCryptoManager
from kortex.engines.recovery.database_restorer import DatabaseRestorer
from kortex.engines.recovery.diagnostics import RecoveryDiagnosticsAdapter
from kortex.engines.recovery.events import RecoveryEventPublisher
from kortex.engines.recovery.exceptions import (
    PreRecoveryCheckpointError,
    RecoveryAuthenticationError,
    RecoveryAuthorizationError,
    RecoveryConcurrencyError,
    RecoveryNotFoundError,
    RecoveryOperatorActionRequiredError,
    RecoveryRollbackError,
    RecoverySecurityError,
    RecoveryValidationError,
    RecoveryVerificationError,
)
from kortex.engines.recovery.interfaces import IRecoveryEngine
from kortex.engines.recovery.journal import RecoveryJournalManager
from kortex.engines.recovery.models import (
    ChecksumsMetadata,
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
    RollbackState,
    StagedStateLocations,
    TargetIdentity,
    VerificationState,
    VerifyRecoveryRequest,
    VerifyRecoveryResponse,
)
from kortex.engines.recovery.quiescence import RecoveryQuiescenceManager
from kortex.engines.recovery.staging import RecoveryStagingManager
from kortex.engines.recovery.storage_restorer import StorageRestorer
from kortex.engines.recovery.validator import RecoveryValidator
from kortex.engines.storage.interfaces import IEngineDiagnostics

if TYPE_CHECKING:
    from kortex.core.dispatch import CapabilityExecutionContext
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.recovery")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class RecoveryEngine(BaseEngine, IRecoveryEngine, IEngineDiagnostics):
    """Authoritative KORTEX Recovery Engine."""

    def __init__(
        self,
        config: RecoveryConfig | None = None,
        crypto_manager: RecoveryCryptoManager | None = None,
    ) -> None:
        super().__init__()
        self._config = config or RecoveryConfig()
        self._kernel: Kernel | None = None
        self._started_at_monotonic: float | None = None

        # Repositories & Subsystems
        self._staging_manager = RecoveryStagingManager(
            staging_base_dir=self._config.staging_directory,
            max_file_count=self._config.max_file_count,
            max_archive_size_bytes=self._config.max_archive_size_bytes,
            safety_margin_bytes=self._config.safety_margin_bytes,
        )
        self._journal_manager = RecoveryJournalManager(
            journal_file_path=Path(self._config.journal_directory) / "journal.json"
        )
        self._quiescence_manager = RecoveryQuiescenceManager(
            lock_file_path=Path(self._config.journal_directory) / "maintenance.lock",
            timeout_seconds=self._config.quiescence_timeout_seconds,
        )
        self._db_restorer = DatabaseRestorer()
        self._storage_restorer = StorageRestorer(storage_root=os.environ.get("KORTEX_STORAGE_DIR", "storage_data"))

        # Cryptographic subsystem
        self._crypto = crypto_manager or RecoveryCryptoManager()

        # Multi-tier validator
        self._validator: RecoveryValidator | None = None  # Instantiated in initialize() once storage root is confirmed

        # Event publisher & Diagnostics adapter
        self._event_publisher = RecoveryEventPublisher()
        self._diagnostics_adapter = RecoveryDiagnosticsAdapter(self)

        # Concurrency & Operational State
        self._recovery_lock = asyncio.Lock()
        self._active_operation: str | None = None

        # Telemetry & Metrics
        self._recoveries_attempted_count = 0
        self._recoveries_completed_count = 0
        self._recoveries_failed_count = 0
        self._recoveries_rolled_back_count = 0
        self._last_recovery_duration_seconds = 0.0
        self._last_recovery_timestamp: str | None = None
        self._last_error_message: str | None = None

    @property
    def name(self) -> str:
        return RECOVERY_ENGINE_NAME

    @property
    def dependencies(self) -> list[str]:
        return ["storage", "security", "backup"]

    @property
    def config(self) -> RecoveryConfig:
        return self._config

    @property
    def crypto_manager(self) -> RecoveryCryptoManager:
        return self._crypto

    @property
    def active_operation(self) -> str | None:
        return self._active_operation

    @property
    def recoveries_attempted_count(self) -> int:
        return self._recoveries_attempted_count

    @property
    def recoveries_completed_count(self) -> int:
        return self._recoveries_completed_count

    @property
    def recoveries_failed_count(self) -> int:
        return self._recoveries_failed_count

    @property
    def recoveries_rolled_back_count(self) -> int:
        return self._recoveries_rolled_back_count

    @property
    def last_recovery_duration_seconds(self) -> float:
        return self._last_recovery_duration_seconds

    @property
    def last_recovery_timestamp(self) -> str | None:
        return self._last_recovery_timestamp

    @property
    def last_error_message(self) -> str | None:
        return self._last_error_message

    def _resolve_storage_root(self) -> Path:
        """Resolve active live storage root directory."""
        if self._kernel is not None:
            storage_engine = getattr(self._kernel, "get_engine", lambda _: None)("storage")
            if storage_engine is not None and hasattr(storage_engine, "base_directory"):
                return Path(storage_engine.base_directory).resolve()

        env_dir = os.environ.get("KORTEX_STORAGE_DIR", "storage_data")
        return Path(env_dir).resolve()

    def _resolve_live_db_path(self) -> Path:
        """Resolve active live database SQLite file path."""
        if self._kernel is not None and hasattr(self._kernel, "db"):
            url = getattr(self._kernel.db, "_url", "")
            if "sqlite" in url:
                path_part = url.split("sqlite+aiosqlite:///")[-1].split("sqlite:///")[-1]
                return Path(path_part).resolve()

        from kortex.core.db import _default_sqlite_url

        url = os.environ.get("KORTEX_DATABASE_URL") or _default_sqlite_url()
        path_part = url.split("sqlite+aiosqlite:///")[-1].split("sqlite:///")[-1]
        return Path(path_part).resolve()

    # -- Lifecycle -----------------------------------------------------------

    async def initialize(self, kernel: Kernel | None = None) -> None:
        """Initialize Recovery Engine, resolve roots, and register capabilities."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)

        try:
            self._kernel = kernel
            storage_root = self._resolve_storage_root()
            self._storage_restorer = StorageRestorer(storage_root)

            self._validator = RecoveryValidator(
                backup_directory=self._config.backup_directory,
                storage_root=storage_root,
                staging_manager=self._staging_manager,
                database_restorer=self._db_restorer,
                crypto_manager=self._crypto,
            )

            if kernel is not None:
                self._event_publisher.set_kernel(kernel)

                # IoC container registration
                container = getattr(kernel, "container", None)
                if (
                    container is not None
                    and hasattr(container, "register_instance")
                    and hasattr(container, "has")
                    and not container.has("engine.recovery")
                ):
                    container.register_instance("engine.recovery", self)

                # Register the 6 approved capabilities
                if hasattr(kernel, "register_capability"):
                    kernel.register_capability(
                        name=CAPABILITY_RECOVERY_CREATE,
                        description="Initiate and complete a staged, journaled live recovery from an accepted backup.",
                        provider=self.name,
                        handler=self.handle_recovery_create,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_RECOVERY_MANAGE],
                        security_classification=RECOVERY_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_RECOVERY_LIST,
                        description="List historical recovery operations and active status.",
                        provider=self.name,
                        handler=self.handle_recovery_list,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_RECOVERY_READ],
                        security_classification=RECOVERY_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_RECOVERY_GET,
                        description="Retrieve status, phase, and journal details for a recovery operation.",
                        provider=self.name,
                        handler=self.handle_recovery_get,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_RECOVERY_READ],
                        security_classification=RECOVERY_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_RECOVERY_VERIFY,
                        description="Verify backup artifact validity, schema compatibility, and disk capacity.",
                        provider=self.name,
                        handler=self.handle_recovery_verify,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_RECOVERY_MANAGE],
                        security_classification=RECOVERY_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_RECOVERY_DELETE,
                        description="Cancel in-flight pre-swap recovery or clean completed journal and rollback files.",
                        provider=self.name,
                        handler=self.handle_recovery_delete,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_RECOVERY_MANAGE],
                        security_classification=RECOVERY_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )
                    kernel.register_capability(
                        name=CAPABILITY_RECOVERY_DIAGNOSTICS_GET,
                        description="Retrieve operational telemetry and metrics adhering to IEngineDiagnostics.",
                        provider=self.name,
                        handler=self.handle_recovery_diagnostics_get,
                        requires_authentication=True,
                        required_permissions=[PERMISSION_RECOVERY_READ],
                        security_classification=RECOVERY_SECURITY_CLASSIFICATION,
                        requires_execution_context=True,
                    )

            # Execute boot-time startup sweep for interrupted recoveries
            await self._startup_crash_recovery_sweep()

            self._set_state(EngineState.READY)
            logger.info("Recovery Engine initialized successfully.")
        except Exception as exc:
            self._set_state(EngineState.FAILED)
            logger.error("Failed to initialize Recovery Engine: %s", exc, exc_info=True)
            raise

    async def start(self) -> None:
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self._started_at_monotonic = time.monotonic()
        logger.info("Recovery Engine is RUNNING.")

    async def stop(self) -> None:
        if self._state in (EngineState.STOPPED, EngineState.UNINITIALIZED):
            return
        self._set_state(EngineState.STOPPING)
        self._quiescence_manager.release_maintenance_lock()
        self._set_state(EngineState.STOPPED)
        logger.info("Recovery Engine stopped cleanly.")

    # -- Boot-Time Crash Recovery Sweep --------------------------------------

    async def _startup_crash_recovery_sweep(self) -> None:
        """Inspect journal on startup and reconcile interrupted recoveries."""
        journal = self._journal_manager.load_journal()
        if journal is None:
            # No active journal; check for orphaned staging workspaces
            self._staging_manager.cleanup_directory(self._staging_manager.staging_base_dir)
            self._quiescence_manager.release_maintenance_lock()
            return

        logger.warning(
            "Detected active recovery journal '%s' in phase '%s' on boot.",
            journal.recovery_id,
            journal.current_phase.value,
        )

        phase = journal.current_phase
        pre_mutation_phases = {
            RecoveryJournalPhase.CREATED,
            RecoveryJournalPhase.CHECKPOINT_CREATED,
            RecoveryJournalPhase.ARTIFACT_VALIDATED,
            RecoveryJournalPhase.STAGING,
            RecoveryJournalPhase.STAGED,
            RecoveryJournalPhase.PRE_SWAP,
        }

        if phase in pre_mutation_phases:
            logger.info("Interrupted recovery '%s' was pre-mutation. Discarding staging.", journal.recovery_id)
            if journal.staged_state_locations:
                self._staging_manager.cleanup_directory(Path(journal.staged_state_locations.staging_root))
            self._journal_manager.archive_journal("cancelled_interrupted")
            self._quiescence_manager.release_maintenance_lock()
            return

        if phase == RecoveryJournalPhase.COMMITTED:
            logger.info("Previous recovery '%s' was fully committed. Cleaning up.", journal.recovery_id)
            if journal.staged_state_locations:
                self._staging_manager.cleanup_directory(Path(journal.staged_state_locations.staging_root))
            self._journal_manager.archive_journal("completed_archived")
            self._quiescence_manager.release_maintenance_lock()
            return

        # Destructive phase was interrupted: execute automated reverse swap
        destructive_phases = {
            RecoveryJournalPhase.STORAGE_SWAP_PARTIAL,
            RecoveryJournalPhase.STORAGE_SWAP_COMPLETE,
            RecoveryJournalPhase.DATABASE_SWAP_COMPLETE,
            RecoveryJournalPhase.RECONNECTING,
            RecoveryJournalPhase.VERIFYING,
            RecoveryJournalPhase.ROLLBACK_REQUIRED,
            RecoveryJournalPhase.ROLLING_BACK,
        }

        if phase in destructive_phases:
            logger.critical(
                "Interrupted recovery '%s' in destructive phase '%s'. Initiating rollback!", journal.recovery_id, phase
            )
            await self._execute_automated_rollback(journal)

    async def _execute_automated_rollback(self, journal: RecoveryJournalEntry) -> None:
        """Execute automated reverse-swap rollback from preserved rollback sources."""
        self._journal_manager.record_phase(
            RecoveryJournalPhase.ROLLING_BACK,
            operation="START_AUTOMATED_ROLLBACK",
            operator_notes="Initiated by startup crash recovery sweep.",
        )

        live_db = Path(journal.target_identity.database_path).resolve()
        rollback_sources = journal.rollback_state.rollback_sources

        try:
            # 1. Reverse storage swap
            self._storage_restorer.execute_reverse_swap(rollback_sources)

            # 2. Reverse database swap
            self._db_restorer.execute_reverse_swap(live_db, rollback_sources)

            # 3. Verify restored database
            valid, msg, _ = self._db_restorer.validate_sqlite_file(live_db)
            if not valid:
                raise RecoveryRollbackError(f"Restored database failed integrity check post-rollback: {msg}")

            # 4. Mark rolled back
            self._journal_manager.record_phase(
                RecoveryJournalPhase.ROLLED_BACK,
                operation="COMPLETE_AUTOMATED_ROLLBACK",
                operator_notes="Rollback verified successfully.",
            )
            self._journal_manager.archive_journal("rolled_back")
            self._quiescence_manager.release_maintenance_lock()
            self._recoveries_rolled_back_count += 1
            logger.warning("Automated rollback for recovery '%s' completed successfully.", journal.recovery_id)

        except Exception as exc:
            logger.critical("AUTOMATED ROLLBACK FAILED for recovery '%s': %s", journal.recovery_id, exc, exc_info=True)
            self._journal_manager.record_phase(
                RecoveryJournalPhase.FAILED_NEEDS_OPERATOR,
                operation="ROLLBACK_CRITICAL_FAILURE",
                error_message=str(exc),
                operator_notes="FAIL-CLOSED: System locked in maintenance. Manual operator intervention required.",
            )
            # Re-acquire lock to ensure system remains contained
            self._quiescence_manager.acquire_maintenance_lock(journal.recovery_id)
            await self._event_publisher.emit_operator_intervention_required(
                journal.recovery_id, journal.backup_id, str(exc)
            )
            raise RecoveryOperatorActionRequiredError(
                f"FATAL: Rollback failed during crash recovery: {exc}. System halted in fail-closed MAINTENANCE state."
            ) from exc

    # -- Primary Operations (IRecoveryEngine) -------------------------------

    async def create_recovery(self, request: CreateRecoveryRequest) -> CreateRecoveryResponse:
        """Execute the end-to-end recovery pipeline from an accepted backup."""
        if not request.confirm_destructive_restore:
            raise RecoveryValidationError(
                "Destructive system recovery requires explicit confirmation. Set confirm_destructive_restore=True."
            )

        # Concurrency Guards
        if self._recovery_lock.locked():
            raise RecoveryConcurrencyError("Another recovery operation is currently in progress.")

        # Check Backup Engine mutual exclusion
        if self._kernel is not None:
            backup_engine = getattr(self._kernel, "get_engine", lambda _: None)("backup")
            if (
                backup_engine is not None
                and hasattr(backup_engine, "_backup_lock")
                and backup_engine._backup_lock.locked()
            ):
                raise RecoveryConcurrencyError("Cannot start recovery while a backup operation is in progress.")

        async with self._recovery_lock:
            start_time = time.monotonic()
            now_iso = _utc_now_iso()
            recovery_id = f"kortex_rec_{int(time.time())}_{uuid.uuid4().hex[:8]}"
            self._active_operation = f"restore:{recovery_id}"
            self._recoveries_attempted_count += 1

            logger.info("Starting recovery operation '%s' for backup '%s'...", recovery_id, request.backup_id)
            await self._event_publisher.emit_requested(recovery_id, request.backup_id)

            # Acquire maintenance lock
            self._quiescence_manager.acquire_maintenance_lock(recovery_id)

            workspace = self._staging_manager.get_recovery_workspace(recovery_id)
            raw_zip = workspace / "raw.zip"
            extracted_dir = workspace / "extracted"

            live_db = self._resolve_live_db_path()
            live_storage = self._resolve_storage_root()

            # 1. Locate backup artifact
            assert self._validator is not None
            artifact_path, sidecar_meta = self._validator.locate_artifact(request.backup_id)

            # 2. Precheck & Envelope verification
            self._validator.verify_envelope(artifact_path, sidecar_meta)

            # Compute disk space requirement
            art_size = artifact_path.stat().st_size
            live_db_size = live_db.stat().st_size if live_db.is_file() else 0
            live_storage_size = (
                sum(f.stat().st_size for f in live_storage.rglob("*") if f.is_file()) if live_storage.is_dir() else 0
            )

            # Conservative preflight estimate before full extraction
            self._staging_manager.preflight_disk_capacity(
                artifact_size=art_size,
                uncompressed_payload_size=art_size * 2,
                extracted_db_size=live_db_size * 2,
                extracted_storage_size=live_storage_size * 2,
                live_db_size=live_db_size,
                live_storage_size=live_storage_size,
                target_volume_dir=live_storage,
            )
            await self._event_publisher.emit_precheck_passed(recovery_id, request.backup_id)

            # 3. Create Mandatory Pre-Recovery Safety Checkpoint
            checkpoint_id = await self._create_safety_checkpoint(recovery_id)
            await self._event_publisher.emit_safety_checkpoint_created(recovery_id, request.backup_id, checkpoint_id)

            # 4. Write Initial Journal
            target_ident = TargetIdentity(
                instance_id="kortex-instance",
                database_path=str(live_db),
                storage_root=str(live_storage),
            )
            journal_entry = RecoveryJournalEntry(
                recovery_id=recovery_id,
                backup_id=request.backup_id,
                target_identity=target_ident,
                created_at=now_iso,
                updated_at=now_iso,
                current_phase=RecoveryJournalPhase.CHECKPOINT_CREATED,
                completed_operations=["ACQUIRE_LOCK", "PRECHECK_RESOURCES", "CREATE_SAFETY_CHECKPOINT"],
                rollback_state=RollbackState(
                    status="ARMED",
                    safety_checkpoint_id=checkpoint_id,
                ),
                verification_state=VerificationState(),
                staged_state_locations=StagedStateLocations(
                    staging_root=str(workspace),
                    staged_db=str(extracted_dir / "database" / "kortex_snapshot.db"),
                    staged_storage=str(extracted_dir / "storage"),
                ),
                checksums=ChecksumsMetadata(
                    artifact_sha256=sidecar_meta.sha256 if sidecar_meta else "",
                ),
            )
            self._journal_manager.write_journal(journal_entry)

            try:
                # 5. Decrypt into staging
                self._journal_manager.record_phase(RecoveryJournalPhase.STAGING, operation="DECRYPT_PAYLOAD")
                key_bytes = (
                    RecoveryCryptoManager.parse_key_bytes(request.encryption_key) if request.encryption_key else None
                )
                self._crypto.decrypt_file(
                    source_path=artifact_path,
                    dest_path=raw_zip,
                    key_override=key_bytes,
                )

                # 6. Extract into staging
                self._staging_manager.extract_zip_safely(raw_zip, extracted_dir)

                # 7. Verify Checksums
                cs_ok, _, cs_errors = self._validator.verify_checksums(extracted_dir)
                if not cs_ok:
                    raise RecoveryValidationError(f"Archive checksums validation failed: {'; '.join(cs_errors)}")
                await self._event_publisher.emit_validated(recovery_id, request.backup_id)

                # 8. Verify Staged SQLite Database & Schema Compatibility
                staged_db = extracted_dir / "database" / "kortex_snapshot.db"
                db_valid, db_msg, snap_rev = self._db_restorer.validate_sqlite_file(staged_db)
                if not db_valid:
                    raise RecoveryValidationError(f"Staged database integrity check failed: {db_msg}")

                app_rev = self._db_restorer.get_app_schema_head()
                is_compat, req_migration, compat_msg = self._db_restorer.evaluate_schema_compatibility(
                    snap_rev, app_rev
                )
                if not is_compat:
                    raise RecoveryValidationError(f"Schema compatibility check failed: {compat_msg}")

                # Staged migration if older schema
                if req_migration:
                    logger.info("Executing staged-only migration on '%s'...", staged_db)
                    self._db_restorer.apply_staged_migration(staged_db)

                # 9. Verify Referential Consistency
                staged_storage = extracted_dir / "storage"
                ref_ok, missing_refs, _ = self._storage_restorer.verify_referential_consistency(
                    staged_db, staged_storage
                )
                if not ref_ok:
                    raise RecoveryValidationError(
                        f"Referential integrity failure: database references {len(missing_refs)} "
                        "files missing from storage."
                    )

                self._journal_manager.record_phase(RecoveryJournalPhase.STAGED, operation="VALIDATE_STAGED_STATE")
                await self._event_publisher.emit_staged(recovery_id, request.backup_id)

                # 10. Quiescence
                self._journal_manager.record_phase(RecoveryJournalPhase.PRE_SWAP, operation="ENTER_QUIESCENCE")
                db_mgr = self._kernel.db if self._kernel is not None and hasattr(self._kernel, "db") else None
                if db_mgr is not None:
                    await self._quiescence_manager.enter_quiescence(self._kernel, db_mgr)
                await self._event_publisher.emit_quiesced(recovery_id, request.backup_id)

                # 11. Destructive Swaps
                self._journal_manager.record_phase(RecoveryJournalPhase.STORAGE_SWAP_PARTIAL, operation="SWAP_STORAGE")
                storage_rollback_sources = self._storage_restorer.execute_storage_swap(staged_storage, recovery_id)

                # Update rollback sources in journal
                current_entry = self._journal_manager.load_journal()
                if current_entry:
                    current_rollback = dict(current_entry.rollback_state.rollback_sources)
                    current_rollback.update(storage_rollback_sources)
                    updated_rb = current_entry.rollback_state.model_copy(update={"rollback_sources": current_rollback})
                    self._journal_manager.update_journal(
                        current_entry.model_copy(update={"rollback_state": updated_rb})
                    )

                self._journal_manager.record_phase(RecoveryJournalPhase.STORAGE_SWAP_COMPLETE)

                # Swap Database
                db_rollback_sources = self._db_restorer.execute_database_swap(staged_db, live_db, recovery_id)
                if current_entry:
                    current_rollback = dict(current_entry.rollback_state.rollback_sources)
                    current_rollback.update(storage_rollback_sources)
                    current_rollback.update(db_rollback_sources)
                    updated_rb = current_entry.rollback_state.model_copy(update={"rollback_sources": current_rollback})
                    self._journal_manager.update_journal(
                        current_entry.model_copy(update={"rollback_state": updated_rb})
                    )

                self._journal_manager.record_phase(
                    RecoveryJournalPhase.DATABASE_SWAP_COMPLETE, operation="SWAP_DATABASE"
                )
                await self._event_publisher.emit_swapped(recovery_id, request.backup_id)

                # 12. Reconnect Database
                self._journal_manager.record_phase(RecoveryJournalPhase.RECONNECTING, operation="RECONNECT_DATABASE")
                if db_mgr is not None:
                    await self._quiescence_manager.exit_quiescence(self._kernel, db_mgr)

                # 13. Post-Restore Verification
                self._journal_manager.record_phase(RecoveryJournalPhase.VERIFYING, operation="VERIFY_POST_RESTORE")
                live_valid, live_msg, _ = self._db_restorer.validate_sqlite_file(live_db)
                if not live_valid:
                    raise RecoveryVerificationError(f"Post-restore live database integrity check failed: {live_msg}")

                await self._event_publisher.emit_verified(recovery_id, request.backup_id)

                # 14. Commit Recovery
                self._journal_manager.record_phase(RecoveryJournalPhase.COMMITTED, operation="COMMIT_RECOVERY")
                self._journal_manager.archive_journal("completed")
                self._quiescence_manager.release_maintenance_lock()

                # Clean staging directory
                self._staging_manager.cleanup_workspace(recovery_id)

                duration = time.monotonic() - start_time
                self._recoveries_completed_count += 1
                self._last_recovery_duration_seconds = duration
                self._last_recovery_timestamp = _utc_now_iso()
                self._active_operation = None

                storage_files_count = sum(1 for f in live_storage.rglob("*") if f.is_file())
                await self._event_publisher.emit_completed(
                    recovery_id, request.backup_id, checkpoint_id, storage_files_count
                )

                logger.info("Recovery operation '%s' completed successfully in %.2fs.", recovery_id, duration)

                return CreateRecoveryResponse(
                    recovery_id=recovery_id,
                    backup_id=request.backup_id,
                    state=RecoveryState.COMPLETED,
                    created_at=now_iso,
                    completed_at=_utc_now_iso(),
                    safety_checkpoint_id=checkpoint_id,
                    database_restored=True,
                    storage_files_restored=storage_files_count,
                    duration_seconds=duration,
                    is_success=True,
                )

            except Exception as exc:
                duration = time.monotonic() - start_time
                self._recoveries_failed_count += 1
                self._last_error_message = str(exc)
                self._active_operation = None

                logger.error("Recovery operation '%s' encountered error: %s", recovery_id, exc, exc_info=True)

                # Determine if live system was modified (destructive phase reached)
                active_j = self._journal_manager.load_journal()
                if active_j and active_j.current_phase in {
                    RecoveryJournalPhase.STORAGE_SWAP_PARTIAL,
                    RecoveryJournalPhase.STORAGE_SWAP_COMPLETE,
                    RecoveryJournalPhase.DATABASE_SWAP_COMPLETE,
                    RecoveryJournalPhase.RECONNECTING,
                    RecoveryJournalPhase.VERIFYING,
                }:
                    logger.critical("Failure occurred post-swap for '%s'. Executing automated rollback!", recovery_id)
                    await self._execute_automated_rollback(active_j)
                    await self._event_publisher.emit_rolled_back(recovery_id, request.backup_id)
                    raise RecoveryRollbackError(
                        f"Recovery operation '{recovery_id}' failed and was successfully rolled back: {exc}"
                    ) from exc

                # Pre-mutation failure
                self._staging_manager.cleanup_workspace(recovery_id)
                self._journal_manager.archive_journal("failed_pre_mutation")
                self._quiescence_manager.release_maintenance_lock()
                await self._event_publisher.emit_failed(recovery_id, request.backup_id, str(exc))
                raise

    async def _create_safety_checkpoint(self, recovery_id: str) -> str:
        """Capture an authoritative full-instance safety backup before mutation."""
        if self._kernel is None:
            raise PreRecoveryCheckpointError("Kernel is required to create a pre-recovery safety backup.")

        backup_engine = getattr(self._kernel, "get_engine", lambda _: None)("backup")
        if backup_engine is None or not hasattr(backup_engine, "create_backup"):
            raise PreRecoveryCheckpointError("Backup Engine is not available in Kernel.")

        req = BackupCreateRequest(
            scope=BackupScope.FULL_INSTANCE,
            metadata={
                "origin": "pre_recovery_safety_checkpoint",
                "is_safety_checkpoint": True,
                "target_recovery_id": recovery_id,
            },
        )

        try:
            res = await backup_engine.create_backup(req)
            checkpoint_id = res.backup_id
        except Exception as exc:
            raise PreRecoveryCheckpointError(f"Pre-recovery safety backup creation failed: {exc}") from exc

        # Verify checkpoint artifact exists and is discoverable
        checkpoint_file = Path(self._config.backup_directory) / f"{checkpoint_id}{BACKUP_EXTENSION}"
        if not checkpoint_file.is_file():
            raise PreRecoveryCheckpointError(f"Safety backup artifact was not found on disk at '{checkpoint_file}'.")

        logger.info("Pre-recovery safety checkpoint created and verified: '%s'", checkpoint_id)
        return checkpoint_id

    async def list_recoveries(self, request: ListRecoveriesRequest) -> ListRecoveriesResponse:
        """List recovery history and active state."""
        recoveries: list[GetRecoveryResponse] = []
        journal = self._journal_manager.load_journal()
        if journal is not None:
            active = await self.get_recovery(GetRecoveryRequest(recovery_id=journal.recovery_id))
            recoveries.append(active)

        return ListRecoveriesResponse(
            recoveries=recoveries[request.offset : request.offset + request.limit],
            total_count=len(recoveries),
        )

    async def get_recovery(self, request: GetRecoveryRequest) -> GetRecoveryResponse:
        """Retrieve status and journal metadata for a recovery operation."""
        journal = self._journal_manager.load_journal()
        if journal is None or (request.recovery_id and journal.recovery_id != request.recovery_id):
            return GetRecoveryResponse(
                recovery_id=request.recovery_id or "none",
                backup_id="",
                state=RecoveryState.COMPLETED if self._recoveries_completed_count > 0 else RecoveryState.REQUESTED,
                phase=RecoveryJournalPhase.COMMITTED,
                created_at=_utc_now_iso(),
                updated_at=_utc_now_iso(),
                is_active=self._recovery_lock.locked(),
                error_message=self._last_error_message,
            )

        state_map = {
            RecoveryJournalPhase.CREATED: RecoveryState.REQUESTED,
            RecoveryJournalPhase.CHECKPOINT_CREATED: RecoveryState.CHECKPOINTING,
            RecoveryJournalPhase.ARTIFACT_VALIDATED: RecoveryState.VALIDATING,
            RecoveryJournalPhase.STAGING: RecoveryState.STAGING,
            RecoveryJournalPhase.STAGED: RecoveryState.PREPARING_SWAP,
            RecoveryJournalPhase.PRE_SWAP: RecoveryState.PREPARING_SWAP,
            RecoveryJournalPhase.STORAGE_SWAP_PARTIAL: RecoveryState.SWAPPING,
            RecoveryJournalPhase.STORAGE_SWAP_COMPLETE: RecoveryState.SWAPPING,
            RecoveryJournalPhase.DATABASE_SWAP_COMPLETE: RecoveryState.SWAPPING,
            RecoveryJournalPhase.RECONNECTING: RecoveryState.RECONNECTING,
            RecoveryJournalPhase.VERIFYING: RecoveryState.VERIFYING,
            RecoveryJournalPhase.COMMITTED: RecoveryState.COMPLETED,
            RecoveryJournalPhase.ROLLBACK_REQUIRED: RecoveryState.ROLLBACK_REQUIRED,
            RecoveryJournalPhase.ROLLING_BACK: RecoveryState.ROLLING_BACK,
            RecoveryJournalPhase.ROLLED_BACK: RecoveryState.ROLLED_BACK,
            RecoveryJournalPhase.FAILED_NEEDS_OPERATOR: RecoveryState.FAILED_NEEDS_OPERATOR,
        }

        return GetRecoveryResponse(
            recovery_id=journal.recovery_id,
            backup_id=journal.backup_id,
            state=state_map.get(journal.current_phase, RecoveryState.FAILED),
            phase=journal.current_phase,
            created_at=journal.created_at,
            updated_at=journal.updated_at,
            safety_checkpoint_id=journal.rollback_state.safety_checkpoint_id,
            completed_operations=journal.completed_operations,
            is_active=self._recovery_lock.locked(),
            error_message=journal.error_message,
            journal=journal.model_dump(mode="json"),
        )

    async def verify_recovery(self, request: VerifyRecoveryRequest) -> VerifyRecoveryResponse:
        """Preflight and verify a backup artifact without modifying live system state."""
        if self._validator is None:
            raise RecoveryNotFoundError("Recovery Engine is not initialized.")
        return await self._validator.verify_backup(request)

    async def delete_recovery(self, request: DeleteRecoveryRequest) -> DeleteRecoveryResponse:
        """Clean or cancel recovery workspace and journal."""
        journal = self._journal_manager.load_journal()
        if journal is not None and journal.recovery_id == request.recovery_id:
            if self._recovery_lock.locked():
                raise RecoveryConcurrencyError("Cannot delete or cancel an active in-flight recovery operation.")
            self._staging_manager.cleanup_workspace(request.recovery_id)
            self._journal_manager.delete_journal()
            self._quiescence_manager.release_maintenance_lock()
            return DeleteRecoveryResponse(
                recovery_id=request.recovery_id,
                deleted=True,
                message=f"Recovery operation '{request.recovery_id}' journal and staging cleaned successfully.",
            )

        return DeleteRecoveryResponse(
            recovery_id=request.recovery_id,
            deleted=False,
            message=f"No active recovery found with ID '{request.recovery_id}'.",
        )

    def get_diagnostics(self) -> RecoveryDiagnostics:
        """Return operational self-diagnostics for Recovery Engine."""
        uptime = (time.monotonic() - self._started_at_monotonic) if self._started_at_monotonic else 0.0
        return RecoveryDiagnostics(
            engine_name=RECOVERY_ENGINE_NAME,
            engine_version=CURRENT_ENGINE_VERSION,
            state=self.state.value,
            active_operation=self._active_operation,
            recoveries_attempted=self._recoveries_attempted_count,
            recoveries_completed=self._recoveries_completed_count,
            recoveries_failed=self._recoveries_failed_count,
            recoveries_rolled_back=self._recoveries_rolled_back_count,
            last_recovery_duration_seconds=self._last_recovery_duration_seconds,
            last_recovery_timestamp=self._last_recovery_timestamp,
            last_error_message=self._last_error_message,
            journal_path=str(self._journal_manager.journal_path),
            staging_path=str(self._staging_manager.staging_base_dir),
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

    # -- Security & Execution Context Enforcement ----------------------------

    def _enforce_execution_context(
        self,
        execution_context: CapabilityExecutionContext | None,
        required_permission: str,
    ) -> CapabilityExecutionContext:
        """Enforce trusted execution context, authentication, and fail-closed permission checks."""
        if execution_context is None:
            raise RecoveryAuthenticationError(
                "Missing trusted execution context. Direct unauthenticated capability invocation is forbidden."
            )

        principal = execution_context.principal
        if principal is not None:
            roles = set(principal.roles)
            if "TENANT_ADMIN" not in roles and "SYSTEM_ADMIN" not in roles:
                perms = set(principal.attributes.get("permissions", []))
                if required_permission not in perms and PERMISSION_RECOVERY_MANAGE not in perms:
                    raise RecoveryAuthorizationError(
                        f"Unauthorized principal '{principal.principal_id}': "
                        f"missing required permission '{required_permission}'."
                    )
        return execution_context

    def _resolve_authoritative_tenant(
        self,
        execution_context: CapabilityExecutionContext,
        caller_tenant: str | None = None,
    ) -> str:
        """Enforce tenant isolation: execution context tenant is strictly authoritative."""
        return execution_context.tenant_id

    # -- Capability Handlers -------------------------------------------------

    async def handle_recovery_create(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        backup_id: str = "",
        confirm_destructive_restore: bool = False,
        encryption_key: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.recovery.create."""
        self._enforce_execution_context(execution_context, PERMISSION_RECOVERY_MANAGE)
        if "allow_forward_migration" in kwargs:
            raise RecoverySecurityError(
                "Caller migration bypass prohibited: forward migration policy is controlled solely by configuration."
            )

        if not backup_id:
            raise RecoveryValidationError("Parameter 'backup_id' is required.")

        req = CreateRecoveryRequest(
            backup_id=backup_id,
            confirm_destructive_restore=confirm_destructive_restore,
            encryption_key=encryption_key,
            idempotency_key=idempotency_key,
            metadata=metadata or {},
        )
        res = await self.create_recovery(req)
        return res.model_dump(mode="json")

    async def handle_recovery_list(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        limit: int = 50,
        offset: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.recovery.list."""
        self._enforce_execution_context(execution_context, PERMISSION_RECOVERY_READ)
        req = ListRecoveriesRequest(limit=limit, offset=offset)
        res = await self.list_recoveries(req)
        return res.model_dump(mode="json")

    async def handle_recovery_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        recovery_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.recovery.get."""
        self._enforce_execution_context(execution_context, PERMISSION_RECOVERY_READ)
        if recovery_id and (".." in recovery_id or "/" in recovery_id or "\\" in recovery_id):
            raise RecoverySecurityError(f"Invalid recovery ID: '{recovery_id}' contains traversal characters.")

        req = GetRecoveryRequest(recovery_id=recovery_id)
        res = await self.get_recovery(req)
        return res.model_dump(mode="json")

    async def handle_recovery_verify(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        backup_id: str = "",
        encryption_key: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.recovery.verify."""
        self._enforce_execution_context(execution_context, PERMISSION_RECOVERY_READ)
        if not backup_id:
            raise RecoveryValidationError("Parameter 'backup_id' is required.")

        req = VerifyRecoveryRequest(
            backup_id=backup_id,
            encryption_key=encryption_key,
        )
        res = await self.verify_recovery(req)
        return res.model_dump(mode="json")

    async def handle_recovery_delete(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        recovery_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.recovery.delete."""
        self._enforce_execution_context(execution_context, PERMISSION_RECOVERY_MANAGE)
        if not recovery_id:
            raise RecoveryValidationError("Parameter 'recovery_id' is required.")

        req = DeleteRecoveryRequest(recovery_id=recovery_id)
        res = await self.delete_recovery(req)
        return res.model_dump(mode="json")

    async def handle_recovery_diagnostics_get(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Capability handler for kortex.recovery.diagnostics.get."""
        self._enforce_execution_context(execution_context, PERMISSION_RECOVERY_READ)
        diag = self.get_diagnostics()
        return diag.model_dump(mode="json")
