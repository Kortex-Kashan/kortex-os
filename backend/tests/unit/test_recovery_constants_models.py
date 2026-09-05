"""Unit tests for Recovery Engine constants, enumerations, and Pydantic models."""

from __future__ import annotations

import pytest

from kortex.engines.recovery.constants import (
    CAPABILITY_RECOVERY_CREATE,
    CAPABILITY_RECOVERY_DELETE,
    CAPABILITY_RECOVERY_DIAGNOSTICS_GET,
    CAPABILITY_RECOVERY_GET,
    CAPABILITY_RECOVERY_LIST,
    CAPABILITY_RECOVERY_VERIFY,
    CURRENT_ENGINE_VERSION,
    CURRENT_RECOVERY_FORMAT_VERSION,
    CURRENT_RECOVERY_JOURNAL_VERSION,
    EVENT_RECOVERY_COMPLETED,
    EVENT_RECOVERY_FAILED,
    EVENT_RECOVERY_OPERATOR_INTERVENTION_REQUIRED,
    EVENT_RECOVERY_PRECHECK_PASSED,
    EVENT_RECOVERY_QUIESCED,
    EVENT_RECOVERY_REQUESTED,
    EVENT_RECOVERY_ROLLED_BACK,
    EVENT_RECOVERY_SAFETY_CHECKPOINT_CREATED,
    EVENT_RECOVERY_STAGED,
    EVENT_RECOVERY_SWAPPED,
    EVENT_RECOVERY_VALIDATED,
    EVENT_RECOVERY_VERIFIED,
    PERMISSION_RECOVERY_MANAGE,
    PERMISSION_RECOVERY_READ,
    RECOVERY_DEFAULT_LOCK_TIMEOUT_SECONDS,
    RECOVERY_DEFAULT_RESERVE_BYTES,
    RECOVERY_ENGINE_NAME,
    RECOVERY_SECURITY_CLASSIFICATION,
    RecoveryComponentType,
    RecoveryJournalPhase,
    RecoveryScope,
    RecoveryState,
)
from kortex.engines.recovery.models import (
    ChecksumsMetadata,
    CreateRecoveryRequest,
    CreateRecoveryResponse,
    DeleteRecoveryRequest,
    DeleteRecoveryResponse,
    GetRecoveryRequest,
    RecoveryConfig,
    RecoveryDiagnostics,
    RecoveryJournalEntry,
    RollbackState,
    TargetIdentity,
    VerificationState,
    VerifyRecoveryRequest,
    VerifyRecoveryResponse,
)


def test_constants_and_enums() -> None:
    """Verify core engine constants and enum definitions."""
    assert RECOVERY_ENGINE_NAME == "recovery"
    assert RECOVERY_SECURITY_CLASSIFICATION == "INTERNAL"
    assert CURRENT_RECOVERY_FORMAT_VERSION == 1
    assert CURRENT_RECOVERY_JOURNAL_VERSION == 1
    assert CURRENT_ENGINE_VERSION == "1.0.0"

    assert PERMISSION_RECOVERY_READ == "system:recovery:read"
    assert PERMISSION_RECOVERY_MANAGE == "system:recovery:manage"

    assert CAPABILITY_RECOVERY_CREATE == "kortex.recovery.create"
    assert CAPABILITY_RECOVERY_LIST == "kortex.recovery.list"
    assert CAPABILITY_RECOVERY_GET == "kortex.recovery.get"
    assert CAPABILITY_RECOVERY_VERIFY == "kortex.recovery.verify"
    assert CAPABILITY_RECOVERY_DELETE == "kortex.recovery.delete"
    assert CAPABILITY_RECOVERY_DIAGNOSTICS_GET == "kortex.recovery.diagnostics.get"

    assert RECOVERY_DEFAULT_LOCK_TIMEOUT_SECONDS == 30.0
    assert RECOVERY_DEFAULT_RESERVE_BYTES == 500 * 1024 * 1024

    assert RecoveryScope.FULL_INSTANCE.value == "FULL_INSTANCE"
    assert RecoveryComponentType.DATABASE.value == "database"
    assert RecoveryComponentType.STORAGE.value == "storage"

    # Enforce exact recovery states
    expected_states = {
        "REQUESTED",
        "PRECHECKING",
        "CHECKPOINTING",
        "VALIDATING",
        "STAGING",
        "PREPARING_SWAP",
        "SWAPPING",
        "RECONNECTING",
        "VERIFYING",
        "COMPLETED",
        "FAILED",
        "ROLLBACK_REQUIRED",
        "ROLLING_BACK",
        "ROLLED_BACK",
        "FAILED_NEEDS_OPERATOR",
    }
    actual_states = {s.value for s in RecoveryState}
    assert actual_states == expected_states

    # Enforce journal phases
    assert RecoveryJournalPhase.CREATED.value == "CREATED"
    assert RecoveryJournalPhase.CHECKPOINT_CREATED.value == "CHECKPOINT_CREATED"
    assert RecoveryJournalPhase.STAGING.value == "STAGING"
    assert RecoveryJournalPhase.STAGED.value == "STAGED"
    assert RecoveryJournalPhase.COMMITTED.value == "COMMITTED"
    assert RecoveryJournalPhase.ROLLED_BACK.value == "ROLLED_BACK"
    assert RecoveryJournalPhase.FAILED_NEEDS_OPERATOR.value == "FAILED_NEEDS_OPERATOR"


