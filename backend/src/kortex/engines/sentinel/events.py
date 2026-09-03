"""KORTEX Sentinel Engine — Event Definitions and Publisher.

Defines schemas and emission helpers for the authoritative Sentinel event family:
1. kortex.sentinel.health.changed
2. kortex.sentinel.subsystem.failed
3. kortex.sentinel.subsystem.recovered
4. kortex.sentinel.deadlock.detected
5. kortex.sentinel.crash_loop.detected
6. kortex.sentinel.recovery.requested
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.event.engine import EventPriority
from kortex.engines.sentinel.constants import (
    ENGINE_NAME,
    TOPIC_CRASH_LOOP_DETECTED,
    TOPIC_DEADLOCK_DETECTED,
    TOPIC_HEALTH_CHANGED,
    TOPIC_RECOVERY_REQUESTED,
    TOPIC_SUBSYSTEM_FAILED,
    TOPIC_SUBSYSTEM_RECOVERED,
)

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engine.sentinel.events")


class SentinelEventPayload(BaseModel):
    """Base model for Sentinel event payloads."""

    model_config = ConfigDict(frozen=True)

    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class HealthChangedPayload(SentinelEventPayload):
    """Payload for kortex.sentinel.health.changed."""

    previous_status: str
    current_status: str
    healthy: bool
    degraded_subsystems: list[str] = Field(default_factory=list)


class SubsystemFailedPayload(SentinelEventPayload):
    """Payload for kortex.sentinel.subsystem.failed."""

    subsystem_name: str
    failure_class: str
    health_status: str
    error_message: str
    consecutive_failures: int = 1
    details: dict[str, Any] = Field(default_factory=dict)


class SubsystemRecoveredPayload(SentinelEventPayload):
    """Payload for kortex.sentinel.subsystem.recovered."""

    subsystem_name: str
    previous_status: str
    current_status: str


class DeadlockDetectedPayload(SentinelEventPayload):
    """Payload for kortex.sentinel.deadlock.detected."""

    loop_lag_ms: float
    stalled_operations: list[dict[str, Any]] = Field(default_factory=list)
    active_operations_count: int = 0


class CrashLoopDetectedPayload(SentinelEventPayload):
    """Payload for kortex.sentinel.crash_loop.detected."""

    subsystem_name: str
    failure_count: int
    window_seconds: float
    circuit_breaker_tripped: bool = True


class RecoveryRequestedPayload(SentinelEventPayload):
    """Payload for kortex.sentinel.recovery.requested."""

    subsystem_name: str
    failure_class: str
    health_status: str
    idempotency_key: str  # Deterministic per failure episode
    observed_evidence: dict[str, Any] = Field(default_factory=dict)
    diagnostic_context: dict[str, Any] = Field(default_factory=dict)
    suggested_action: str = "RESTART_SUBSYSTEM"


class SentinelEventPublisher:
    """Helper for publishing Sentinel events onto Kernel EventEngine."""

    def __init__(self, kernel: Kernel | None = None) -> None:
        self._kernel = kernel

    def set_kernel(self, kernel: Kernel) -> None:
        """Bind kernel reference."""
        self._kernel = kernel

    async def publish_event(
        self,
        topic: str,
        payload: BaseModel | dict[str, Any],
        priority: EventPriority = EventPriority.NORMAL,
    ) -> bool:
        """Publish an event onto Kernel event engine.

        Never raises — event publishing failures are logged and isolated.
        """
        if self._kernel is None:
            logger.debug("Kernel not bound; suppressing event on topic '%s'", topic)
            return False

        payload_dict = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload

        try:
            res = await self._kernel.publish_event(
                topic=topic,
                payload=payload_dict,
                sender=ENGINE_NAME,
                priority=priority,
            )
            logger.debug(
                "Emitted Sentinel event on topic '%s' (event_id=%s, notified=%d)",
                topic,
                res.event_id,
                res.subscribers_notified,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to emit Sentinel event on topic '%s': %s", topic, exc)
            return False

    async def emit_health_changed(
        self,
        previous_status: str,
        current_status: str,
        healthy: bool,
        degraded_subsystems: list[str] | None = None,
        correlation_id: str | None = None,
    ) -> bool:
        """Publish kortex.sentinel.health.changed event."""
        payload = HealthChangedPayload(
            previous_status=previous_status,
            current_status=current_status,
            healthy=healthy,
            degraded_subsystems=degraded_subsystems or [],
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        return await self.publish_event(TOPIC_HEALTH_CHANGED, payload, priority=EventPriority.HIGH)

    async def emit_subsystem_failed(
        self,
        subsystem_name: str,
        failure_class: str,
        health_status: str,
        error_message: str,
        consecutive_failures: int = 1,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> bool:
        """Publish kortex.sentinel.subsystem.failed event."""
        payload = SubsystemFailedPayload(
            subsystem_name=subsystem_name,
            failure_class=failure_class,
            health_status=health_status,
            error_message=error_message,
            consecutive_failures=consecutive_failures,
            details=details or {},
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        return await self.publish_event(TOPIC_SUBSYSTEM_FAILED, payload, priority=EventPriority.CRITICAL)

    async def emit_subsystem_recovered(
        self,
        subsystem_name: str,
        previous_status: str,
        current_status: str,
        correlation_id: str | None = None,
    ) -> bool:
        """Publish kortex.sentinel.subsystem.recovered event."""
        payload = SubsystemRecoveredPayload(
            subsystem_name=subsystem_name,
            previous_status=previous_status,
            current_status=current_status,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        return await self.publish_event(TOPIC_SUBSYSTEM_RECOVERED, payload, priority=EventPriority.NORMAL)

    async def emit_deadlock_detected(
        self,
        loop_lag_ms: float,
        stalled_operations: list[dict[str, Any]],
        active_operations_count: int,
        correlation_id: str | None = None,
    ) -> bool:
        """Publish kortex.sentinel.deadlock.detected event."""
        payload = DeadlockDetectedPayload(
            loop_lag_ms=loop_lag_ms,
            stalled_operations=stalled_operations,
            active_operations_count=active_operations_count,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        return await self.publish_event(TOPIC_DEADLOCK_DETECTED, payload, priority=EventPriority.CRITICAL)

    async def emit_crash_loop_detected(
        self,
        subsystem_name: str,
        failure_count: int,
        window_seconds: float,
        correlation_id: str | None = None,
    ) -> bool:
        """Publish kortex.sentinel.crash_loop.detected event."""
        payload = CrashLoopDetectedPayload(
            subsystem_name=subsystem_name,
            failure_count=failure_count,
            window_seconds=window_seconds,
            circuit_breaker_tripped=True,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        return await self.publish_event(TOPIC_CRASH_LOOP_DETECTED, payload, priority=EventPriority.CRITICAL)

    async def emit_recovery_requested(
        self,
        subsystem_name: str,
        failure_class: str,
        health_status: str,
        idempotency_key: str,
        observed_evidence: dict[str, Any] | None = None,
        diagnostic_context: dict[str, Any] | None = None,
        suggested_action: str = "RESTART_SUBSYSTEM",
        correlation_id: str | None = None,
    ) -> bool:
        """Publish kortex.sentinel.recovery.requested event."""
        payload = RecoveryRequestedPayload(
            subsystem_name=subsystem_name,
            failure_class=failure_class,
            health_status=health_status,
            idempotency_key=idempotency_key,
            observed_evidence=observed_evidence or {},
            diagnostic_context=diagnostic_context or {},
            suggested_action=suggested_action,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        return await self.publish_event(TOPIC_RECOVERY_REQUESTED, payload, priority=EventPriority.HIGH)
