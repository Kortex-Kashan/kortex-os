"""KORTEX Sentinel Engine — Incident Store & Circuit Breaker.

Provides bounded in-memory storage of recent diagnostic incidents,
crash-loop tracking, and recovery-request emission circuit breaking.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from kortex.engines.sentinel.constants import (
    DEFAULT_CRASH_LOOP_THRESHOLD,
    DEFAULT_CRASH_LOOP_WINDOW_SECONDS,
    DEFAULT_RECOVERY_COOLDOWN_SECONDS,
    DEFAULT_RING_BUFFER_SIZE,
)
from kortex.engines.sentinel.models import FailureClass, SentinelStatus

logger = logging.getLogger("kortex.engine.sentinel.incident")


@dataclass
class IncidentRecord:
    """Bounded in-memory representation of an anomaly or failure episode."""

    incident_id: str
    subsystem: str
    failure_class: str
    health_status: str
    message: str
    timestamp: str
    monotonic_time: float
    details: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None


class IncidentStore:
    """Thread-safe bounded in-memory store for incidents and crash-loop detection."""

    def __init__(
        self,
        max_size: int = DEFAULT_RING_BUFFER_SIZE,
        crash_loop_threshold: int = DEFAULT_CRASH_LOOP_THRESHOLD,
        crash_loop_window_seconds: float = DEFAULT_CRASH_LOOP_WINDOW_SECONDS,
        recovery_cooldown_seconds: float = DEFAULT_RECOVERY_COOLDOWN_SECONDS,
    ) -> None:
        self._max_size = max_size
        self._crash_loop_threshold = crash_loop_threshold
        self._crash_loop_window_seconds = crash_loop_window_seconds
        self._recovery_cooldown_seconds = recovery_cooldown_seconds

        self._ring_buffer: deque[IncidentRecord] = deque(maxlen=max_size)
        # subsystem -> deque of failure timestamps (monotonic)
        self._failure_history: dict[str, deque[float]] = {}
        # subsystem -> timestamp of last recovery request (monotonic)
        self._last_recovery_request_at: dict[str, float] = {}
        # subsystem -> boolean indicating whether crash loop event was emitted
        self._crash_loop_emitted: dict[str, bool] = {}
        self._lock = threading.Lock()

    def record_incident(
        self,
        subsystem: str,
        failure_class: FailureClass | str,
        health_status: SentinelStatus | str,
        message: str,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        timestamp_monotonic: float | None = None,
        track_in_failure_history: bool = True,
    ) -> IncidentRecord:
        """Record a new incident into the bounded ring buffer."""
        now_mono = timestamp_monotonic if timestamp_monotonic is not None else time.monotonic()
        now_utc = datetime.now(UTC).isoformat()
        f_class = failure_class.value if isinstance(failure_class, FailureClass) else str(failure_class)
        h_status = health_status.value if isinstance(health_status, SentinelStatus) else str(health_status)

        record = IncidentRecord(
            incident_id=str(uuid.uuid4()),
            subsystem=subsystem,
            failure_class=f_class,
            health_status=h_status,
            message=message,
            timestamp=now_utc,
            monotonic_time=now_mono,
            details=details or {},
            correlation_id=correlation_id,
        )

        with self._lock:
            self._ring_buffer.append(record)
            if (
                h_status == SentinelStatus.FAILED.value
                and track_in_failure_history
                and f_class != FailureClass.CRASH_LOOP.value
            ):
                if subsystem not in self._failure_history:
                    self._failure_history[subsystem] = deque(maxlen=self._crash_loop_threshold * 2)
                self._failure_history[subsystem].append(now_mono)

        logger.info(
            "Sentinel incident recorded: subsystem=%s class=%s status=%s msg=%s",
            subsystem,
            f_class,
            h_status,
            message,
        )
        return record

    def check_crash_loop(
        self,
        subsystem: str,
        now_monotonic: float | None = None,
    ) -> tuple[bool, int]:
        """Check if a subsystem has entered a crash-loop.

        Returns (is_crash_loop, failure_count_in_window).
        """
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        with self._lock:
            history = self._failure_history.get(subsystem)
            if not history:
                return False, 0

            window_start = now - self._crash_loop_window_seconds
            failures_in_window = [t for t in history if t >= window_start]
            count = len(failures_in_window)
            is_crash = count >= self._crash_loop_threshold

            return is_crash, count

    def should_emit_crash_loop_event(
        self,
        subsystem: str,
        now_monotonic: float | None = None,
    ) -> bool:
        """Determine if a crash-loop event should be emitted (avoiding event storms)."""
        is_crash, _ = self.check_crash_loop(subsystem, now_monotonic)
        if not is_crash:
            with self._lock:
                self._crash_loop_emitted[subsystem] = False
            return False

        with self._lock:
            if not self._crash_loop_emitted.get(subsystem, False):
                self._crash_loop_emitted[subsystem] = True
                return True
            return False

    def can_emit_recovery_request(
        self,
        subsystem: str,
        now_monotonic: float | None = None,
    ) -> bool:
        """Circuit breaker check: enforces cooldown between recovery requests for the same subsystem."""
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        with self._lock:
            last_req = self._last_recovery_request_at.get(subsystem)
            if last_req is None:
                return True
            elapsed = now - last_req
            return elapsed >= self._recovery_cooldown_seconds

    def record_recovery_request_emitted(
        self,
        subsystem: str,
        now_monotonic: float | None = None,
    ) -> None:
        """Record that a recovery request was emitted, setting the cooldown timestamp."""
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        with self._lock:
            self._last_recovery_request_at[subsystem] = now

    def reset_subsystem(self, subsystem: str) -> None:
        """Reset crash loop and recovery tracking on a successful subsystem recovery."""
        with self._lock:
            self._failure_history.pop(subsystem, None)
            self._last_recovery_request_at.pop(subsystem, None)
            self._crash_loop_emitted.pop(subsystem, None)

    def get_recent_incidents(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return the most recent incidents as serialized dicts."""
        with self._lock:
            records = list(self._ring_buffer)
        if limit is not None and limit > 0:
            records = records[-limit:]
        return [asdict(r) for r in reversed(records)]

    def clear(self) -> None:
        """Clear all in-memory diagnostic state."""
        with self._lock:
            self._ring_buffer.clear()
            self._failure_history.clear()
            self._last_recovery_request_at.clear()
            self._crash_loop_emitted.clear()
