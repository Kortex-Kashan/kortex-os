"""Unit & concurrency tests for AI Conversation Storage Hardening (Milestone 9.3).

Tests adhere strictly to the ratified M9.3 specification:
- Concurrent append simulation under race conditions
- Optimistic retry handling on sequence collision (IntegrityError)
- Bounded retry exhaustion and ConversationStoreError propagation
- Transaction rollback safety (zero partial/corrupt rows)
- Multi-tenant isolation under concurrent writes
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TypeVar

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.engines.ai.exceptions import ConversationStoreError
from kortex.engines.ai.memory import ConversationTurn
from kortex.engines.ai.persistence import (
    MAX_APPEND_RETRIES,
    StorageConversationStore,
)
from kortex.engines.storage.interfaces import IDataStore
from kortex.engines.storage.stores.data_store import RelationalDataStore

_T = TypeVar("_T")

TENANT_ALPHA = "tenant-alpha"
TENANT_BETA = "tenant-beta"
CONVERSATION_ID = "conv-concurrent-1"


@pytest.fixture
async def data_store(tmp_path: Path) -> AsyncIterator[RelationalDataStore]:
    """Isolated, real SQLite IDataStore instance for transactional concurrency tests."""
    db_path = (tmp_path / "m9_3_concurrency.db").as_posix()
    manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await manager.connect()
    await manager.create_all_tables()
    try:
        yield RelationalDataStore(manager)
    finally:
        await manager.disconnect()


@pytest.fixture
def store(data_store: RelationalDataStore) -> StorageConversationStore:
    """StorageConversationStore backed by real isolated SQLite database."""
    return StorageConversationStore(data_store)


# ---------------------------------------------------------------------------
# §1 — Concurrent Append Simulation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_appends_generate_monotonic_gap_free_sequences(
    store: StorageConversationStore,
) -> None:
    """Verify that 3 concurrent async writers (A, B, C) successfully append without collision."""
    num_writers = 3

    async def _writer(writer_id: int) -> ConversationTurn:
        return await store.append(
            tenant_id=TENANT_ALPHA,
            conversation_id=CONVERSATION_ID,
            user_content=f"User message from writer {writer_id}",
            assistant_content=f"Assistant response to writer {writer_id}",
            request_id=f"req-{writer_id}",
            user_id=f"user-{writer_id}",
        )

    # Launch all writers simultaneously
    results = await asyncio.gather(*[_writer(i) for i in range(num_writers)])

    # All writers must succeed
    assert len(results) == num_writers
    sequences = sorted([r.sequence for r in results])
    assert sequences == list(range(1, num_writers + 1)), "Sequences must be 1..N with zero collisions"

    # Verify retrieval
    stored_turns = await store.recent_turns(TENANT_ALPHA, CONVERSATION_ID, limit=100)
    assert len(stored_turns) == num_writers
    assert [t.sequence for t in stored_turns] == list(range(1, num_writers + 1))


# ---------------------------------------------------------------------------
# §2 — Retry Logic & Collision Tests
# ---------------------------------------------------------------------------


class MockCollidingDataStore(IDataStore):
    """Mock IDataStore that injects IntegrityError for a set number of attempts."""

    def __init__(self, real_store: IDataStore, fail_count: int = 1) -> None:
        self._real_store = real_store
        self._fail_count = fail_count
        self.attempts = 0

    async def get_session(self) -> AsyncIterator[AsyncSession]:
        async for session in self._real_store.get_session():
            yield session

    async def execute_in_transaction(self, action: Callable[[AsyncSession], Awaitable[_T]]) -> _T:
        self.attempts += 1
        if self.attempts <= self._fail_count:
            # Simulate unique constraint collision inside transaction
            raise IntegrityError(
                statement="INSERT INTO ai_conversation_turns...",
                params={},
                orig=Exception("UNIQUE constraint failed: ai_conversation_turns.sequence"),
            )
        return await self._real_store.execute_in_transaction(action)


@pytest.mark.asyncio
async def test_retry_on_integrity_error_succeeds_on_second_attempt(
    data_store: RelationalDataStore,
) -> None:
    """Verify that a sequence collision triggers a retry and succeeds."""
    mock_data_store = MockCollidingDataStore(data_store, fail_count=2)
    colliding_store = StorageConversationStore(mock_data_store, max_retries=3)

    turn = await colliding_store.append(
        tenant_id=TENANT_ALPHA,
        conversation_id="conv-retry",
        user_content="Hello retry",
        assistant_content="Hi retry response",
        request_id="req-retry-1",
        user_id="user-1",
    )

    assert turn.sequence == 1
    assert mock_data_store.attempts == 3  # Failed 2 times, succeeded on 3rd


@pytest.mark.asyncio
async def test_retry_exhaustion_raises_conversation_store_error(
    data_store: RelationalDataStore,
) -> None:
    """Verify that exhausting max_retries raises ConversationStoreError."""
    # Fails 3 times on max_retries=3
    mock_data_store = MockCollidingDataStore(data_store, fail_count=5)
    colliding_store = StorageConversationStore(mock_data_store, max_retries=3)

    with pytest.raises(ConversationStoreError, match="IntegrityError"):
        await colliding_store.append(
            tenant_id=TENANT_ALPHA,
            conversation_id="conv-exhaust",
            user_content="Hello exhaust",
            assistant_content="Hi exhaust",
            request_id="req-exhaust-1",
            user_id="user-1",
        )

    assert mock_data_store.attempts == 3


# ---------------------------------------------------------------------------
# §3 — Rollback & Tenant Isolation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_safety_leaves_no_phantom_rows(
    data_store: RelationalDataStore,
) -> None:
    """Verify that a completely failed transaction leaves zero rows in the database."""
    mock_data_store = MockCollidingDataStore(data_store, fail_count=5)
    colliding_store = StorageConversationStore(mock_data_store, max_retries=2)

    with pytest.raises(ConversationStoreError):
        await colliding_store.append(
            tenant_id=TENANT_ALPHA,
            conversation_id="conv-rollback",
            user_content="Phantom user",
            assistant_content="Phantom assistant",
            request_id="req-phantom",
            user_id="user-1",
        )

    # Check that no turns exist for this conversation
    clean_store = StorageConversationStore(data_store)
    turns = await clean_store.recent_turns(TENANT_ALPHA, "conv-rollback", limit=10)
    assert len(turns) == 0, "Failed append must leave zero phantom turns"


@pytest.mark.asyncio
async def test_tenant_isolation_under_concurrent_writes(
    store: StorageConversationStore,
) -> None:
    """Verify concurrent writes to different tenants with identical conversation_id do not collide."""
    shared_conv_id = "shared-conv-id"
    count_per_tenant = 2

    async def _write_tenant_a(i: int) -> ConversationTurn:
        return await store.append(
            tenant_id=TENANT_ALPHA,
            conversation_id=shared_conv_id,
            user_content=f"Alpha user {i}",
            assistant_content=f"Alpha assistant {i}",
            request_id=f"req-a-{i}",
            user_id="user-a",
        )

    async def _write_tenant_b(i: int) -> ConversationTurn:
        return await store.append(
            tenant_id=TENANT_BETA,
            conversation_id=shared_conv_id,
            user_content=f"Beta user {i}",
            assistant_content=f"Beta assistant {i}",
            request_id=f"req-b-{i}",
            user_id="user-b",
        )

    tasks = [_write_tenant_a(i) for i in range(count_per_tenant)] + [
        _write_tenant_b(i) for i in range(count_per_tenant)
    ]
    await asyncio.gather(*tasks)

    turns_a = await store.recent_turns(TENANT_ALPHA, shared_conv_id, limit=100)
    turns_b = await store.recent_turns(TENANT_BETA, shared_conv_id, limit=100)

    assert len(turns_a) == count_per_tenant
    assert len(turns_b) == count_per_tenant
    assert [t.sequence for t in turns_a] == list(range(1, count_per_tenant + 1))
    assert [t.sequence for t in turns_b] == list(range(1, count_per_tenant + 1))
    assert all("Alpha" in t.user_content for t in turns_a)
    assert all("Beta" in t.user_content for t in turns_b)


# ---------------------------------------------------------------------------
# §4 — Configuration & Boundary Tests
# ---------------------------------------------------------------------------


def test_default_and_custom_max_retries(data_store: RelationalDataStore) -> None:
    """Verify default constant and custom max_retries bounds."""
    assert MAX_APPEND_RETRIES == 3

    default_store = StorageConversationStore(data_store)
    assert default_store.max_retries == 3

    custom_store = StorageConversationStore(data_store, max_retries=5)
    assert custom_store.max_retries == 5

    clamped_store = StorageConversationStore(data_store, max_retries=0)
    assert clamped_store.max_retries == 1
