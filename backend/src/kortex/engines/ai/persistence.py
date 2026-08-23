"""Durable conversation persistence for the KORTEX OS AI Orchestration Engine.

**This is the single module in `kortex.engines.ai` permitted to import
infrastructure** (`sqlalchemy`, `kortex.core.db`,
`kortex.engines.storage.interfaces`). Every other module in the package
keeps the narrow `kortex.engines.ai.*` / `kortex.core.exceptions` allowlist,
and `kortex.engines.security`, `kortex.core.kernel`, `kortex.core.container`,
and `kortex.engines.knowledge` remain forbidden here as everywhere else —
those are authority boundaries, not data dependencies.

Follows the pattern already established by Knowledge Engine's own
`persistence.py` and Security Engine's record models: inherit
`core.db.BaseModel` and rely on the existing `Base.metadata.create_all()`
boot path for table creation. No new persistence mechanism, no migration,
and no database connection is ever opened by this module itself — all row
work happens inside `IDataStore.execute_in_transaction`.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from typing import Final, TypeVar, cast

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel
from kortex.engines.ai.exceptions import ConversationStoreError
from kortex.engines.ai.memory import ConversationTurn
from kortex.engines.storage.interfaces import IDataStore

logger = logging.getLogger("kortex.engines.ai.persistence")

_T = TypeVar("_T")

MAX_APPEND_RETRIES: Final[int] = 3


class AIConversationTurnRow(BaseModel):
    """Durable row for one completed (user, assistant) turn.

    Stores **no provider or model identifier** — history belongs to the
    conversation, not to whatever model happened to answer, so swapping
    models leaves retrieval unchanged.

    Identity is `(tenant_id, conversation_id, sequence)`. The unique
    constraint is load-bearing: it converts a lost sequence race into a
    loud integrity error instead of a silently duplicated ordinal.

    Content is stored as the caller supplied it, unsanitized — sanitization
    belongs at render time so the stored record stays audit-faithful.
    """

    __tablename__ = "ai_conversation_turns"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "conversation_id", "sequence", name="uq_ai_conversation_turn_sequence"
        ),
        Index("ix_ai_conversation_turn_lookup", "tenant_id", "conversation_id", "sequence"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    user_content: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_content: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)


class StorageConversationStore:
    """`IConversationStore` backed by Storage Engine's `IDataStore`.

    The only writer of `ai_conversation_turns`. Ordering uses an explicit
    per-conversation sequence assigned inside the same transaction as the
    insert — never a timestamp, and deliberately not the
    `time.monotonic_ns()` pattern used elsewhere in the platform, whose
    reference point Python defines as undefined and therefore resets across
    process restarts. Durable conversation ordering must survive a restart.
    """

    def __init__(self, data_store: IDataStore, max_retries: int = MAX_APPEND_RETRIES) -> None:
        self._data_store = data_store
        self._max_retries = max(1, max_retries)

    @property
    def max_retries(self) -> int:
        """Configured maximum retry attempts on sequence collision."""
        return self._max_retries

    async def append(
        self,
        tenant_id: str,
        conversation_id: str,
        user_content: str,
        assistant_content: str,
        request_id: str,
        user_id: str,
    ) -> ConversationTurn:
        """Insert one turn, assigning the next sequence within the same transaction.

        Retries on sequence collision (unique constraint violation) up to
        `max_retries` times, recalculating `max(sequence)` within a fresh
        transaction on each attempt.

        Raises:
            ConversationStoreError: On any storage failure, including a lost
                sequence race that exhausts all retry attempts. Never swallowed
                — a failed history write means the recorded conversation is wrong.
        """
        created_at = datetime.datetime.now(datetime.UTC)

        async def _action(session: AsyncSession) -> int:
            highest = await session.scalar(
                select(func.max(AIConversationTurnRow.sequence)).where(
                    AIConversationTurnRow.tenant_id == tenant_id,
                    AIConversationTurnRow.conversation_id == conversation_id,
                )
            )
            next_sequence = int(highest or 0) + 1
            session.add(
                AIConversationTurnRow(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    sequence=next_sequence,
                    user_content=user_content,
                    assistant_content=assistant_content,
                    request_id=request_id,
                    user_id=user_id,
                    created_at=created_at,
                )
            )
            return next_sequence

        for attempt in range(1, self._max_retries + 1):
            try:
                sequence = await self._run(_action, "append conversation turn")
                return ConversationTurn(
                    sequence=sequence,
                    user_content=user_content,
                    assistant_content=assistant_content,
                    request_id=request_id,
                    user_id=user_id,
                    created_at=created_at,
                )
            except ConversationStoreError as exc:
                is_collision = (
                    "IntegrityError" in exc.message
                    or "UniqueConstraint" in exc.message
                    or "unique" in exc.message.lower()
                    or "constraint" in exc.message.lower()
                )
                if is_collision and attempt < self._max_retries:
                    jitter = random.uniform(0.005, 0.03 * attempt)  # noqa: S311
                    logger.warning(
                        "Conversation turn sequence collision on (%s, %s) (attempt %d/%d), retrying in %.3fs...",
                        tenant_id,
                        conversation_id,
                        attempt,
                        self._max_retries,
                        jitter,
                    )
                    await asyncio.sleep(jitter)
                    continue
                raise

        raise ConversationStoreError(  # pragma: no cover
            f"Conversation store failed to append conversation turn after {self._max_retries} attempts."
        )

    async def recent_turns(
        self, tenant_id: str, conversation_id: str, limit: int
    ) -> list[ConversationTurn]:
        """Return at most `limit` most-recent turns, oldest-first.

        Selects the newest `limit` rows by descending sequence, then
        reverses — so truncation always keeps the *newest* turns while the
        caller still receives them in chronological order. Ordering is by
        `sequence` only; `created_at` is stored for audit and is never an
        ordering key.
        """

        async def _action(session: AsyncSession) -> list[AIConversationTurnRow]:
            result = await session.execute(
                select(AIConversationTurnRow)
                .where(
                    AIConversationTurnRow.tenant_id == tenant_id,
                    AIConversationTurnRow.conversation_id == conversation_id,
                )
                .order_by(AIConversationTurnRow.sequence.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

        rows = await self._run(_action, "read conversation turns")
        return [
            ConversationTurn(
                sequence=row.sequence,
                user_content=row.user_content,
                assistant_content=row.assistant_content,
                request_id=row.request_id,
                user_id=row.user_id,
                created_at=row.created_at,
            )
            for row in reversed(rows)
        ]

    async def _run(
        self, action: Callable[[AsyncSession], Awaitable[_T]], description: str
    ) -> _T:
        """Execute `action` transactionally, normalizing failures.

        The message names the operation and the underlying exception *type*
        only — never conversation content or a stored value, which are
        tenant-sensitive.
        """
        try:
            result = await self._data_store.execute_in_transaction(action)
        except ConversationStoreError:
            raise
        except (IntegrityError, Exception) as exc:
            raise ConversationStoreError(
                f"Conversation store failed to {description}: {type(exc).__name__}"
            ) from exc
        return cast("_T", result)


__all__ = [
    "MAX_APPEND_RETRIES",
    "AIConversationTurnRow",
    "StorageConversationStore",
]
