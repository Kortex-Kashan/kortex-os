"""KORTEX Sentinel Engine — Heartbeat and Watchdog Manager.

Provides explicit registration, tracking, and evaluation of active component
heartbeats using monotonic time and deterministic thresholds.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from kortex.engines.sentinel.constants import (
    DEFAULT_HEARTBEAT_FAILURE_MULTIPLIER,
    DEFAULT_HEARTBEAT_WARNING_MULTIPLIER,
)
from kortex.engines.sentinel.interfaces import IHeartbeatSource
from kortex.engines.sentinel.models import CheckStatus, ProbeResult

logger = logging.getLogger("kortex.engine.sentinel.heartbeats")


@dataclass
class HeartbeatRecord:
    """Internal state tracking a registered heartbeat source."""

    source_id: str
    expected_interval_seconds: float
    owner: str
    registered_at_monotonic: float
    last_heartbeat_monotonic: float
    heartbeat_count: int = 0


class HeartbeatManager:
    """Manages heartbeat registrations, recordings, and threshold evaluations."""

    def __init__(
        self,
        warning_multiplier: float = DEFAULT_HEARTBEAT_WARNING_MULTIPLIER,
        failure_multiplier: float = DEFAULT_HEARTBEAT_FAILURE_MULTIPLIER,
    ) -> None:
        self._warning_multiplier = warning_multiplier
        self._failure_multiplier = failure_multiplier
        self._sources: dict[str, HeartbeatRecord] = {}
        self._lock = threading.Lock()

    def register_source(
        self,
        source: str | IHeartbeatSource,
        expected_interval_seconds: float | None = None,
        owner: str = "system",
        replace: bool = False,
    ) -> None:
        """Register a heartbeat source.

        Args:
            source: Source identifier string or IHeartbeatSource instance.
            expected_interval_seconds: Expected interval in seconds (required if source is str).
            owner: Owning subsystem or module name.
            replace: If True, overwrite an existing registration deterministically.

        Raises:
            ValueError: If source_id is empty, interval is non-positive, or duplicate without replace=True.
        """
        if isinstance(source, IHeartbeatSource):
            source_id = source.source_id
            interval = source.expected_interval_seconds
        elif isinstance(source, str):
            source_id = source.strip()
            if expected_interval_seconds is None:
                raise ValueError("expected_interval_seconds must be provided when source is a string")
            interval = expected_interval_seconds
        else:
            raise ValueError(f"Invalid source type: {type(source).__name__}")

        if not source_id:
            raise ValueError("source_id must not be empty")

        if interval <= 0.0:
            raise ValueError(f"expected_interval_seconds must be strictly positive, got {interval}")

        now = time.monotonic()
        with self._lock:
            if source_id in self._sources and not replace:
                raise ValueError(
                    f"Heartbeat source '{source_id}' is already registered. Set replace=True to re-register."
                )

            self._sources[source_id] = HeartbeatRecord(
                source_id=source_id,
                expected_interval_seconds=interval,
                owner=owner,
                registered_at_monotonic=now,
                last_heartbeat_monotonic=now,
                heartbeat_count=0,
            )
            logger.debug(
                "Heartbeat source '%s' registered (interval=%.2fs, owner=%s, replace=%s)",
                source_id,
                interval,
                owner,
                replace,
            )

    def unregister_source(self, source_id: str) -> bool:
        """Unregister a heartbeat source.

        Once unregistered, the source will no longer be evaluated or generate alerts.
        """
        with self._lock:
            removed = self._sources.pop(source_id, None) is not None
        if removed:
            logger.debug("Heartbeat source '%s' unregistered", source_id)
        return removed

    def record_heartbeat(self, source_id: str, timestamp_monotonic: float | None = None) -> bool:
        """Record a heartbeat ping from a registered source.

        Thread-safe and async-compatible.
        """
        ts = timestamp_monotonic if timestamp_monotonic is not None else time.monotonic()
        with self._lock:
            record = self._sources.get(source_id)
            if record is None:
                logger.warning("Heartbeat received for unregistered source '%s'", source_id)
                return False

            record.last_heartbeat_monotonic = ts
            record.heartbeat_count += 1
            return True

    def reset_source(self, source_id: str, timestamp_monotonic: float | None = None) -> bool:
        """Reset the heartbeat timer on a legitimate subsystem restart."""
        ts = timestamp_monotonic if timestamp_monotonic is not None else time.monotonic()
        with self._lock:
            record = self._sources.get(source_id)
            if record is None:
                return False
            record.last_heartbeat_monotonic = ts
            return True

    def has_source(self, source_id: str) -> bool:
        """Check whether a heartbeat source is currently registered."""
        with self._lock:
            return source_id in self._sources

    @property
    def registered_count(self) -> int:
        """Number of active registered heartbeat sources."""
        with self._lock:
            return len(self._sources)

    def evaluate_all(
        self,
        now_monotonic: float | None = None,
        is_starting: bool = False,
        is_stopping: bool = False,
    ) -> list[ProbeResult]:
        """Evaluate liveness across all registered heartbeat sources.

        Respects startup and shutdown immunity:
        - When is_stopping is True, heartbeat lapses are suppressed.
        - When is_starting is True, newly registered sources are given startup grace.
        """
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        results: list[ProbeResult] = []

        with self._lock:
            records = list(self._sources.values())

        for record in records:
            age = max(0.0, now - record.last_heartbeat_monotonic)
            warn_threshold = record.expected_interval_seconds * self._warning_multiplier
            fail_threshold = record.expected_interval_seconds * self._failure_multiplier

            details: dict[str, Any] = {
                "source_id": record.source_id,
                "owner": record.owner,
                "expected_interval_seconds": record.expected_interval_seconds,
                "age_seconds": round(age, 3),
                "heartbeat_count": record.heartbeat_count,
            }

            if is_stopping:
                # Shutdown immunity
                results.append(
                    ProbeResult(
                        probe_name=f"heartbeat.{record.source_id}",
                        status=CheckStatus.PASS,
                        message=f"Subsystem shutdown in progress; heartbeat '{record.source_id}' suppressed.",
                        details=details,
                        is_required=False,
                    )
                )
                continue

            if is_starting and record.heartbeat_count == 0:
                # Startup grace for newly registered source that hasn't pinged yet
                since_reg = max(0.0, now - record.registered_at_monotonic)
                if since_reg < fail_threshold:
                    results.append(
                        ProbeResult(
                            probe_name=f"heartbeat.{record.source_id}",
                            status=CheckStatus.PASS,
                            message=f"Startup grace active for heartbeat '{record.source_id}'.",
                            details=details,
                            is_required=False,
                        )
                    )
                    continue

            if age >= fail_threshold:
                results.append(
                    ProbeResult(
                        probe_name=f"heartbeat.{record.source_id}",
                        status=CheckStatus.FAIL,
                        message=(
                            f"Heartbeat failure: '{record.source_id}' age {age:.2f}s "
                            f"exceeded failure threshold {fail_threshold:.2f}s."
                        ),
                        details=details,
                        is_required=False,  # heartbeat probes are optional probes by default
                    )
                )
            elif age >= warn_threshold:
                results.append(
                    ProbeResult(
                        probe_name=f"heartbeat.{record.source_id}",
                        status=CheckStatus.WARN,
                        message=(
                            f"Heartbeat warning: '{record.source_id}' age {age:.2f}s "
                            f"exceeded warning threshold {warn_threshold:.2f}s."
                        ),
                        details=details,
                        is_required=False,
                    )
                )
            else:
                results.append(
                    ProbeResult(
                        probe_name=f"heartbeat.{record.source_id}",
                        status=CheckStatus.PASS,
                        message=f"Heartbeat '{record.source_id}' healthy (age {age:.2f}s).",
                        details=details,
                        is_required=False,
                    )
                )

        return results
