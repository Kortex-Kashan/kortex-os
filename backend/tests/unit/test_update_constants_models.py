"""Unit tests for Update Engine constants, enumerations, events, and Pydantic models.

Phase 7 — Production Hardening — Update Engine.
Verifies the exact 12-event contract, 6 capabilities, permissions, states, and data contracts.
"""

from __future__ import annotations

from kortex.engines.update.constants import (
    ALL_UPDATE_CAPABILITIES,
    ALL_UPDATE_EVENTS,
    CAPABILITY_UPDATE_APPLY,
    CAPABILITY_UPDATE_CANCEL,
    CAPABILITY_UPDATE_CHECK,
    CAPABILITY_UPDATE_DIAGNOSTICS_GET,
    CAPABILITY_UPDATE_GET,
    CAPABILITY_UPDATE_STAGE,
    CURRENT_ENGINE_VERSION,
    CURRENT_JOURNAL_FORMAT_VERSION,
    CURRENT_MANIFEST_FORMAT_VERSION,
    EVENT_UPDATE_APPLIED,
    EVENT_UPDATE_CHECKED,
    EVENT_UPDATE_COMPLETED,
    EVENT_UPDATE_FAILED,
    EVENT_UPDATE_MANIFEST_VERIFIED,
    EVENT_UPDATE_MIGRATED,
    EVENT_UPDATE_OPERATOR_INTERVENTION_REQUIRED,
    EVENT_UPDATE_QUIESCED,
    EVENT_UPDATE_ROLLED_BACK,
    EVENT_UPDATE_SAFETY_CHECKPOINT_CREATED,
    EVENT_UPDATE_STAGED,
    EVENT_UPDATE_VERIFIED,
    PERMISSION_UPDATE_MANAGE,
    PERMISSION_UPDATE_READ,
    UPDATE_CAPABILITY_PERMISSIONS,
    UPDATE_ENGINE_NAME,
    UpdateJournalPhase,
    UpdateState,
)
from kortex.engines.update.exceptions import (
    UpdateArchiveSecurityError,
    UpdateAuthenticationError,
    UpdateAuthorizationError,
    UpdateCheckpointError,
    UpdateChecksumMismatchError,
    UpdateCompatibilityError,
    UpdateConcurrencyError,
    UpdateDiskSpaceError,
    UpdateDowngradeError,
    UpdateError,
    UpdateKeyNotFoundError,
    UpdateManifestError,
    UpdateMigrationError,
    UpdateNotFoundError,
    UpdateOperatorActionRequiredError,
    UpdatePathTraversalError,
    UpdatePlatformMismatchError,
    UpdateQuiescenceError,
    UpdateRollbackError,
    UpdateSchemaIncompatibleError,
    UpdateSecurityError,
    UpdateSignatureError,
    UpdateSwapError,
    UpdateVerificationError,
    UpdateZipBombError,
)
from kortex.engines.update.models import (
    UpdateApplyResponse,
    UpdateCancelRequest,
    UpdateCancelResponse,
    UpdateCheckRequest,
    UpdateCheckResponse,
    UpdateGetRequest,
    UpdateGetResponse,
    UpdateManifest,
    UpdateManifestCompatibility,
    UpdateManifestDatabase,
    UpdateManifestPackage,
    UpdateManifestVersion,
    UpdateStageRequest,
    UpdateStageResponse,
)


def test_update_engine_metadata() -> None:
    """Verify engine name and version constants."""
    assert UPDATE_ENGINE_NAME == "update"
    assert CURRENT_ENGINE_VERSION == "1.0.0"
    assert CURRENT_MANIFEST_FORMAT_VERSION == "1.0"
    assert CURRENT_JOURNAL_FORMAT_VERSION == "1.0"


def test_frozen_12_event_contract() -> None:
    """Verify that exactly 12 canonical events exist, with no renames, additions, or removals (CLARIFICATION 3)."""
    expected_events = [
        "kortex.update.checked",
        "kortex.update.manifest.verified",
        "kortex.update.staged",
        "kortex.update.safety_checkpoint.created",
        "kortex.update.quiesced",
        "kortex.update.migrated",
        "kortex.update.applied",
        "kortex.update.verified",
        "kortex.update.completed",
        "kortex.update.failed",
        "kortex.update.rolled_back",
        "kortex.update.operator_intervention_required",
    ]

    assert EVENT_UPDATE_CHECKED == "kortex.update.checked"
    assert EVENT_UPDATE_MANIFEST_VERIFIED == "kortex.update.manifest.verified"
    assert EVENT_UPDATE_STAGED == "kortex.update.staged"
    assert EVENT_UPDATE_SAFETY_CHECKPOINT_CREATED == "kortex.update.safety_checkpoint.created"
    assert EVENT_UPDATE_QUIESCED == "kortex.update.quiesced"
    assert EVENT_UPDATE_MIGRATED == "kortex.update.migrated"
    assert EVENT_UPDATE_APPLIED == "kortex.update.applied"
    assert EVENT_UPDATE_VERIFIED == "kortex.update.verified"
    assert EVENT_UPDATE_COMPLETED == "kortex.update.completed"
    assert EVENT_UPDATE_FAILED == "kortex.update.failed"
    assert EVENT_UPDATE_ROLLED_BACK == "kortex.update.rolled_back"
    assert EVENT_UPDATE_OPERATOR_INTERVENTION_REQUIRED == "kortex.update.operator_intervention_required"

    assert len(ALL_UPDATE_EVENTS) == 12
    assert sorted(ALL_UPDATE_EVENTS) == sorted(expected_events)


