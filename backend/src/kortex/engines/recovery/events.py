"""KORTEX Recovery Engine event publisher and payloads.

Phase 7 — Production Hardening — Recovery Engine.
Publishes decoupled recovery lifecycle events to the Kernel event engine
without blocking or raising exceptions into calling handlers.
"""

from __future__ import annotations

import datetime
import inspect
import logging
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.event.engine import EventPriority
from kortex.engines.recovery.constants import (
    EVENT_RECOVERY_CHECKPOINT_CREATED,
    EVENT_RECOVERY_COMPLETED,
    EVENT_RECOVERY_DELETED,
    EVENT_RECOVERY_FAILED,
    EVENT_RECOVERY_OPERATOR_REQUIRED,
    EVENT_RECOVERY_REQUESTED,
    EVENT_RECOVERY_ROLLBACK_REQUIRED,
    EVENT_RECOVERY_ROLLED_BACK,
    EVENT_RECOVERY_STAGED,
    EVENT_RECOVERY_STARTED,
    EVENT_RECOVERY_SWAPPED,
    EVENT_RECOVERY_VALIDATED,
    EVENT_RECOVERY_VERIFIED,
    RecoveryState,
)

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.recovery.events")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class RecoveryLifecycleEventPayload(BaseModel):
    """Payload model for Recovery lifecycle events."""

    model_config = ConfigDict(frozen=True)

    recovery_id: str = Field(description="Unique recovery identifier")
    backup_id: str = Field(description="Associated backup identifier")
    state: RecoveryState = Field(description="Current recovery state")
    timestamp: str = Field(default_factory=_utc_now_iso, description="UTC event timestamp")
    safety_checkpoint_id: str | None = Field(default=None)
    database_restored: bool = Field(default=False)
    storage_files_restored: int = Field(default=0)
    error_message: str | None = Field(default=None)
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class RecoveryEventPublisher:
    """Publishes decoupled recovery lifecycle events to the Kernel event engine."""

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
        """Safely publish an event onto the Kernel event engine."""
        if self._kernel is None:
            return False

        try:
            event_engine = getattr(self._kernel, "event_engine", None)
            if event_engine is None and hasattr(self._kernel, "get_engine"):
                try:
                    event_engine = self._kernel.get_engine("event")
                except Exception:
                    event_engine = None

            if event_engine is not None and hasattr(event_engine, "publish"):
                event_data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
                res = event_engine.publish(
                    topic=topic,
                    payload=event_data,
                    priority=priority,
                )
                if inspect.isawaitable(res):
                    await res
                return True
        except Exception as exc:
            logger.warning("Failed to publish recovery event '%s': %s", topic, exc)

        return False

    async def emit_requested(self, recovery_id: str, backup_id: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.REQUESTED,
        )
        await self.publish_event(EVENT_RECOVERY_REQUESTED, payload)

    async def emit_started(self, recovery_id: str, backup_id: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.PRECHECKING,
        )
        await self.publish_event(EVENT_RECOVERY_STARTED, payload)

    async def emit_checkpoint_created(self, recovery_id: str, backup_id: str, checkpoint_id: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.CHECKPOINTING,
            safety_checkpoint_id=checkpoint_id,
        )
        await self.publish_event(EVENT_RECOVERY_CHECKPOINT_CREATED, payload)

    async def emit_validated(self, recovery_id: str, backup_id: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.VALIDATING,
        )
        await self.publish_event(EVENT_RECOVERY_VALIDATED, payload)

    async def emit_staged(self, recovery_id: str, backup_id: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.STAGING,
        )
        await self.publish_event(EVENT_RECOVERY_STAGED, payload)

    async def emit_swapped(self, recovery_id: str, backup_id: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.SWAPPING,
            database_restored=True,
        )
        await self.publish_event(EVENT_RECOVERY_SWAPPED, payload)

    async def emit_verified(self, recovery_id: str, backup_id: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.VERIFYING,
            database_restored=True,
        )
        await self.publish_event(EVENT_RECOVERY_VERIFIED, payload)

    async def emit_completed(
        self,
        recovery_id: str,
        backup_id: str,
        checkpoint_id: str,
        storage_files_count: int,
    ) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.COMPLETED,
            safety_checkpoint_id=checkpoint_id,
            database_restored=True,
            storage_files_restored=storage_files_count,
        )
        await self.publish_event(EVENT_RECOVERY_COMPLETED, payload, priority=EventPriority.HIGH)

    async def emit_failed(self, recovery_id: str, backup_id: str, error_msg: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.FAILED,
            error_message=error_msg,
        )
        await self.publish_event(EVENT_RECOVERY_FAILED, payload, priority=EventPriority.HIGH)

    async def emit_rollback_required(self, recovery_id: str, backup_id: str, error_msg: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.ROLLBACK_REQUIRED,
            error_message=error_msg,
        )
        await self.publish_event(EVENT_RECOVERY_ROLLBACK_REQUIRED, payload, priority=EventPriority.CRITICAL)

    async def emit_rolled_back(self, recovery_id: str, backup_id: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.ROLLED_BACK,
        )
        await self.publish_event(EVENT_RECOVERY_ROLLED_BACK, payload, priority=EventPriority.HIGH)

    async def emit_operator_required(self, recovery_id: str, backup_id: str, error_msg: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id=backup_id,
            state=RecoveryState.FAILED_NEEDS_OPERATOR,
            error_message=error_msg,
        )
        await self.publish_event(EVENT_RECOVERY_OPERATOR_REQUIRED, payload, priority=EventPriority.CRITICAL)

    async def emit_deleted(self, recovery_id: str) -> None:
        payload = RecoveryLifecycleEventPayload(
            recovery_id=recovery_id,
            backup_id="",
            state=RecoveryState.COMPLETED,
        )
        await self.publish_event(EVENT_RECOVERY_DELETED, payload)
