"""
KORTEX Dispatcher-Level Idempotency Layer (Milestone M5.2).

Provides durable, tenant-scoped idempotency management, atomic state transitions
(PROCESSING -> COMPLETED / FAILED), concurrency collision protection, and secret-scrubbed
response caching in compliance with the KORTEX Engineering Constitution.
"""

from __future__ import annotations

import contextlib
import datetime
import enum
import json
import logging
import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, String, Text, UniqueConstraint, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel
from kortex.core.exceptions import ConcurrentExecutionError
from kortex.engines.storage.interfaces import ICacheStore, IDataStore

logger = logging.getLogger("kortex.core.idempotency")

SENSITIVE_KEY_NAMES: set[str] = {
    "session_token",
    "auth_token",
    "token",
    "password",
    "secret",
    "secret_key",
    "credentials",
    "bearer_token",
    "api_key",
    "authorization",
    "private_key",
}


def sanitize_for_persistence(value: Any) -> Any:  # noqa: ANN401
    """Recursively scrub sensitive credential tokens and passwords before JSON persistence."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return sanitize_for_persistence(dumped)
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            if str(k).lower() in SENSITIVE_KEY_NAMES:
                cleaned[k] = None
            else:
                cleaned[k] = sanitize_for_persistence(v)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize_for_persistence(item) for item in value]
    # Fallback to string representation for other types
    return str(value)


class IdempotencyState(enum.StrEnum):
    """Execution state of an idempotent mutation."""

    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ClaimResult(enum.StrEnum):
    """Result of attempting to claim an idempotency key."""

    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    PROCESSING = "PROCESSING"


class IdempotencyRecordModel(BaseModel):
    """SQLAlchemy ORM model for durable dispatcher idempotency records.

    Partitioned strictly by (tenant_id, idempotency_key).
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_idempotency_tenant_key"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    capability_name: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32),
        default=IdempotencyState.PROCESSING.value,
        nullable=False,
        index=True,
    )
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdempotencyStore:
    """Manages transactional idempotency claiming, completion, and failure tracking."""

    def __init__(self, data_store: IDataStore, cache_store: ICacheStore | None = None) -> None:
        self._data_store = data_store
        self._cache_store = cache_store

    def _cache_key(self, tenant_id: str, idempotency_key: str) -> str:
        return f"kortex:idemp:{tenant_id}:{idempotency_key}"

    async def claim_or_get_execution(
        self,
        tenant_id: str,
        idempotency_key: str,
        capability_name: str,
        request_id: str,
        correlation_id: str,
    ) -> tuple[ClaimResult, Any | None, str | None]:
        """Atomically claim an idempotency key for execution or retrieve existing state.

        Returns:
            Tuple of (ClaimResult, cached_response_payload, error_message)
        """
        # Tier 1 fast check in memory cache
        if self._cache_store is not None:
            try:
                cached_val = await self._cache_store.get(self._cache_key(tenant_id, idempotency_key))
                if cached_val is not None:
                    return ClaimResult.COMPLETED, cached_val, None
            except Exception as e:
                logger.warning("Cache lookup failed for idempotency key '%s': %s", idempotency_key, e)

        async def _action(session: AsyncSession) -> tuple[ClaimResult, Any | None, str | None]:
            # Query existing row strictly partitioned by tenant_id
            stmt = select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.tenant_id == tenant_id,
                IdempotencyRecordModel.idempotency_key == idempotency_key,
            )
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()

            if row is None:
                # Insert initial PROCESSING record
                new_record = IdempotencyRecordModel(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    capability_name=capability_name,
                    request_id=request_id,
                    correlation_id=correlation_id,
                    state=IdempotencyState.PROCESSING.value,
                )
                try:
                    session.add(new_record)
                    await session.flush()
                    return ClaimResult.CLAIMED, None, None
                except IntegrityError:
                    # Race condition: concurrent insert collided
                    await session.rollback()
                    # Re-query
                    res = await session.execute(stmt)
                    row = res.scalar_one_or_none()
                    if row is None:
                        # Should not happen, but fail closed
                        raise ConcurrentExecutionError(
                            f"Concurrent collision detected for idempotency key '{idempotency_key}'."
                        ) from None

            # Evaluate existing row state
            if row.state == IdempotencyState.COMPLETED.value:
                payload = json.loads(row.response_json) if row.response_json else None
                return ClaimResult.COMPLETED, payload, None

            if row.state == IdempotencyState.PROCESSING.value:
                return ClaimResult.PROCESSING, None, None

            if row.state == IdempotencyState.FAILED.value:
                # Atomically claim the failed record for retry: FAILED -> PROCESSING
                update_stmt = (
                    update(IdempotencyRecordModel)
                    .where(
                        IdempotencyRecordModel.id == row.id,
                        IdempotencyRecordModel.state == IdempotencyState.FAILED.value,
                    )
                    .values(
                        state=IdempotencyState.PROCESSING.value,
                        request_id=request_id,
                        correlation_id=correlation_id,
                        error_message=None,
                        updated_at=datetime.datetime.now(datetime.UTC),
                    )
                )
                result = await session.execute(update_stmt)
                cursor_res = cast(CursorResult[Any], result)
                if cursor_res.rowcount == 1:
                    return ClaimResult.CLAIMED, None, None
                else:
                    # Another concurrent retry claimed it first
                    return ClaimResult.PROCESSING, None, None

            return ClaimResult.PROCESSING, None, None

        result_tuple = await self._data_store.execute_in_transaction(_action)
        return cast(tuple[ClaimResult, Any | None, str | None], result_tuple)

    async def record_completed(
        self,
        tenant_id: str,
        idempotency_key: str,
        response_payload: Any,  # noqa: ANN401
    ) -> None:
        """Transition idempotency record to COMPLETED and persist sanitized response payload."""
        sanitized = sanitize_for_persistence(response_payload)
        resp_json = json.dumps(sanitized)

        async def _action(session: AsyncSession) -> None:
            update_stmt = (
                update(IdempotencyRecordModel)
                .where(
                    IdempotencyRecordModel.tenant_id == tenant_id,
                    IdempotencyRecordModel.idempotency_key == idempotency_key,
                )
                .values(
                    state=IdempotencyState.COMPLETED.value,
                    response_json=resp_json,
                    error_message=None,
                    updated_at=datetime.datetime.now(datetime.UTC),
                )
            )
            await session.execute(update_stmt)

        await self._data_store.execute_in_transaction(_action)

        # Update cache store if present
        if self._cache_store is not None:
            try:
                await self._cache_store.set(
                    self._cache_key(tenant_id, idempotency_key),
                    sanitized,
                    ttl_seconds=86400,
                )
            except Exception as e:
                logger.warning("Failed to cache completed idempotency result: %s", e)

    async def record_failed(
        self,
        tenant_id: str,
        idempotency_key: str,
        error_message: str,
    ) -> None:
        """Transition idempotency record to FAILED and persist failure information."""
        clean_error = error_message[:1000]

        async def _action(session: AsyncSession) -> None:
            update_stmt = (
                update(IdempotencyRecordModel)
                .where(
                    IdempotencyRecordModel.tenant_id == tenant_id,
                    IdempotencyRecordModel.idempotency_key == idempotency_key,
                )
                .values(
                    state=IdempotencyState.FAILED.value,
                    error_message=clean_error,
                    updated_at=datetime.datetime.now(datetime.UTC),
                )
            )
            await session.execute(update_stmt)

        await self._data_store.execute_in_transaction(_action)

        # Invalidate cache if present
        if self._cache_store is not None:
            with contextlib.suppress(Exception):
                await self._cache_store.delete(self._cache_key(tenant_id, idempotency_key))

    async def get_record(
        self, tenant_id: str, idempotency_key: str
    ) -> IdempotencyRecordModel | None:
        """Retrieve the raw IdempotencyRecordModel for inspection."""
        async def _action(session: AsyncSession) -> IdempotencyRecordModel | None:
            stmt = select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.tenant_id == tenant_id,
                IdempotencyRecordModel.idempotency_key == idempotency_key,
            )
            res = await session.execute(stmt)
            return res.scalar_one_or_none()

        result = await self._data_store.execute_in_transaction(_action)
        return cast(IdempotencyRecordModel | None, result)