def test_capabilities_and_permissions() -> None:
    """Verify approved 6 capabilities and their required RBAC permissions."""
    expected_capabilities = [
        "kortex.update.check",
        "kortex.update.stage",
        "kortex.update.apply",
        "kortex.update.get",
        "kortex.update.cancel",
        "kortex.update.diagnostics.get",
    ]
    assert len(ALL_UPDATE_CAPABILITIES) == 6
    assert sorted(ALL_UPDATE_CAPABILITIES) == sorted(expected_capabilities)

    assert UPDATE_CAPABILITY_PERMISSIONS[CAPABILITY_UPDATE_CHECK] == PERMISSION_UPDATE_READ
    assert UPDATE_CAPABILITY_PERMISSIONS[CAPABILITY_UPDATE_STAGE] == PERMISSION_UPDATE_MANAGE
    assert UPDATE_CAPABILITY_PERMISSIONS[CAPABILITY_UPDATE_APPLY] == PERMISSION_UPDATE_MANAGE
    assert UPDATE_CAPABILITY_PERMISSIONS[CAPABILITY_UPDATE_GET] == PERMISSION_UPDATE_READ
    assert UPDATE_CAPABILITY_PERMISSIONS[CAPABILITY_UPDATE_CANCEL] == PERMISSION_UPDATE_MANAGE
    assert UPDATE_CAPABILITY_PERMISSIONS[CAPABILITY_UPDATE_DIAGNOSTICS_GET] == PERMISSION_UPDATE_READ


def test_lifecycle_and_journal_phases() -> None:
    """Verify all lifecycle states and write-ahead journal phases."""
    assert UpdateState.IDLE.value == "IDLE"
    assert UpdateState.CHECKING.value == "CHECKING"
    assert UpdateState.STAGING.value == "STAGING"
    assert UpdateState.STAGED.value == "STAGED"
    assert UpdateState.APPLYING.value == "APPLYING"
    assert UpdateState.COMPLETED.value == "COMPLETED"
    assert UpdateState.FAILED.value == "FAILED"
    assert UpdateState.ROLLED_BACK.value == "ROLLED_BACK"
    assert UpdateState.FAILED_NEEDS_OPERATOR.value == "FAILED_NEEDS_OPERATOR"

    # Exactly 15 journal phases
    assert len(UpdateJournalPhase) == 15
    assert UpdateJournalPhase.CREATED.value == "CREATED"
    assert UpdateJournalPhase.MANIFEST_VERIFIED.value == "MANIFEST_VERIFIED"
    assert UpdateJournalPhase.ARTIFACT_ACQUIRED.value == "ARTIFACT_ACQUIRED"
    assert UpdateJournalPhase.ARTIFACT_VERIFIED.value == "ARTIFACT_VERIFIED"
    assert UpdateJournalPhase.STAGED.value == "STAGED"
    assert UpdateJournalPhase.CHECKPOINT_CREATED.value == "CHECKPOINT_CREATED"
    assert UpdateJournalPhase.QUIESCED.value == "QUIESCED"
    assert UpdateJournalPhase.SCHEMA_MIGRATED.value == "SCHEMA_MIGRATED"
    assert UpdateJournalPhase.FILES_SWAPPED.value == "FILES_SWAPPED"
    assert UpdateJournalPhase.VERIFIED.value == "VERIFIED"
    assert UpdateJournalPhase.COMMITTED.value == "COMMITTED"
    assert UpdateJournalPhase.FAILED.value == "FAILED"
    assert UpdateJournalPhase.ROLLING_BACK.value == "ROLLING_BACK"
    assert UpdateJournalPhase.ROLLED_BACK.value == "ROLLED_BACK"
    assert UpdateJournalPhase.FAILED_NEEDS_OPERATOR.value == "FAILED_NEEDS_OPERATOR"


