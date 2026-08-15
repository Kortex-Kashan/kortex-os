"""Unit tests for Document Recovery Manager (Milestone 6).

Target: 100% pass rate, 100% line coverage for recovery.py.
"""

from __future__ import annotations

import pytest

from kortex.engines.document.exceptions import DocumentRecoveryError
from kortex.engines.document.interfaces import IDocumentRecoveryProvider
from kortex.engines.document.recovery import (
    CheckpointState,
    DocumentRecoveryManager,
    FailureMetadata,
)


@pytest.mark.asyncio
async def test_document_recovery_manager_checkpoint_and_resume() -> None:
    """Test CheckpointState, DocumentRecoveryManager checkpointing, and resume."""
    recovery = DocumentRecoveryManager()
    assert isinstance(recovery, IDocumentRecoveryProvider)

    # Empty inputs raise DocumentRecoveryError
    with pytest.raises(DocumentRecoveryError, match="request_id cannot be empty"):
        await recovery.checkpoint("", "s1", b"state")

    with pytest.raises(DocumentRecoveryError, match="stage_id cannot be empty"):
        await recovery.checkpoint("req-1", "", b"state")

    # Checkpoint creation
    chk_id1 = await recovery.checkpoint("req-1", "stage-1", b"[STAGE_1_STATE]")
    assert chk_id1 is not None

    chk_id2 = await recovery.checkpoint("req-1", "stage-2", b"[STAGE_2_STATE]")
    assert chk_id2 is not None

    checkpoints = await recovery.get_checkpoints("req-1")
    assert len(checkpoints) == 2
    assert isinstance(checkpoints[0], CheckpointState)
    assert checkpoints[0].stage_id == "stage-1"
    assert checkpoints[1].stage_id == "stage-2"

    # Resume retrieves last valid checkpoint
    last_chk = await recovery.resume("req-1")
    assert last_chk is not None
    assert last_chk.stage_id == "stage-2"
    assert last_chk.state_data == b"[STAGE_2_STATE]"

    # Resume missing request_id
    assert await recovery.resume("req-missing") is None
    assert await recovery.resume("") is None


@pytest.mark.asyncio
async def test_document_recovery_rollback_and_failures() -> None:
    """Test FailureMetadata recording, retries, and rollback stacks."""
    recovery = DocumentRecoveryManager()

    # Record failures
    f1 = await recovery.record_failure(
        request_id="req-2",
        stage_id="stage-ocr",
        adapter_id="kortex.adapter.ocr",
        error_code="OCR_TIMEOUT",
        stack_trace_snippet="TimeoutError: OCR process timed out",
    )
    assert isinstance(f1, FailureMetadata)
    assert f1.adapter_id == "kortex.adapter.ocr"

    failures = await recovery.get_failures("req-2")
    assert len(failures) == 1
    assert failures[0].error_code == "OCR_TIMEOUT"

    # Retry attempts
    can_retry1 = await recovery.retry_stage("req-2", "stage-ocr", max_retries=3)
    assert can_retry1 is True

    # Record 2 more failures for stage-ocr
    await recovery.record_failure("req-2", "stage-ocr", "a", "ERR", "stack")
    await recovery.record_failure("req-2", "stage-ocr", "a", "ERR", "stack")

    can_retry2 = await recovery.retry_stage("req-2", "stage-ocr", max_retries=3)
    assert can_retry2 is False

    # Retry with invalid inputs
    assert await recovery.retry_stage("", "stage-ocr") is False
    assert await recovery.retry_stage("req-2", "", max_retries=0) is False

    # Checkpoint and rollback
    await recovery.checkpoint("req-2", "stage-1", b"state")
    assert len(await recovery.get_checkpoints("req-2")) == 1

    # Rollback clears checkpoints and compensation stack
    rolled_back = await recovery.rollback("req-2")
    assert rolled_back is True

    assert len(await recovery.get_checkpoints("req-2")) == 0

    # Rollback non-existent or empty request_id
    assert await recovery.rollback("req-2") is False
    assert await recovery.rollback("") is False


def test_document_recovery_calculate_backoff() -> None:
    """Test exponential backoff delay calculations."""
    recovery = DocumentRecoveryManager()

    # Attempt 1 -> base delay
    assert recovery.calculate_backoff(attempt=1, backoff_factor=2.0, base_delay=0.01) == 0.01
    assert recovery.calculate_backoff(attempt=0, backoff_factor=2.0, base_delay=0.01) == 0.01

    # Attempt 2 -> base * 2.0^1 = 0.02
    assert pytest.approx(recovery.calculate_backoff(attempt=2, backoff_factor=2.0, base_delay=0.01)) == 0.02

    # Attempt 3 -> base * 2.0^2 = 0.04
    assert pytest.approx(recovery.calculate_backoff(attempt=3, backoff_factor=2.0, base_delay=0.01)) == 0.04

    # Default parameters
    assert recovery.calculate_backoff(attempt=1) == 0.001
    assert pytest.approx(recovery.calculate_backoff(attempt=2)) == 0.0015


@pytest.mark.asyncio
async def test_document_engine_recovery_manager_di() -> None:
    """Test DocumentEngine constructor injection and properties for recovery_manager."""
    from kortex.engines.document.engine import DocumentEngine

    # Default recovery manager
    engine_default = DocumentEngine()
    assert isinstance(engine_default.recovery_manager, DocumentRecoveryManager)
    assert engine_default.pipeline_executor.recovery_manager is engine_default.recovery_manager

    # Custom recovery manager
    custom_recovery = DocumentRecoveryManager()
    engine_custom = DocumentEngine(recovery_manager=custom_recovery)
    assert engine_custom.recovery_manager is custom_recovery
    assert engine_custom.pipeline_executor.recovery_manager is custom_recovery
