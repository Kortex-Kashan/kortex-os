"""Event definitions and publisher for the KORTEX Monitoring Engine.

Defines schemas and emission helpers for the approved Monitoring event family:
1. kortex.monitoring.threshold.exceeded
2. kortex.monitoring.threshold.recovered
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.event.engine import EventPriority
from kortex.engines.monitoring.constants import (
    EVENT_MONITORING_THRESHOLD_EXCEEDED,
    EVENT_MONITORING_THRESHOLD_RECOVERED,
    MONITORING_ENGINE_NAME,
)

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.monitoring.events")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class MonitoringEventPayload(BaseModel):
    """Base model for Monitoring event payloads."""

    model_config = ConfigDict(frozen=True)

    timestamp: str = Field(default_factory=_utc_now_iso)
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class ThresholdExceededPayload(MonitoringEventPayload):
    """Payload for kortex.monitoring.threshold.exceeded."""

    metric_name: str
    subsystem: str
    current_value: float
    threshold_value: float
    severity: str
    consecutive_breaches: int


class ThresholdRecoveredPayload(MonitoringEventPayload):
    """Payload for kortex.monitoring.threshold.recovered."""

    metric_name: str
    subsystem: str
    current_value: float
    recovery_value: float
    previous_severity: str


class MonitoringEventPublisher:
    """Thread-safe event publisher using the Kernel's event engine."""

    def __init__(self, kernel: Kernel | None = None) -> None:
        self._kernel = kernel

    def set_kernel(self, kernel: Kernel) -> None:
        self._kernel = kernel

    async def publish_event(
        self,
        topic: str,
        payload: BaseModel | dict[str, Any],
        priority: EventPriority = EventPriority.NORMAL,
    ) -> bool:
        """Publish an event onto Kernel event engine without crashing."""
        if self._kernel is None:
            logger.debug("Kernel not bound; suppressing event on topic '%s'", topic)
            return False

        payload_dict = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload

        try:
            res = await self._kernel.publish_event(
                topic=topic,
                payload=payload_dict,
                sender=MONITORING_ENGINE_NAME,
                priority=priority,
            )
            logger.debug(
                "Emitted Monitoring event on topic '%s' (event_id=%s, notified=%d)",
                topic,
                res.event_id,
                res.subscribers_notified,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to emit Monitoring event on topic '%s': %s", topic, exc)
            return False

    async def emit_threshold_exceeded(
        self,
        metric_name: str,
        subsystem: str,
        current_value: float,
        threshold_value: float,
        severity: str,
        consecutive_breaches: int,
        correlation_id: str | None = None,
    ) -> bool:
        """Emit kortex.monitoring.threshold.exceeded event."""
        payload = ThresholdExceededPayload(
            metric_name=metric_name,
            subsystem=subsystem,
            current_value=current_value,
            threshold_value=threshold_value,
            severity=severity,
            consecutive_breaches=consecutive_breaches,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        priority = EventPriority.HIGH if severity == "CRITICAL" else EventPriority.NORMAL
        return await self.publish_event(EVENT_MONITORING_THRESHOLD_EXCEEDED, payload, priority=priority)

    async def emit_threshold_recovered(
        self,
        metric_name: str,
        subsystem: str,
        current_value: float,
        recovery_value: float,
        previous_severity: str,
        correlation_id: str | None = None,
    ) -> bool:
        """Emit kortex.monitoring.threshold.recovered event."""
        payload = ThresholdRecoveredPayload(
            metric_name=metric_name,
            subsystem=subsystem,
            current_value=current_value,
            recovery_value=recovery_value,
            previous_severity=previous_severity,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        return await self.publish_event(EVENT_MONITORING_THRESHOLD_RECOVERED, payload, priority=EventPriority.NORMAL)
