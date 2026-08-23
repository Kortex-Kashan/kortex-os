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
import json
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Final, TypeVar, cast

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel
from kortex.engines.ai.agent import (
    AgentStatus,
    AgentStep,
    AgentTask,
    IAgentTaskStore,
    PersistedAgentTaskRecord,
    ResumeToken,
)
from kortex.engines.ai.exceptions import (
    AgentNotFoundError,
    AgentStateConflictError,
    AgentTaskStoreError,
    ConversationStoreError,
)
from kortex.engines.ai.memory import ConversationTurn, require_identifier
from kortex.engines.ai.models import TokenUsage
from kortex.engines.ai.tools import ToolCall
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
        self, tenant_id: str, conversation_id: str, limit: int, offset: int = 0
    ) -> list[ConversationTurn]:
        """Return at most `limit` most-recent turns, oldest-first, with optional `offset`.

        Selects rows by descending sequence with offset and limit, then
        reverses — so pagination preserves oldest-first ordering for the caller.
        """
        require_identifier(tenant_id, "tenant_id")
        require_identifier(conversation_id, "conversation_id")

        async def _action(session: AsyncSession) -> list[AIConversationTurnRow]:
            result = await session.execute(
                select(AIConversationTurnRow)
                .where(
                    AIConversationTurnRow.tenant_id == tenant_id,
                    AIConversationTurnRow.conversation_id == conversation_id,
                )
                .order_by(AIConversationTurnRow.sequence.desc())
                .offset(offset)
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
        """Execute `action` transactionally, normalizing failures."""
        try:
            result = await self._data_store.execute_in_transaction(action)
        except ConversationStoreError:
            raise
        except (IntegrityError, Exception) as exc:
            raise ConversationStoreError(
                f"Conversation store failed to {description}: {type(exc).__name__}"
            ) from exc
        return cast("_T", result)


class AIAgentTaskRow(BaseModel):
    """Durable relational row representing an agent task execution snapshot."""

    __tablename__ = "ai_agent_tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "task_id", name="uq_ai_agent_task_identity"),
        Index("ix_ai_agent_task_lookup", "tenant_id", "task_id"),
        Index("ix_ai_agent_task_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    task_json: Mapped[str] = mapped_column(Text, nullable=False)
    steps_json: Mapped[str] = mapped_column(Text, nullable=False)
    pending_calls_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    resume_token_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_usage_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
    )


class StorageAgentTaskStore(IAgentTaskStore):
    """Production durable IAgentTaskStore backed by Storage Engine's IDataStore."""

    def __init__(self, data_store: IDataStore) -> None:
        self._data_store = data_store

    async def save_task(self, record: PersistedAgentTaskRecord) -> None:
        async def _action(session: AsyncSession) -> None:
            task_json = record.task.model_dump_json()
            steps_json = json.dumps([s.model_dump(mode="json") for s in record.steps])
            pending_calls_json = json.dumps(
                [c.model_dump(mode="json") for c in record.pending_tool_calls]
            )
            resume_token_json = (
                record.resume_token.model_dump_json() if record.resume_token else None
            )
            token_usage_json = record.total_token_usage.model_dump_json()

            row = AIAgentTaskRow(
                id=str(uuid.uuid4()),
                tenant_id=record.task.tenant_id,
                task_id=record.task.task_id,
                status=record.status.value,
                version=record.version,
                task_json=task_json,
                steps_json=steps_json,
                pending_calls_json=pending_calls_json,
                resume_token_json=resume_token_json,
                token_usage_json=token_usage_json,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            session.add(row)

        try:
            await self._data_store.execute_in_transaction(_action)
        except IntegrityError as exc:
            raise AgentTaskStoreError(
                f"Task '{record.task.task_id}' already exists in tenant '{record.task.tenant_id}'."
            ) from exc
        except Exception as exc:
            raise AgentTaskStoreError(f"Failed to save agent task: {type(exc).__name__}") from exc

    async def get_task(self, task_id: str, tenant_id: str) -> PersistedAgentTaskRecord | None:
        require_identifier(tenant_id, "tenant_id")
        require_identifier(task_id, "task_id")

        async def _action(session: AsyncSession) -> AIAgentTaskRow | None:
            result = await session.execute(
                select(AIAgentTaskRow).where(
                    AIAgentTaskRow.tenant_id == tenant_id,
                    AIAgentTaskRow.task_id == task_id,
                )
            )
            return result.scalar_one_or_none()

        try:
            row = await self._data_store.execute_in_transaction(_action)
        except Exception as exc:
            raise AgentTaskStoreError(f"Failed to get agent task: {type(exc).__name__}") from exc

        if row is None:
            return None

        return self._row_to_record(row)

    async def update_task(self, record: PersistedAgentTaskRecord) -> None:
        async def _action(session: AsyncSession) -> None:
            task_json = record.task.model_dump_json()
            steps_json = json.dumps([s.model_dump(mode="json") for s in record.steps])
            pending_calls_json = json.dumps(
                [c.model_dump(mode="json") for c in record.pending_tool_calls]
            )
            resume_token_json = (
                record.resume_token.model_dump_json() if record.resume_token else None
            )

            token_usage_json = record.total_token_usage.model_dump_json()

            result = await session.execute(
                update(AIAgentTaskRow)
                .where(
                    AIAgentTaskRow.tenant_id == record.task.tenant_id,
                    AIAgentTaskRow.task_id == record.task.task_id,
                )
                .values(
                    status=record.status.value,
                    version=record.version,
                    task_json=task_json,
                    steps_json=steps_json,
                    pending_calls_json=pending_calls_json,
                    resume_token_json=resume_token_json,
                    token_usage_json=token_usage_json,
                    updated_at=record.updated_at,
                )
            )
            if cast(CursorResult[Any], result).rowcount == 0:
                row = AIAgentTaskRow(
                    id=str(uuid.uuid4()),
                    tenant_id=record.task.tenant_id,
                    task_id=record.task.task_id,
                    status=record.status.value,
                    version=record.version,
                    task_json=task_json,
                    steps_json=steps_json,
                    pending_calls_json=pending_calls_json,
                    resume_token_json=resume_token_json,
                    token_usage_json=token_usage_json,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
                session.add(row)

        try:
            await self._data_store.execute_in_transaction(_action)
        except Exception as exc:
            raise AgentTaskStoreError(f"Failed to update agent task: {type(exc).__name__}") from exc

    async def cancel_task(self, task_id: str, tenant_id: str) -> bool:
        require_identifier(tenant_id, "tenant_id")
        require_identifier(task_id, "task_id")

        async def _action(session: AsyncSession) -> bool:
            now = datetime.datetime.now(datetime.UTC)
            stmt = (
                update(AIAgentTaskRow)
                .where(
                    AIAgentTaskRow.tenant_id == tenant_id,
                    AIAgentTaskRow.task_id == task_id,
                    AIAgentTaskRow.status.in_([
                        AgentStatus.RUNNING.value,
                        AgentStatus.PAUSED_FOR_APPROVAL.value,
                        AgentStatus.RESUMING.value,
                    ]),
                )
                .values(
                    status=AgentStatus.CANCELLED.value,
                    version=AIAgentTaskRow.version + 1,
                    updated_at=now,
                )
            )
            res = await session.execute(stmt)
            return cast(CursorResult[Any], res).rowcount > 0

        try:
            return bool(await self._data_store.execute_in_transaction(_action))
        except Exception as exc:
            raise AgentTaskStoreError(f"Failed to cancel agent task: {type(exc).__name__}") from exc

    async def claim_task_for_resumption(
        self, task_id: str, tenant_id: str, expected_version: int
    ) -> PersistedAgentTaskRecord:
        require_identifier(tenant_id, "tenant_id")
        require_identifier(task_id, "task_id")

        async def _action(session: AsyncSession) -> AIAgentTaskRow | None:
            now = datetime.datetime.now(datetime.UTC)
            stmt = (
                update(AIAgentTaskRow)
                .where(
                    AIAgentTaskRow.tenant_id == tenant_id,
                    AIAgentTaskRow.task_id == task_id,
                    AIAgentTaskRow.status == AgentStatus.PAUSED_FOR_APPROVAL.value,
                    AIAgentTaskRow.version == expected_version,
                )
                .values(
                    status=AgentStatus.RESUMING.value,
                    version=expected_version + 1,
                    updated_at=now,
                )
            )
            res = await session.execute(stmt)
            if cast(CursorResult[Any], res).rowcount == 0:
                return None

            fetch_stmt = select(AIAgentTaskRow).where(
                AIAgentTaskRow.tenant_id == tenant_id,
                AIAgentTaskRow.task_id == task_id,
            )
            fetch_res = await session.execute(fetch_stmt)
            return fetch_res.scalar_one_or_none()

        try:
            row = await self._data_store.execute_in_transaction(_action)
        except Exception as exc:
            raise AgentTaskStoreError(f"Failed to claim task: {type(exc).__name__}") from exc

        if row is None:
            existing = await self.get_task(task_id, tenant_id)
            if existing is None:
                raise AgentNotFoundError(task_id, f"Agent task '{task_id}' not found.")
            if existing.status != AgentStatus.PAUSED_FOR_APPROVAL:
                raise AgentStateConflictError(
                    task_id,
                    f"Agent task '{task_id}' cannot be resumed: current status is '{existing.status}'.",
                )
            raise AgentStateConflictError(
                task_id,
                f"Agent task '{task_id}' concurrency conflict: "
                f"version {existing.version} != expected {expected_version}.",
            )

        return self._row_to_record(row)

    async def list_tasks(
        self, tenant_id: str, status: AgentStatus | None = None, limit: int = 50
    ) -> list[PersistedAgentTaskRecord]:
        require_identifier(tenant_id, "tenant_id")

        async def _action(session: AsyncSession) -> list[AIAgentTaskRow]:
            query = select(AIAgentTaskRow).where(AIAgentTaskRow.tenant_id == tenant_id)
            if status is not None:
                query = query.where(AIAgentTaskRow.status == status.value)
            query = query.order_by(AIAgentTaskRow.updated_at.desc()).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

        try:
            rows = await self._data_store.execute_in_transaction(_action)
            return [self._row_to_record(r) for r in rows]
        except Exception as exc:
            raise AgentTaskStoreError(f"Failed to list agent tasks: {type(exc).__name__}") from exc

    def _row_to_record(self, row: AIAgentTaskRow) -> PersistedAgentTaskRecord:
        task_data = json.loads(row.task_json)
        task = AgentTask.model_validate(task_data)
        steps_data = json.loads(row.steps_json)
        steps = [AgentStep.model_validate(s) for s in steps_data]
        calls_data = json.loads(row.pending_calls_json)
        pending_calls = [ToolCall.model_validate(c) for c in calls_data]
        resume_token = (
            ResumeToken.model_validate_json(row.resume_token_json)
            if row.resume_token_json
            else None
        )
        token_usage = (
            TokenUsage.model_validate_json(row.token_usage_json)
            if getattr(row, "token_usage_json", None)
            else TokenUsage()
        )
        return PersistedAgentTaskRecord(
            task=task,
            status=AgentStatus(row.status),
            current_step=len(steps),
            steps=steps,
            pending_tool_calls=pending_calls,
            resume_token=resume_token,
            total_token_usage=token_usage,
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


__all__ = [
    "MAX_APPEND_RETRIES",
    "AIAgentTaskRow",
    "AIConversationTurnRow",
    "StorageAgentTaskStore",
    "StorageConversationStore",
]