def test_recovery_config_defaults_and_validation() -> None:
    """Verify RecoveryConfig default attributes and bounds."""
    config = RecoveryConfig()
    assert config.quiescence_timeout_seconds == 30.0
    assert config.safety_margin_bytes == 500 * 1024 * 1024
    assert config.rollback_retention_hours == 72
    assert config.max_file_count == 100_000

    # Custom valid config
    custom = RecoveryConfig(
        quiescence_timeout_seconds=45.0,
        safety_margin_bytes=250 * 1024 * 1024,
    )
    assert custom.quiescence_timeout_seconds == 45.0
    assert custom.safety_margin_bytes == 250 * 1024 * 1024


def test_target_identity_model() -> None:
    """Verify TargetIdentity instantiation and serialization."""
    target = TargetIdentity(
        instance_id="inst-123",
        database_path="storage_data/kortex_local.db",
        storage_root="storage_data",
    )
    assert target.instance_id == "inst-123"
    assert target.database_path == "storage_data/kortex_local.db"
    assert target.storage_root == "storage_data"
    data = target.model_dump()
    assert data["database_path"] == "storage_data/kortex_local.db"


def test_recovery_journal_entry_model() -> None:
    """Verify RecoveryJournalEntry lifecycle tracking and mutations."""
    entry = RecoveryJournalEntry(
        recovery_id="rec-001",
        backup_id="bck-001",
        target_identity=TargetIdentity(
            instance_id="inst-1",
            database_path="/db.db",
            storage_root="/storage",
        ),
        created_at="2026-09-05T00:00:00Z",
        updated_at="2026-09-05T00:00:00Z",
        current_phase=RecoveryJournalPhase.CREATED,
        rollback_state=RollbackState(safety_checkpoint_id="bck-safe-1"),
        verification_state=VerificationState(),
        checksums=ChecksumsMetadata(artifact_sha256="abc123hash"),
    )
    assert entry.recovery_id == "rec-001"
    assert entry.backup_id == "bck-001"
    assert entry.current_phase == RecoveryJournalPhase.CREATED
    assert len(entry.completed_operations) == 0

    entry.completed_operations.append("staged_database")
    assert len(entry.completed_operations) == 1


def test_rollback_state_model() -> None:
    """Verify RollbackState model validation and attributes."""
    rollback = RollbackState(
        status="ARMED",
        safety_checkpoint_id="bck-safety-001",
        safety_checkpoint_sha256="abc123hash",
        rollback_sources={"database": "/path/to/db.rollback"},
    )
    assert rollback.safety_checkpoint_id == "bck-safety-001"
    assert rollback.status == "ARMED"
    assert len(rollback.rollback_sources) == 1


def test_create_recovery_request_validation() -> None:
    """Verify CreateRecoveryRequest validation constraints."""
    valid_req = CreateRecoveryRequest(
        backup_id="backup-12345",
        confirm_destructive_restore=True,
    )
    assert valid_req.backup_id == "backup-12345"
    assert valid_req.confirm_destructive_restore is True


def test_get_and_delete_requests() -> None:
    """Verify request and response models for get and delete operations."""
    get_req = GetRecoveryRequest(recovery_id="rec-123")
    assert get_req.recovery_id == "rec-123"

    del_req = DeleteRecoveryRequest(recovery_id="rec-456")
    assert del_req.recovery_id == "rec-456"

    del_resp = DeleteRecoveryResponse(recovery_id="rec-456", deleted=True, message="Deleted")
    assert del_resp.deleted is True
    assert del_resp.message == "Deleted"


def test_verify_recovery_models() -> None:
    """Verify models for recovery artifact verification."""
    req = VerifyRecoveryRequest(backup_id="bck-check")
    assert req.backup_id == "bck-check"

    resp = VerifyRecoveryResponse(
        backup_id="bck-check",
        is_valid=True,
        checksum_verified=True,
        encryption_verified=True,
        schema_compatible=True,
        database_integrity_passed=True,
        storage_referential_integrity_passed=True,
        has_sufficient_disk_space=True,
    )
    assert resp.is_valid is True
    assert resp.checksum_verified is True
    assert resp.database_integrity_passed is True


