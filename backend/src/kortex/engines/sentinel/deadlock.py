"""KORTEX Sentinel Engine — Deadlock & Starvation Detector.

Provides non-invasive observation of:
- Event-loop scheduling lag and responsiveness
- Tracked long-running operations against explicit thresholds
- Deterministic detection of event loop starvation and suspected deadlocks
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from kortex.engines.sentinel.constants import (
    DEFAULT_LOOP_LAG_THRESHOLD_MS,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
)
from kortex.engines.sentinel.models import DeadlockReport, StalledOperation

logger = logging.getLogger("kortex.engine.sentinel.deadlock")


class OperationTracker:
    """Non-invasive tracker for active operations with expected completion thresholds."""

    def __init__(self, default_threshold_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS) -> None:
        self._default_threshold = default_threshold_seconds
        self._active_operations: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def register_operation(
        self,
        name: str,
        threshold_seconds: float | None = None,
        details: dict[str, Any] | None = None,
        operation_id: str | None = None,
    ) -> str:
        """Register the start of an operation."""
        op_id = operation_id or str(uuid.uuid4())
        threshold = threshold_seconds if threshold_seconds is not None else self._default_threshold
        with self._lock:
            self._active_operations[op_id] = {
                "name": name,
                "start_monotonic": time.monotonic(),
                "start_time": datetime.now(UTC),
                "threshold": threshold,
                "details": details or {},
            }
        return op_id

    def finish_operation(self, operation_id: str) -> bool:
        """Mark an operation as completed and remove from tracking."""
        with self._lock:
            return self._active_operations.pop(operation_id, None) is not None

    @asynccontextmanager
    async def track_operation(
        self,
        name: str,
        threshold_seconds: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Async context manager to automatically track an operation duration."""
        op_id = self.register_operation(name, threshold_seconds, details)
        try:
            yield op_id
        finally:
            self.finish_operation(op_id)

    def get_stalled_operations(
        self,
        override_threshold_seconds: float | None = None,
        now_monotonic: float | None = None,
    ) -> list[StalledOperation]:
        """Identify currently active operations whose duration exceeds their threshold."""
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        stalled: list[StalledOperation] = []

        with self._lock:
            items = list(self._active_operations.items())

        for op_id, op_data in items:
            elapsed = max(0.0, now - op_data["start_monotonic"])
            threshold = override_threshold_seconds if override_threshold_seconds is not None else op_data["threshold"]
            if elapsed > threshold:
                stalled.append(
                    StalledOperation(
                        operation_id=op_id,
                        name=op_data["name"],
                        duration_seconds=round(elapsed, 3),
                        threshold_seconds=threshold,
                        details=dict(op_data["details"]),
                    )
                )

        return stalled

    @property
    def active_count(self) -> int:
        """Count of currently active tracked operations."""
        with self._lock:
            return len(self._active_operations)


class DeadlockDetector:
    """Non-invasive inspector for event loop lag, starvation, and deadlock suspicion."""

    def __init__(
        self,
        loop_lag_threshold_ms: float = DEFAULT_LOOP_LAG_THRESHOLD_MS,
        operation_timeout_threshold_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        self._loop_lag_threshold_ms = loop_lag_threshold_ms
        self._tracker = OperationTracker(default_threshold_seconds=operation_timeout_threshold_seconds)

    @property
    def tracker(self) -> OperationTracker:
        """Access the internal operation tracker."""
        return self._tracker

    async def measure_loop_lag(self) -> float:
        """Measure event loop scheduling latency in milliseconds."""
        start = time.perf_counter()
        # Schedule an immediate yield back to the event loop
        await asyncio.sleep(0)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return max(0.0, elapsed_ms)

    async def inspect_deadlocks(
        self,
        threshold_seconds: float | None = None,
    ) -> DeadlockReport:
        """Inspect event loop responsiveness and stalled operations.

        Deterministic classification:
        - EVENT_LOOP_STARVATION: event-loop scheduling latency exceeds threshold or operations exceed timeout.
        - DEADLOCK_SUSPECTED: high event-loop lag combined with multiple concurrently stalled operations.
        """
        loop_lag_ms = await self.measure_loop_lag()
        stalled = self._tracker.get_stalled_operations(override_threshold_seconds=threshold_seconds)

        lag_exceeded = loop_lag_ms >= self._loop_lag_threshold_ms
        has_stalled = len(stalled) > 0

        starvation_detected = lag_exceeded or has_stalled

        # Conservative, non-fabricated condition:
        # High lag + at least two concurrent stalled operations suggests deadlock
        deadlock_suspected = lag_exceeded and len(stalled) >= 2

        return DeadlockReport(
            deadlock_suspected=deadlock_suspected,
            starvation_detected=starvation_detected,
            loop_lag_ms=round(loop_lag_ms, 2),
            stalled_operations=stalled,
            active_operations_count=self._tracker.active_count,
            timestamp=datetime.now(UTC),
        )
