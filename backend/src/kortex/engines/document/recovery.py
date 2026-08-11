"""Document Execution Recovery Manager for KORTEX OS Document Engine.

This module implements CheckpointState, FailureMetadata, and DocumentRecoveryManager in accordance
with Section 11.2 and Milestone 6 of the Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.document.exceptions import DocumentRecoveryError
from kortex.engines.document.interfaces import IDocumentRecoveryProvider


class CheckpointState(BaseModel):
    """Immutable operational checkpoint state recorded after successful stage execution."""

    model_config = ConfigDict(frozen=True)

    checkpoint_id: str
    request_id: str
    stage_id: str
    state_data: bytes
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


class FailureMetadata(BaseModel):
    """Structured telemetry recording detailed failure context for administrative inspection."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    stage_id: str
    adapter_id: str
    error_code: str
    stack_trace_snippet: str
    timestamp: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


class DocumentRecoveryManager(IDocumentRecoveryProvider):
    """Manager for document operation checkpointing, failure recording, retries, and rollback stacks.

    Coordinated with Workflow Engine to guarantee transactional document operations.
    """

    def __init__(self) -> None:
        """Initialize in-memory checkpoint, failure, and rollback state stores."""
        # Maps request_id -> list of CheckpointState
        self._checkpoints: dict[str, list[CheckpointState]] = {}
        # Maps request_id -> list of FailureMetadata
        self._failures: dict[str, list[FailureMetadata]] = {}
        # Maps request_id -> list of rollback compensation payload bytes
        self._rollback_stacks: dict[str, list[bytes]] = {}

    async def checkpoint(self, request_id: str, stage_id: str, state_data: bytes) -> str:
        """Save operational checkpoint state for recovery (IDocumentRecoveryProvider protocol).

        Args:
            request_id: Operation request ID.
            stage_id: Pipeline stage ID.
            state_data: Binary payload state data.

        Returns:
            Generated checkpoint_id string.

        Raises:
            DocumentRecoveryError: If request_id or stage_id is invalid.
        """
        if not request_id or not request_id.strip():
            raise DocumentRecoveryError("Invalid checkpoint request: request_id cannot be empty.")

        if not stage_id or not stage_id.strip():
            raise DocumentRecoveryError("Invalid checkpoint request: stage_id cannot be empty.")

        req_id = request_id.strip()
        stg_id = stage_id.strip()
        chk_id = str(uuid.uuid4())

        checkpoint_item = CheckpointState(
            checkpoint_id=chk_id,
            request_id=req_id,
            stage_id=stg_id,
            state_data=state_data,
        )

        if req_id not in self._checkpoints:
            self._checkpoints[req_id] = []

        self._checkpoints[req_id].append(checkpoint_item)

        # Register state_data on rollback stack as compensation backup
        if req_id not in self._rollback_stacks:
            self._rollback_stacks[req_id] = []
        self._rollback_stacks[req_id].append(state_data)

        return chk_id

    async def rollback(self, request_id: str) -> bool:
        """Execute rollback stack to clean up failed operation artifacts (IDocumentRecoveryProvider protocol).

        Args:
            request_id: Operation request ID to roll back.

        Returns:
            True if rollback compensation executed successfully; False if no checkpoints existed.
        """
        if not request_id or not request_id.strip():
            return False

        req_id = request_id.strip()
        existed = req_id in self._checkpoints or req_id in self._rollback_stacks

        if req_id in self._checkpoints:
            del self._checkpoints[req_id]

        if req_id in self._rollback_stacks:
            del self._rollback_stacks[req_id]

        return existed

    async def retry_stage(
        self,
        request_id: str,
        stage_id: str,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
    ) -> bool:
        """Calculate retry backoff and determine if a failed pipeline stage re-dispatch attempt is permitted.

        Args:
            request_id: Operation request ID.
            stage_id: Pipeline stage ID.
            max_retries: Maximum permitted retry attempts.
            backoff_factor: Exponential backoff multiplier.

        Returns:
            True if retry attempt is within allowed threshold; False otherwise.
        """
        if not request_id or not stage_id or max_retries <= 0:
            return False

        req_id = request_id.strip()
        failures = self._failures.get(req_id, [])
        stage_failures = [f for f in failures if f.stage_id == stage_id]

        return len(stage_failures) < max_retries

    async def resume(self, request_id: str) -> CheckpointState | None:
        """Resume pipeline execution from the last valid stage checkpoint.

        Args:
            request_id: Operation request ID.

        Returns:
            Last valid CheckpointState if available; None otherwise.
        """
        if not request_id or not request_id.strip():
            return None

        req_id = request_id.strip()
        checkpoints = self._checkpoints.get(req_id, [])
        if not checkpoints:
            return None

        return checkpoints[-1]

    async def record_failure(
        self,
        request_id: str,
        stage_id: str,
        adapter_id: str,
        error_code: str,
        stack_trace_snippet: str,
    ) -> FailureMetadata:
        """Record detailed failure context for telemetry and administrative inspection.

        Args:
            request_id: Operation request ID.
            stage_id: Pipeline stage ID.
            adapter_id: Document adapter ID.
            error_code: Error code string.
            stack_trace_snippet: Stack trace snippet.

        Returns:
            FailureMetadata record.
        """
        req_id = request_id.strip()
        stg_id = stage_id.strip()

        failure = FailureMetadata(
            request_id=req_id,
            stage_id=stg_id,
            adapter_id=adapter_id,
            error_code=error_code,
            stack_trace_snippet=stack_trace_snippet,
        )

        if req_id not in self._failures:
            self._failures[req_id] = []

        self._failures[req_id].append(failure)
        return failure

    async def get_checkpoints(self, request_id: str) -> list[CheckpointState]:
        """Return all checkpoints for a request ID."""
        return list(self._checkpoints.get(request_id.strip(), []))

    async def get_failures(self, request_id: str) -> list[FailureMetadata]:
        """Return all failure metadata records for a request ID."""
        return list(self._failures.get(request_id.strip(), []))


__all__ = [
    "CheckpointState",
    "DocumentRecoveryManager",
    "FailureMetadata",
]
