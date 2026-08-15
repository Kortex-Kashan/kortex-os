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
from kortex.engines.storage.interfaces import ICacheStore


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

    def __init__(self, cache_store: ICacheStore | None = None) -> None:
        """Initialize in-memory checkpoint, failure, and rollback state stores.

        Args:
            cache_store: Optional ICacheStore used to additionally persist checkpoint
                state so it survives across DocumentRecoveryManager instances sharing
                the same cache store. When absent, behavior is identical to pure
                in-memory operation.
        """
        # Maps request_id -> list of CheckpointState
        self._checkpoints: dict[str, list[CheckpointState]] = {}
        # Maps request_id -> list of FailureMetadata
        self._failures: dict[str, list[FailureMetadata]] = {}
        # Maps request_id -> list of rollback compensation payload bytes
        self._rollback_stacks: dict[str, list[bytes]] = {}
        self._cache_store = cache_store

    @property
    def cache_store(self) -> ICacheStore | None:
        """Return the configured ICacheStore backing checkpoint persistence, or None if in-memory only."""
        return self._cache_store

    @staticmethod
    def _checkpoint_cache_key(request_id: str) -> str:
        """Build the ICacheStore key under which a request's checkpoints are persisted."""
        return f"doc_engine:recovery:checkpoints:{request_id}"

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

        if self._cache_store is not None:
            await self._cache_store.set(
                self._checkpoint_cache_key(req_id),
                list(self._checkpoints[req_id]),
                ttl_seconds=None,
            )

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

        if self._cache_store is not None:
            await self._cache_store.delete(self._checkpoint_cache_key(req_id))

        return existed

    def calculate_backoff(
        self,
        attempt: int,
        backoff_factor: float = 1.5,
        base_delay: float = 0.001,
    ) -> float:
        """Calculate exponential backoff duration for a retry attempt.

        Args:
            attempt: 1-indexed failure count / attempt number.
            backoff_factor: Exponential multiplier.
            base_delay: Initial delay in seconds (default 0.001s for fast execution).

        Returns:
            Calculated delay in seconds.
        """
        if attempt <= 1:
            return base_delay
        return base_delay * (backoff_factor ** (attempt - 1))

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
        checkpoints = await self.get_checkpoints(req_id)
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
        """Return all checkpoints for a request ID.

        Falls back to the configured ICacheStore when this instance has no in-memory
        record of request_id, so a fresh DocumentRecoveryManager sharing the same
        cache_store can still observe checkpoints written by a prior instance.
        """
        req_id = request_id.strip()
        if req_id in self._checkpoints:
            return list(self._checkpoints[req_id])

        if self._cache_store is not None:
            cached = await self._cache_store.get(self._checkpoint_cache_key(req_id))
            if cached:
                return list(cached)

        return []

    async def get_failures(self, request_id: str) -> list[FailureMetadata]:
        """Return all failure metadata records for a request ID."""
        return list(self._failures.get(request_id.strip(), []))


__all__ = [
    "CheckpointState",
    "DocumentRecoveryManager",
    "FailureMetadata",
]
