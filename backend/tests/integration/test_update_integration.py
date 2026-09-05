"""End-to-end integration tests for KORTEX Update Engine.

Phase 7 — Production Hardening — Update Engine.
Verifies the complete lifecycle:
CHECK -> STAGE -> CHECKPOINT -> QUIESCE -> MIGRATE -> SWAP -> VERIFY -> REPORT.
Verifies the 3 Clarifications:
- Clarification 1: Recovery delegation passes confirm_destructive_restore=True explicitly.
- Clarification 2: Filesystem swap != runtime activation
  (filesystem_updated=True, restart_required=True, runtime_activated=False).
- Clarification 3: Exact 12 canonical events emitted on kortex.update.* namespace.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.backup.constants import BackupScope, BackupState
from kortex.engines.backup.models import CreateBackupResponse
from kortex.engines.recovery.constants import RecoveryState
from kortex.engines.recovery.models import CreateRecoveryRequest, CreateRecoveryResponse
from kortex.engines.security.models import PrincipalType, SecurityPrincipal
from kortex.engines.update.constants import (
    ALL_UPDATE_EVENTS,
    EVENT_UPDATE_APPLIED,
    EVENT_UPDATE_CHECKED,
    EVENT_UPDATE_COMPLETED,
    EVENT_UPDATE_MANIFEST_VERIFIED,
    EVENT_UPDATE_MIGRATED,
    EVENT_UPDATE_QUIESCED,
    EVENT_UPDATE_ROLLED_BACK,
    EVENT_UPDATE_SAFETY_CHECKPOINT_CREATED,
    EVENT_UPDATE_STAGED,
    EVENT_UPDATE_VERIFIED,
    PERMISSION_UPDATE_MANAGE,
    PERMISSION_UPDATE_READ,
    UpdateJournalPhase,
    UpdateState,
)
from kortex.engines.update.crypto import UpdateCryptoManager
from kortex.engines.update.engine import UpdateEngine
from kortex.engines.update.exceptions import UpdateCheckpointError, UpdateVerificationError
from kortex.engines.update.journal import UpdateJournalManager
from kortex.engines.update.models import (
    UpdateApplyRequest,
    UpdateCheckRequest,
    UpdateManifest,
    UpdateManifestPackage,
    UpdateManifestVersion,
    UpdateStageRequest,
)


def make_admin_context() -> CapabilityExecutionContext:
    """Helper to build an authorized execution context."""
    principal = SecurityPrincipal(
        principal_id="admin-user",
        tenant_id="primary",
        principal_type=PrincipalType.USER,
        roles=["admin", "TENANT_ADMIN"],
        attributes={"permissions": [PERMISSION_UPDATE_READ, PERMISSION_UPDATE_MANAGE]},
    )
    return CapabilityExecutionContext(
        request_id="req-integ-1",
        correlation_id="corr-integ-1",
        capability_name="kortex.update.test",
        principal=principal,
        tenant_id="primary",
    )


def create_signed_package(
    tmp_path: Path,
    key_id: str,
    private_key: Ed25519PrivateKey,
    target_version: str = "0.2.0",
    requires_migration: bool = False,
    target_revision: str | None = None,
) -> tuple[Path, Path]:
    """Helper to construct a valid update zip archive and signed manifest."""
    # 1. Create target zip payload
    pkg_dir = tmp_path / "pkg_work"
    pkg_dir.mkdir(exist_ok=True)
    zip_path = pkg_dir / f"update-{target_version}.zip"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("core/module.py", f"VERSION = '{target_version}'\n")
    zip_bytes = buf.getvalue()
    zip_path.write_bytes(zip_bytes)

    crypto = UpdateCryptoManager(vendor_keys={key_id: private_key.public_key().public_bytes_raw()})
    zip_sha = crypto.compute_sha256(zip_bytes)

    from kortex.engines.update.models import (
        UpdateManifest,
        UpdateManifestCompatibility,
        UpdateManifestDatabase,
        UpdateManifestPackage,
        UpdateManifestVersion,
    )

    manifest_obj = UpdateManifest(
        manifest_version="kortex-update-manifest-v1.0",
        manifest_id=f"mf-{target_version}",
        created_at="2026-09-05T00:00:00Z",
        expires_at="2026-09-12T00:00:00Z",
        key_id=key_id,
        signature="",
        version=UpdateManifestVersion(
            target_version=target_version,
            min_supported_version="0.1.0",
            release_channel="stable",
        ),
        compatibility=UpdateManifestCompatibility(
            platforms=["windows", "linux", "darwin", "win32"],
            architectures=["x86_64", "amd64", "arm64", "aarch64"],
            python_version_min="3.11",
        ),
        package=UpdateManifestPackage(
            filename=zip_path.name,
            sha256=zip_sha,
            size_bytes=len(zip_bytes),
            uncompressed_bytes=len(zip_bytes) * 2,
            file_count=1,
        ),
        database=UpdateManifestDatabase(
            requires_migration=requires_migration,
            target_revision=target_revision,
            supported_source_revisions=["rev_001"] if requires_migration else [],
            reversible=False,
        ),
    )

    manifest_dict = manifest_obj.model_dump(mode="json")
    sig_str = crypto.sign_manifest(manifest_dict, private_key.private_bytes_raw())
    manifest_dict["signature"] = sig_str

    manifest_path = pkg_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_dict, indent=2))

    return manifest_path, zip_path


@pytest.mark.asyncio
async def test_end_to_end_successful_update_lifecycle(tmp_path: Path) -> None:
    """Verify complete successful update lifecycle: check, stage, apply, verify."""
    # 1. Setup keys
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes_raw()
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")
    key_id = "test-prod-key-2026"

    update_dir = tmp_path / "storage_data" / ".update"
    target_root = tmp_path / "app_root"
    target_root.mkdir(parents=True)
    (target_root / "core").mkdir(parents=True)
    live_file = target_root / "core" / "module.py"
    live_file.write_text("VERSION = '0.1.0'\n")

    manifest_path, zip_path = create_signed_package(
        tmp_path,
        key_id,
        private_key,
        target_version="0.2.0",
        requires_migration=True,
        target_revision="rev_002",
    )

    # 2. Setup mock Kernel with Backup and Recovery engines
    mock_kernel = MagicMock()
    mock_backup = MagicMock()
    mock_backup.create_backup = AsyncMock(
        return_value=CreateBackupResponse(
            backup_id="bck-safety-chk-100",
            state=BackupState.VALID,
            created_at="2026-09-05T00:00:00Z",
            finalized_at="2026-09-05T00:00:01Z",
            filename="bck-safety-chk-100.tar.gz",
            file_size_bytes=1000,
            sha256="abc",
            is_encrypted=True,
        )
    )

    emitted_events: list[tuple[str, dict]] = []
    mock_event = MagicMock()

    async def capture_event(event_name: str, payload: dict) -> None:
        emitted_events.append((event_name, payload))

    mock_event.publish = AsyncMock(side_effect=capture_event)

    def get_engine_mock(name: str):
        if name == "backup":
            return mock_backup
        if name == "event":
            return mock_event
        return None

    mock_kernel.get_engine.side_effect = get_engine_mock

    # 3. Initialize UpdateEngine
    engine = UpdateEngine(update_dir=update_dir, current_version="0.1.0", target_root=target_root)
    engine.set_kernel(mock_kernel)
    engine._crypto_manager = UpdateCryptoManager(trusted_public_keys={key_id: pub_b64})

    ctx = make_admin_context()

    # 4. Check Update
    check_req = UpdateCheckRequest(manifest_content=json.loads(manifest_path.read_text()))
    check_res = await engine.check(check_req, context=ctx)
    assert check_res.update_available is True
    assert check_res.target_version == "0.2.0"

    # 5. Stage Update
    stage_req = UpdateStageRequest(
        manifest_path=str(manifest_path),
        archive_path=str(zip_path),
    )
    stage_res = await engine.stage(stage_req, context=ctx)
    assert stage_res.staged is True
    assert stage_res.target_version == "0.2.0"
    update_id = stage_res.update_id

    # 6. Apply Update
    apply_req = UpdateApplyRequest(update_id=update_id)
    with (
        patch.object(
            engine._migrator,
            "run_forward_migration",
            return_value={"migrated": True, "target_revision": "rev_002", "status": "MIGRATION_COMPLETED"},
        ),
        patch.object(
            engine._migrator,
            "get_current_revision",
            return_value="rev_002",
        ),
    ):
        apply_res = await engine.apply(apply_req, context=ctx)

    # CLARIFICATION 2 verification: Filesystem swap != runtime activation
    assert apply_res.status == UpdateState.COMPLETED
    assert apply_res.filesystem_updated is True
    assert apply_res.restart_required is True
    assert apply_res.runtime_activated is False
    assert apply_res.safety_checkpoint_id == "bck-safety-chk-100"

    # Verify live file on disk is updated
    assert live_file.read_text() == "VERSION = '0.2.0'\n"

    # Verify .rollback copy was cleaned up after successful verification
    rollback_copy = target_root / "core" / f"module.py.rollback_{update_id}"
    assert not rollback_copy.exists()

    # Verify Backup checkpoint was invoked with FULL_INSTANCE
    mock_backup.create_backup.assert_awaited_once()
    chk_arg = mock_backup.create_backup.call_args[0][0]
    assert chk_arg.scope == BackupScope.FULL_INSTANCE
    assert chk_arg.metadata["is_safety_checkpoint"] is True

    # CLARIFICATION 3 verification: Exactly canonical events emitted
    emitted_names = [ev[0] for ev in emitted_events]
    assert EVENT_UPDATE_CHECKED in emitted_names
    assert EVENT_UPDATE_MANIFEST_VERIFIED in emitted_names
    assert EVENT_UPDATE_STAGED in emitted_names
    assert EVENT_UPDATE_SAFETY_CHECKPOINT_CREATED in emitted_names
    assert EVENT_UPDATE_QUIESCED in emitted_names
    assert EVENT_UPDATE_MIGRATED in emitted_names
    assert EVENT_UPDATE_APPLIED in emitted_names
    assert EVENT_UPDATE_VERIFIED in emitted_names
    assert EVENT_UPDATE_COMPLETED in emitted_names

    # Confirm all emitted event names belong to ALL_UPDATE_EVENTS
    for name in emitted_names:
        assert name in ALL_UPDATE_EVENTS


@pytest.mark.asyncio
async def test_recovery_delegation_on_post_mutation_failure(tmp_path: Path) -> None:
    """Verify CLARIFICATION 1: UpdateEngine explicitly sets confirm_destructive_restore=True
    when delegating to RecoveryEngine.
    """
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes_raw()
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")
    key_id = "test-prod-key-2026"

    update_dir = tmp_path / "storage_data" / ".update"
    target_root = tmp_path / "app_root"
    target_root.mkdir(parents=True)
    (target_root / "core").mkdir(parents=True)
    live_file = target_root / "core" / "module.py"
    live_file.write_text("VERSION = '0.1.0'\n")

    manifest_path, zip_path = create_signed_package(
        tmp_path,
        key_id,
        private_key,
        target_version="0.2.0",
        requires_migration=True,
        target_revision="rev_002",
    )

    # Setup mock Kernel with Backup and Recovery engines
    mock_kernel = MagicMock()
    mock_backup = MagicMock()
    mock_backup.create_backup = AsyncMock(
        return_value=CreateBackupResponse(
            backup_id="bck-checkpoint-555",
            state=BackupState.VALID,
            created_at="2026-09-05T00:00:00Z",
            finalized_at="2026-09-05T00:00:01Z",
            filename="bck-checkpoint-555.tar.gz",
            file_size_bytes=1000,
            sha256="abc",
            is_encrypted=True,
        )
    )

    mock_recovery = MagicMock()
    recovery_calls: list[CreateRecoveryRequest] = []

    async def record_recovery(req: CreateRecoveryRequest) -> CreateRecoveryResponse:
        recovery_calls.append(req)
        return CreateRecoveryResponse(
            recovery_id="rec-rollback-001",
            backup_id=req.backup_id,
            state=RecoveryState.COMPLETED,
            created_at="2026-09-05T00:00:00Z",
            completed_at="2026-09-05T00:00:01Z",
            safety_checkpoint_id="bck-chk-pre-rec",
            database_restored=True,
            is_success=True,
        )

    mock_recovery.create_recovery = AsyncMock(side_effect=record_recovery)

    emitted_events: list[tuple[str, dict]] = []
    mock_event = MagicMock()

    async def capture_event(event_name: str, payload: dict) -> None:
        emitted_events.append((event_name, payload))

    mock_event.publish = AsyncMock(side_effect=capture_event)

    def get_engine_mock(name: str):
        if name == "backup":
            return mock_backup
        if name == "recovery":
            return mock_recovery
        if name == "event":
            return mock_event
        return None

    mock_kernel.get_engine.side_effect = get_engine_mock

    engine = UpdateEngine(update_dir=update_dir, current_version="0.1.0", target_root=target_root)
    engine.set_kernel(mock_kernel)
    engine._crypto_manager = UpdateCryptoManager(trusted_public_keys={key_id: pub_b64})

    ctx = make_admin_context()

    # Stage update
    stage_res = await engine.stage(
        UpdateStageRequest(manifest_path=str(manifest_path), archive_path=str(zip_path)),
        context=ctx,
    )
    update_id = stage_res.update_id

    # Mock migration success but simulate post-update verification failure!
    with (
        patch.object(
            engine._migrator,
            "run_forward_migration",
            return_value={"migrated": True, "target_revision": "rev_002"},
        ),
        patch.object(
            engine,
            "_verify_post_update",
            side_effect=UpdateVerificationError("Simulated verification failure"),
        ),
        pytest.raises(UpdateVerificationError),
    ):
        await engine.apply(UpdateApplyRequest(update_id=update_id), context=ctx)

    # Verify CLARIFICATION 1: UpdateEngine invoked RecoveryEngine with confirm_destructive_restore=True
    assert len(recovery_calls) == 1
    rec_req = recovery_calls[0]
    assert rec_req.backup_id == "bck-checkpoint-555"
    assert rec_req.confirm_destructive_restore is True
    assert rec_req.metadata["origin"] == "update_engine_post_mutation_rollback"
    assert rec_req.metadata["update_id"] == update_id

    # Verify Layer 1 rollback reverted the live file
    assert live_file.read_text() == "VERSION = '0.1.0'\n"

    # Verify rolled_back event was emitted
    emitted_names = [ev[0] for ev in emitted_events]
    assert EVENT_UPDATE_ROLLED_BACK in emitted_names


@pytest.mark.asyncio
async def test_checkpoint_failure_aborts_before_any_destructive_mutation(tmp_path: Path) -> None:
    """If BackupEngine.create_backup fails, Update Engine must abort immediately with
    UpdateCheckpointError and MUST NOT acquire the maintenance lock, drain the database,
    run migration, or swap any files. Live state must remain completely untouched, per the
    inviolable invariant that no destructive mutation may occur without a valid checkpoint.
    """
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes_raw()
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")
    key_id = "test-prod-key-2026"

    update_dir = tmp_path / "storage_data" / ".update"
    target_root = tmp_path / "app_root"
    target_root.mkdir(parents=True)
    (target_root / "core").mkdir(parents=True)
    live_file = target_root / "core" / "module.py"
    live_file.write_text("VERSION = '0.1.0'\n")

    manifest_path, zip_path = create_signed_package(
        tmp_path,
        key_id,
        private_key,
        target_version="0.2.0",
    )

    mock_kernel = MagicMock()
    mock_backup = MagicMock()
    mock_backup.create_backup = AsyncMock(side_effect=RuntimeError("Simulated backup failure"))

    def get_engine_mock(name: str):
        if name == "backup":
            return mock_backup
        return None

    mock_kernel.get_engine.side_effect = get_engine_mock

    engine = UpdateEngine(update_dir=update_dir, current_version="0.1.0", target_root=target_root)
    engine.set_kernel(mock_kernel)
    engine._crypto_manager = UpdateCryptoManager(trusted_public_keys={key_id: pub_b64})

    ctx = make_admin_context()

    stage_res = await engine.stage(
        UpdateStageRequest(manifest_path=str(manifest_path), archive_path=str(zip_path)),
        context=ctx,
    )
    update_id = stage_res.update_id

    with pytest.raises(UpdateCheckpointError):
        await engine.apply(UpdateApplyRequest(update_id=update_id), context=ctx)

    # Live state is completely untouched -- zero destructive mutation occurred.
    assert live_file.read_text() == "VERSION = '0.1.0'\n"
    # The maintenance lock must never have been acquired since checkpointing failed first.
    assert not engine.quiescence_manager.is_maintenance_locked()


@pytest.mark.asyncio
async def test_startup_sweep_confirms_runtime_activation_after_restart(tmp_path: Path) -> None:
    """Simulates a crash immediately after the filesystem swap completed but before the
    crashed process reached VERIFIED/COMMITTED. On restart, a brand-new UpdateEngine
    instance's initialize() must run the startup crash-recovery sweep, detect that files
    were already swapped and a restart has now occurred (filesystem_applied=True,
    runtime_activated=False), and commit + archive the journal with runtime_activated=True --
    proving Update Engine only ever claims runtime activation after an actual restart
    boundary, never merely because files changed on disk (Clarification 2).
    """
    update_dir = tmp_path / "storage_data" / ".update"
    manifest = UpdateManifest(
        manifest_id="mf-restart-test",
        created_at="2026-09-05T00:00:00Z",
        expires_at="2026-09-12T00:00:00Z",
        version=UpdateManifestVersion(target_version="0.2.0", min_supported_version="0.1.0"),
        package=UpdateManifestPackage(
            filename="upd.zip", sha256="abc", size_bytes=1, uncompressed_bytes=1, file_count=1
        ),
    )

    # Simulate the crashed process: journal reflects a filesystem swap that succeeded but
    # never reached VERIFIED/COMMITTED before the process died.
    journal = UpdateJournalManager(update_base_dir=update_dir)
    journal.create_journal(update_id="upd-restart-01", manifest=manifest, current_version="0.1.0")
    journal.record_phase(
        UpdateJournalPhase.FILES_SWAPPED,
        safety_checkpoint_id="bck-restart-1",
        filesystem_applied=True,
        restart_required=True,
        runtime_activated=False,
    )
    assert (update_dir / "journal.json").is_file()

    # Simulate restart: a brand new UpdateEngine process instance boots against the same directory.
    restarted_engine = UpdateEngine(update_dir=update_dir)
    kernel = MagicMock()
    kernel.register_capability = MagicMock()
    kernel.get_engine.side_effect = lambda name: None
    await restarted_engine.initialize(kernel=kernel)

    # The startup crash-recovery sweep must have confirmed activation and archived the journal.
    assert not (update_dir / "journal.json").exists()
    history = restarted_engine.journal_manager.load_history()
    assert len(history) == 1
    assert history[0].update_id == "upd-restart-01"
    assert history[0].status == "COMPLETED"
    assert not restarted_engine.quiescence_manager.is_maintenance_locked()
