"""
KORTEX Transactional Event Outbox (Milestone M5.2).

Provides durable, tenant-scoped event staging directly through `IDataStore` (relational sessions),
enabling atomic coupling between database state changes and event publication in compliance
with the KORTEX Engineering Constitution.
"""

from __future__ import annotations

import datetime
import enum
import json
import logging
import uuid
from typing import Any, cast

from sqlalchemy import DateTime, Integer, String, Text, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel
from kortex.core.idempotency import sanitize_for_persistence
from kortex.engines.storage.interfaces import IDataStore

logger = logging.getLogger("kortex.core.outbox")


class EventOutboxStatus(enum.StrEnum):
    """Lifecycle status of a staged transactional outbox record."""

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class EventOutboxModel(BaseModel):
    """SQLAlchemy ORM model for the Transactional Event Outbox."""

    __tablename__ = "event_outbox"

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default=EventOutboxStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboxStore:
    """Manages transactional outbox staging and asynchronous event dispatching."""

    def __init__(self, data_store: IDataStore) -> None:
        self._data_store = data_store

    def stage_event_in_session(
        self,
        session: AsyncSession,
        tenant_id: str,
        topic: str,
        payload: dict[str, Any],
    ) -> EventOutboxModel:
        """Stage an event outbox record within an existing transactional session."""
        sanitized = sanitize_for_persistence(payload)
        record = EventOutboxModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            topic=topic,
            payload_json=json.dumps(sanitized),
            status=EventOutboxStatus.PENDING.value,
            retry_count=0,
        )
        session.add(record)
        return record

    async def stage_event(
        self,
        tenant_id: str,
        topic: str,
        payload: dict[str, Any],
    ) -> EventOutboxModel:
        """Stage an event outbox record in a standalone transaction."""

        async def _action(session: AsyncSession) -> EventOutboxModel:
            record = self.stage_event_in_session(session, tenant_id, topic, payload)
            await session.flush()
            return record

        result = await self._data_store.execute_in_transaction(_action)
        return cast(EventOutboxModel, result)

    async def get_pending_events(
        self,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[EventOutboxModel]:
        """Query pending outbox records, optionally filtered by tenant."""

        async def _action(session: AsyncSession) -> list[EventOutboxModel]:
            stmt = (
                select(EventOutboxModel)
                .where(EventOutboxModel.status == EventOutboxStatus.PENDING.value)
                .order_by(EventOutboxModel.created_at.asc())
                .limit(limit)
            )
            if tenant_id is not None:
                stmt = stmt.where(EventOutboxModel.tenant_id == tenant_id)
            res = await session.execute(stmt)
            return list(res.scalars().all())

        result = await self._data_store.execute_in_transaction(_action)
        return cast(list[EventOutboxModel], result)

    async def dispatch_pending(self, event_engine: Any, limit: int = 100) -> int:
        """Sweep PENDING outbox records, publish them to EventEngine, and update status to SENT."""
        pending_records = await self.get_pending_events(limit=limit)
        if not pending_records:
            return 0

        dispatched_count = 0
        for record in pending_records:
            r_id = record.id
            try:
                payload = json.loads(record.payload_json)
                if hasattr(event_engine, "publish"):
                    await event_engine.publish(
                        topic=record.topic,
                        payload=payload,
                        sender="kortex.core.outbox",
                    )

                async def _mark_sent(session: AsyncSession, row_id: str = r_id) -> None:
                    stmt = (
                        update(EventOutboxModel)
                        .where(EventOutboxModel.id == row_id)
                        .values(
                            status=EventOutboxStatus.SENT.value,
                            updated_at=datetime.datetime.now(datetime.UTC),
                        )
                    )
                    await session.execute(stmt)

                await self._data_store.execute_in_transaction(_mark_sent)
                dispatched_count += 1
            except Exception as exc:
                logger.error("Failed to dispatch outbox record '%s': %s", r_id, exc)

                async def _mark_failed(session: AsyncSession, row_id: str = r_id) -> None:
                    stmt = (
                        update(EventOutboxModel)
                        .where(EventOutboxModel.id == row_id)
                        .values(
                            retry_count=EventOutboxModel.retry_count + 1,
                            updated_at=datetime.datetime.now(datetime.UTC),
                        )
                    )
                    await session.execute(stmt)

                await self._data_store.execute_in_transaction(_mark_failed)

        return dispatched_count