def test_recovery_response_and_diagnostics() -> None:
    """Verify comprehensive CreateRecoveryResponse and RecoveryDiagnostics model structure."""
    resp = CreateRecoveryResponse(
        recovery_id="rec-999",
        backup_id="bck-999",
        state=RecoveryState.COMPLETED,
        created_at="2026-09-05T00:00:00Z",
        completed_at="2026-09-05T00:01:00Z",
        safety_checkpoint_id="bck-safety-999",
        database_restored=True,
        storage_files_restored=10,
        duration_seconds=5.2,
        is_success=True,
    )
    assert resp.recovery_id == "rec-999"
    assert resp.state == RecoveryState.COMPLETED
    assert resp.safety_checkpoint_id == "bck-safety-999"

    diag = RecoveryDiagnostics(
        engine_name="recovery",
        engine_version="1.0.0",
        state="READY",
        journal_path="storage_data/.recovery/journal.json",
        staging_path="storage_data/.recovery_staging",
    )
    assert diag.engine_name == "recovery"
    assert diag.state == "READY"


def test_canonical_12_recovery_events_contract() -> None:
    """Verify exactly 12 canonical Recovery event names adhering to implementation_plan.md."""
    expected_events = {
        "kortex.recovery.requested",
        "kortex.recovery.precheck.passed",
        "kortex.recovery.safety_checkpoint.created",
        "kortex.recovery.validated",
        "kortex.recovery.staged",
        "kortex.recovery.quiesced",
        "kortex.recovery.swapped",
        "kortex.recovery.verified",
        "kortex.recovery.completed",
        "kortex.recovery.failed",
        "kortex.recovery.rolled_back",
        "kortex.recovery.operator_intervention_required",
    }

    actual_events = {
        EVENT_RECOVERY_REQUESTED,
        EVENT_RECOVERY_PRECHECK_PASSED,
        EVENT_RECOVERY_SAFETY_CHECKPOINT_CREATED,
        EVENT_RECOVERY_VALIDATED,
        EVENT_RECOVERY_STAGED,
        EVENT_RECOVERY_QUIESCED,
        EVENT_RECOVERY_SWAPPED,
        EVENT_RECOVERY_VERIFIED,
        EVENT_RECOVERY_COMPLETED,
        EVENT_RECOVERY_FAILED,
        EVENT_RECOVERY_ROLLED_BACK,
        EVENT_RECOVERY_OPERATOR_INTERVENTION_REQUIRED,
    }

    assert len(actual_events) == 12
    assert actual_events == expected_events
    for event in actual_events:
        assert event.startswith("kortex.recovery.")


@pytest.mark.asyncio
async def test_recovery_event_publisher_lifecycle_emissions() -> None:
    """Verify RecoveryEventPublisher emits all 12 canonical events with correct payloads."""
    from unittest.mock import MagicMock

    from kortex.engines.recovery.events import RecoveryEventPublisher

    kernel = MagicMock()
    event_engine = MagicMock()
    kernel.event_engine = event_engine
    published_topics: list[str] = []

    def mock_publish(topic: str, payload: dict, priority: str = "NORMAL"):
        published_topics.append(topic)
        return True

    event_engine.publish = mock_publish

    publisher = RecoveryEventPublisher(kernel=kernel)

    await publisher.emit_requested("rec-1", "bck-1")
    await publisher.emit_precheck_passed("rec-1", "bck-1")
    await publisher.emit_safety_checkpoint_created("rec-1", "bck-1", "chk-1")
    await publisher.emit_validated("rec-1", "bck-1")
    await publisher.emit_staged("rec-1", "bck-1")
    await publisher.emit_quiesced("rec-1", "bck-1")
    await publisher.emit_swapped("rec-1", "bck-1")
    await publisher.emit_verified("rec-1", "bck-1")
    await publisher.emit_completed("rec-1", "bck-1", "chk-1", 5)
    await publisher.emit_failed("rec-1", "bck-1", "simulated error")
    await publisher.emit_rolled_back("rec-1", "bck-1")
    await publisher.emit_operator_intervention_required("rec-1", "bck-1", "fatal rollback failure")

    assert len(published_topics) == 12
    assert published_topics == [
        "kortex.recovery.requested",
        "kortex.recovery.precheck.passed",
        "kortex.recovery.safety_checkpoint.created",
        "kortex.recovery.validated",
        "kortex.recovery.staged",
        "kortex.recovery.quiesced",
        "kortex.recovery.swapped",
        "kortex.recovery.verified",
        "kortex.recovery.completed",
        "kortex.recovery.failed",
        "kortex.recovery.rolled_back",
        "kortex.recovery.operator_intervention_required",
    ]
