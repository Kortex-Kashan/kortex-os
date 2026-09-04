"""KORTEX Backup Engine event publisher and payloads.

Phase 7 — Production Hardening — Backup Engine.
Publishes lifecycle events to Kernel event bus without blocking or failing callers.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.backup.constants import (
    BACKUP_ENGINE_NAME,
    EVENT_BACKUP_COMPLETED,
    EVENT_BACKUP_DELETED,
    EVENT_BACKUP_FAILED,
    EVENT_BACKUP_REQUESTED,
    EVENT_BACKUP_STARTED,
    EVENT_BACKUP_VALIDATION_FAILED,
    BackupScope,
    BackupState,
)
from kortex.engines.event.engine import EventPriority

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.backup.events")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class BackupLifecycleEventPayload(BaseModel):
    """Payload model for Backup lifecycle events."""

    model_config = ConfigDict(frozen=True)

    backup_id: str = Field(description="Unique backup identifier")
    state: BackupState = Field(description="Lifecycle state")
    scope: BackupScope = Field(default=BackupScope.FULL_INSTANCE, description="Backup scope")
    timestamp: str = Field(default_factory=_utc_now_iso, description="UTC event timestamp")
    file_size_bytes: int = Field(default=0, description="Artifact size in bytes", ge=0)
    is_encrypted: bool = Field(default=True, description="Whether payload is encrypted")
    error_message: str | None = Field(default=None, description="Error detail if failed")
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class BackupEventPublisher:
    """Publishes decoupled lifecycle events to the Kernel event engine."""

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
            logger.debug("Kernel not bound; suppressing event on topic '%s'", topic)
            return False

        payload_dict = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload

        try:
            res = await self._kernel.publish_event(
                topic=topic,
                payload=payload_dict,
                sender=BACKUP_ENGINE_NAME,
                priority=priority,
            )
            logger.debug(
                "Emitted Backup event on topic '%s' (event_id=%s, notified=%d)",
                topic,
                res.event_id,
                res.subscribers_notified,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to emit Backup event on topic '%s': %s", topic, exc)
            return False

    async def emit_requested(self, backup_id: str, scope: BackupScope) -> bool:
        payload = BackupLifecycleEventPayload(
            backup_id=backup_id,
            state=BackupState.REQUESTED,
            scope=scope,
        )
        return await self.publish_event(EVENT_BACKUP_REQUESTED, payload)

    async def emit_started(self, backup_id: str, scope: BackupScope) -> bool:
        payload = BackupLifecycleEventPayload(
            backup_id=backup_id,
            state=BackupState.CAPTURING,
            scope=scope,
        )
        return await self.publish_event(EVENT_BACKUP_STARTED, payload)

    async def emit_completed(
        self,
        backup_id: str,
        scope: BackupScope,
        file_size_bytes: int,
        is_encrypted: bool,
    ) -> bool:
        payload = BackupLifecycleEventPayload(
            backup_id=backup_id,
            state=BackupState.VALID,
            scope=scope,
            file_size_bytes=file_size_bytes,
            is_encrypted=is_encrypted,
        )
        return await self.publish_event(EVENT_BACKUP_COMPLETED, payload)

    async def emit_failed(
        self,
        backup_id: str,
        scope: BackupScope,
        error_message: str,
    ) -> bool:
        payload = BackupLifecycleEventPayload(
            backup_id=backup_id,
            state=BackupState.FAILED,
            scope=scope,
            error_message=error_message,
        )
        return await self.publish_event(EVENT_BACKUP_FAILED, payload, priority=EventPriority.HIGH)

    async def emit_deleted(self, backup_id: str) -> bool:
        payload = BackupLifecycleEventPayload(
            backup_id=backup_id,
            state=BackupState.DELETED,
        )
        return await self.publish_event(EVENT_BACKUP_DELETED, payload)

    async def emit_validation_failed(
        self,
        backup_id: str,
        error_message: str,
    ) -> bool:
        payload = BackupLifecycleEventPayload(
            backup_id=backup_id,
            state=BackupState.FAILED,
            error_message=error_message,
        )
        return await self.publish_event(EVENT_BACKUP_VALIDATION_FAILED, payload, priority=EventPriority.HIGH)
