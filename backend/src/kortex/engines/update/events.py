"""KORTEX Update Engine canonical event publication.

Phase 7 — Production Hardening — Update Engine.
Implements the frozen 12-event contract.
Zero additions, zero removals, zero aliases, zero speculative events.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

from kortex.engines.update.constants import (
    ALL_UPDATE_EVENTS,
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
)

if TYPE_CHECKING:
    from kortex.engines.event.engine import EventEngine

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class UpdateEventPublisher:
    """Publishes canonical Update Engine events onto the system EventEngine."""

    def __init__(self, event_engine: EventEngine | None = None) -> None:
        self._event_engine = event_engine

    def set_event_engine(self, event_engine: EventEngine | None) -> None:
        self._event_engine = event_engine

    async def publish(
        self,
        event_name: str,
        update_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Publish one of the frozen 12 canonical Update events."""
        if event_name not in ALL_UPDATE_EVENTS:
            raise ValueError(
                f"Unauthorized or non-canonical update event name '{event_name}'. "
                f"Allowed events: {sorted(ALL_UPDATE_EVENTS)}"
            )

        envelope: dict[str, Any] = {
            "event": event_name,
            "update_id": update_id,
            "timestamp": _utc_now_iso(),
            "data": payload or {},
        }

        if self._event_engine is not None and hasattr(self._event_engine, "publish"):
            try:
                await self._event_engine.publish(event_name, envelope)
                logger.debug("Published event '%s' for update '%s'", event_name, update_id)
            except Exception as exc:
                logger.warning("Could not publish event '%s' to EventEngine: %s", event_name, exc)
        else:
            logger.info("EventEngine not attached; logged event: %s", envelope)

    # Dedicated convenience helpers for each of the 12 events
    async def emit_checked(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_CHECKED, update_id, payload)

    async def emit_manifest_verified(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_MANIFEST_VERIFIED, update_id, payload)

    async def emit_staged(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_STAGED, update_id, payload)

    async def emit_safety_checkpoint_created(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_SAFETY_CHECKPOINT_CREATED, update_id, payload)

    async def emit_quiesced(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_QUIESCED, update_id, payload)

    async def emit_migrated(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_MIGRATED, update_id, payload)

    async def emit_applied(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_APPLIED, update_id, payload)

    async def emit_verified(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_VERIFIED, update_id, payload)

    async def emit_completed(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_COMPLETED, update_id, payload)

    async def emit_failed(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_FAILED, update_id, payload)

    async def emit_rolled_back(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_ROLLED_BACK, update_id, payload)

    async def emit_operator_intervention_required(self, update_id: str, payload: dict[str, Any] | None = None) -> None:
        await self.publish(EVENT_UPDATE_OPERATOR_INTERVENTION_REQUIRED, update_id, payload)