def test_exception_hierarchy() -> None:
    """Verify typed exceptions root at UpdateError."""
    exceptions = [
        UpdateSecurityError,
        UpdateAuthenticationError,
        UpdateAuthorizationError,
        UpdateManifestError,
        UpdateSignatureError,
        UpdateKeyNotFoundError,
        UpdateChecksumMismatchError,
        UpdateArchiveSecurityError,
        UpdatePathTraversalError,
        UpdateZipBombError,
        UpdateCompatibilityError,
        UpdateDowngradeError,
        UpdatePlatformMismatchError,
        UpdateSchemaIncompatibleError,
        UpdateDiskSpaceError,
        UpdateConcurrencyError,
        UpdateCheckpointError,
        UpdateQuiescenceError,
        UpdateMigrationError,
        UpdateSwapError,
        UpdateVerificationError,
        UpdateRollbackError,
        UpdateOperatorActionRequiredError,
        UpdateNotFoundError,
    ]
    for exc_cls in exceptions:
        assert issubclass(exc_cls, UpdateError)
        inst = exc_cls("test error")
        assert str(inst) == "test error"


def test_update_manifest_model() -> None:
    """Verify UpdateManifest model serialization and validation."""
    manifest = UpdateManifest(
        manifest_id="mf-001",
        created_at="2026-09-05T00:00:00Z",
        expires_at="2026-09-12T00:00:00Z",
        key_id="kortex-official-release-2026",
        signature="base64sig==",
        version=UpdateManifestVersion(
            target_version="0.2.0",
            min_supported_version="0.1.0",
            release_channel="stable",
        ),
        compatibility=UpdateManifestCompatibility(
            platforms=["windows", "linux", "darwin"],
            architectures=["x86_64", "arm64"],
            python_version_min="3.11",
        ),
        package=UpdateManifestPackage(
            filename="update-0.2.0.zip",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            size_bytes=1048576,
            uncompressed_bytes=2097152,
            file_count=50,
        ),
        database=UpdateManifestDatabase(
            requires_migration=True,
            target_revision="rev_002",
        ),
    )

    assert manifest.manifest_id == "mf-001"
    assert manifest.version.target_version == "0.2.0"
    assert manifest.package.filename == "update-0.2.0.zip"


def test_apply_response_runtime_activation_invariants() -> None:
    """Verify UpdateApplyResponse strictly separates filesystem update from runtime activation (CLARIFICATION 2)."""
    resp = UpdateApplyResponse(
        update_id="upd-test-01",
        target_version="0.2.0",
        status=UpdateState.COMPLETED,
        filesystem_updated=True,
        restart_required=True,
        runtime_activated=False,
        safety_checkpoint_id="bck-safe-123",
        applied_at="2026-09-05T00:00:00Z",
    )
    assert resp.filesystem_updated is True
    assert resp.restart_required is True
    assert resp.runtime_activated is False
    assert resp.safety_checkpoint_id == "bck-safe-123"


def test_request_response_models() -> None:
    """Verify other request and response models."""
    check_req = UpdateCheckRequest(channel="stable")
    assert check_req.channel == "stable"

    check_res = UpdateCheckResponse(
        update_available=True,
        current_version="0.1.0",
        target_version="0.2.0",
    )
    assert check_res.update_available is True

    manifest = UpdateManifest(
        manifest_id="mf-001",
        created_at="2026-09-05T00:00:00Z",
        expires_at="2026-09-12T00:00:00Z",
        key_id="k1",
        signature="s1",
        version=UpdateManifestVersion(target_version="0.2.0", min_supported_version="0.1.0"),
        package=UpdateManifestPackage(
            filename="upd.zip", sha256="abc", size_bytes=10, uncompressed_bytes=20, file_count=1
        ),
    )
    stage_req = UpdateStageRequest(manifest=manifest)
    assert stage_req.manifest.manifest_id == "mf-001"

    stage_res = UpdateStageResponse(
        update_id="upd-123",
        target_version="0.2.0",
        staging_path="/path/to/staged",
        staged_at="2026-09-05T00:00:00Z",
        sha256_verified=True,
    )
    assert stage_res.sha256_verified is True

    cancel_req = UpdateCancelRequest(update_id="upd-123")
    assert cancel_req.update_id == "upd-123"
    cancel_res = UpdateCancelResponse(update_id="upd-123", cancelled=True, purged_staging=True)
    assert cancel_res.cancelled is True

    get_req = UpdateGetRequest(update_id="upd-123")
    assert get_req.update_id == "upd-123"
    get_res = UpdateGetResponse(current_version="1.0.0", state=UpdateState.IDLE)
    assert get_res.current_version == "1.0.0"
    assert get_res.state == UpdateState.IDLE
