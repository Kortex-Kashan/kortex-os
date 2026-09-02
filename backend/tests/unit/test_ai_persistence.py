"""Unit tests for AI Orchestration Engine conversation persistence (Milestone 4).

Runs against a **real** SQLite-backed `IDataStore` — no mocked SQL — so
transactional sequencing, ordering, and durability are genuinely exercised.

Each test gets its own temporary database file, deliberately *not* the
shared `kortex_local.db` used by some older suites: state must not leak
between tests or accumulate across runs.
"""

from __future__ import annotations

import asyncio
import datetime
import pathlib
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.engines.ai.exceptions import ConversationStoreError
from kortex.engines.ai.memory import (
    AIMemoryManager,
    ConversationTurn,
    IConversationStore,
    InMemoryConversationStore,
)
from kortex.engines.ai.persistence import AIConversationTurnRow, StorageConversationStore
from kortex.engines.storage.stores.data_store import RelationalDataStore

TENANT = "tenant-a"
CONVERSATION = "conv-1"


@pytest.fixture
async def data_store(tmp_path: pathlib.Path) -> AsyncIterator[RelationalDataStore]:
    """An isolated, real SQLite `IDataStore` scoped to this test only."""
    db_path = (tmp_path / "m4_conversations.db").as_posix()
    manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await manager.connect()
    await manager.create_all_tables()
    try:
        yield RelationalDataStore(manager)
    finally:
        await manager.disconnect()


@pytest.fixture
def store(data_store: RelationalDataStore) -> StorageConversationStore:
    return StorageConversationStore(data_store)


async def _append(
    store: IConversationStore, text: str, tenant: str = TENANT, conversation: str = CONVERSATION
) -> ConversationTurn:
    return await store.append(
        tenant_id=tenant,
        conversation_id=conversation,
        user_content=f"u-{text}",
        assistant_content=f"a-{text}",
        request_id=f"req-{text}",
        user_id="user-1",
    )


# --------------------------------------------------------------------------
# Port conformance
# --------------------------------------------------------------------------


def test_storage_store_satisfies_port(store: StorageConversationStore) -> None:
    assert isinstance(store, IConversationStore)


# --------------------------------------------------------------------------
# M5 / M4 — sequencing and ordering
# --------------------------------------------------------------------------


async def test_sequence_is_one_based_and_gap_free(store: StorageConversationStore) -> None:
    for index in range(1, 6):
        turn = await _append(store, str(index))
        assert turn.sequence == index

    turns = await store.recent_turns(TENANT, CONVERSATION, limit=100)
    assert [t.sequence for t in turns] == [1, 2, 3, 4, 5]


async def test_sequence_is_per_conversation_not_global(store: StorageConversationStore) -> None:
    await _append(store, "a", conversation="conv-1")
    await _append(store, "b", conversation="conv-1")
    first_of_second = await _append(store, "c", conversation="conv-2")
    assert first_of_second.sequence == 1


async def test_sequence_is_per_tenant(store: StorageConversationStore) -> None:
    await _append(store, "a", tenant="tenant-a")
    other = await _append(store, "b", tenant="tenant-b")
    assert other.sequence == 1


async def test_reads_order_by_sequence_not_created_at(
    store: StorageConversationStore, data_store: RelationalDataStore
) -> None:
    """Ordering must survive clock skew: a later turn with an earlier
    timestamp must still read back after its predecessor."""
    now = datetime.datetime.now(datetime.UTC)

    async def _seed(session: AsyncSession) -> None:
        # sequence 1 written with a LATER clock than sequence 2.
        session.add(
            AIConversationTurnRow(
                id=str(uuid.uuid4()),
                tenant_id=TENANT,
                conversation_id=CONVERSATION,
                sequence=1,
                user_content="first",
                assistant_content="a1",
                request_id="r1",
                user_id="user-1",
                created_at=now,
            )
        )
        session.add(
            AIConversationTurnRow(
                id=str(uuid.uuid4()),
                tenant_id=TENANT,
                conversation_id=CONVERSATION,
                sequence=2,
                user_content="second",
                assistant_content="a2",
                request_id="r2",
                user_id="user-1",
                created_at=now - datetime.timedelta(hours=5),
            )
        )

    await data_store.execute_in_transaction(_seed)

    turns = await store.recent_turns(TENANT, CONVERSATION, limit=10)
    assert [t.user_content for t in turns] == ["first", "second"]


# --------------------------------------------------------------------------
# M7 / M8 — bounded reads keep the newest, in chronological order
# --------------------------------------------------------------------------


async def test_limit_keeps_newest_turns_in_chronological_order(
    store: StorageConversationStore,
) -> None:
    for index in range(1, 21):
        await _append(store, str(index))
    turns = await store.recent_turns(TENANT, CONVERSATION, limit=3)
    assert [t.sequence for t in turns] == [18, 19, 20]


async def test_limit_larger_than_history_returns_everything(
    store: StorageConversationStore,
) -> None:
    await _append(store, "a")
    assert len(await store.recent_turns(TENANT, CONVERSATION, limit=500)) == 1


async def test_empty_conversation_returns_empty(store: StorageConversationStore) -> None:
    assert await store.recent_turns(TENANT, "never-used", limit=10) == []


# --------------------------------------------------------------------------
# M2 — tenant isolation enforced in SQL
# --------------------------------------------------------------------------


async def test_tenant_isolation_at_sql_level(store: StorageConversationStore) -> None:
    await _append(store, "secret", tenant="tenant-a")
    await _append(store, "other", tenant="tenant-b")

    a_turns = await store.recent_turns("tenant-a", CONVERSATION, limit=10)
    b_turns = await store.recent_turns("tenant-b", CONVERSATION, limit=10)
    assert [t.user_content for t in a_turns] == ["u-secret"]
    assert [t.user_content for t in b_turns] == ["u-other"]


async def test_conversation_isolation_at_sql_level(store: StorageConversationStore) -> None:
    await _append(store, "one", conversation="conv-1")
    await _append(store, "two", conversation="conv-2")
    turns = await store.recent_turns(TENANT, "conv-1", limit=10)
    assert [t.user_content for t in turns] == ["u-one"]


# --------------------------------------------------------------------------
# M15 — durability across store instances
# --------------------------------------------------------------------------


async def test_history_survives_a_new_store_instance(
    data_store: RelationalDataStore,
) -> None:
    """State lives in the database, not in the store object."""
    writer = StorageConversationStore(data_store)
    await _append(writer, "persisted")

    reader = StorageConversationStore(data_store)
    turns = await reader.recent_turns(TENANT, CONVERSATION, limit=10)
    assert [t.user_content for t in turns] == ["u-persisted"]


async def test_history_survives_reconnecting_the_database(tmp_path: pathlib.Path) -> None:
    """The strongest durability check: a fresh engine against the same file."""
    db_path = (tmp_path / "durable.db").as_posix()
    url = f"sqlite+aiosqlite:///{db_path}"

    first = DatabaseEngineManager(connection_url=url)
    await first.connect()
    await first.create_all_tables()
    await _append(StorageConversationStore(RelationalDataStore(first)), "durable")
    await first.disconnect()

    second = DatabaseEngineManager(connection_url=url)
    await second.connect()
    try:
        turns = await StorageConversationStore(RelationalDataStore(second)).recent_turns(TENANT, CONVERSATION, limit=10)
        assert [t.user_content for t in turns] == ["u-durable"]
    finally:
        await second.disconnect()


# --------------------------------------------------------------------------
# M1 — model independence
# --------------------------------------------------------------------------


def test_row_stores_no_provider_or_model_column() -> None:
    columns = set(AIConversationTurnRow.__table__.columns.keys())
    assert not {c for c in columns if "provider" in c or "model" in c or "vendor" in c}
    assert {
        "tenant_id",
        "conversation_id",
        "sequence",
        "user_content",
        "assistant_content",
        "request_id",
        "user_id",
    } <= columns


def test_row_has_sequence_unique_constraint() -> None:
    """Load-bearing: converts a lost race into a loud error, not a duplicate ordinal."""
    constraints = {
        tuple(sorted(c.columns.keys()))
        for c in AIConversationTurnRow.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }
    assert ("conversation_id", "sequence", "tenant_id") in constraints


# --------------------------------------------------------------------------
# M6 / M18 — concurrency and atomicity
# --------------------------------------------------------------------------


async def test_concurrent_appends_never_duplicate_an_ordinal(
    store: StorageConversationStore,
) -> None:
    """Either every append succeeds with a contiguous sequence, or a losing
    racer raises — never a silently duplicated ordinal."""
    results = await asyncio.gather(*(_append(store, str(i)) for i in range(12)), return_exceptions=True)
    succeeded = [r for r in results if isinstance(r, ConversationTurn)]
    failed = [r for r in results if isinstance(r, BaseException)]

    for failure in failed:
        assert isinstance(failure, ConversationStoreError)

    sequences = sorted(t.sequence for t in succeeded)
    assert len(sequences) == len(set(sequences))  # no duplicates

    stored = await store.recent_turns(TENANT, CONVERSATION, limit=100)
    assert sorted(t.sequence for t in stored) == sequences


async def test_failed_transaction_leaves_no_row(
    store: StorageConversationStore, data_store: RelationalDataStore
) -> None:
    """M18: a mid-transaction failure must roll back completely."""

    async def _add_then_fail(session: AsyncSession) -> None:
        session.add(
            AIConversationTurnRow(
                id=str(uuid.uuid4()),
                tenant_id=TENANT,
                conversation_id=CONVERSATION,
                sequence=99,
                user_content="doomed",
                assistant_content="doomed",
                request_id="r",
                user_id="user-1",
                created_at=datetime.datetime.now(datetime.UTC),
            )
        )
        raise RuntimeError("forced mid-transaction failure")

    with pytest.raises(ConversationStoreError):
        await store._run(_add_then_fail, "run doomed action")

    assert await store.recent_turns(TENANT, CONVERSATION, limit=10) == []


async def test_store_failure_is_normalized_and_chained(
    store: StorageConversationStore,
) -> None:
    async def _boom(session: AsyncSession) -> None:
        raise ValueError("underlying driver failure")

    with pytest.raises(ConversationStoreError) as exc_info:
        await store._run(_boom, "run failing action")
    assert isinstance(exc_info.value.__cause__, ValueError)


async def test_existing_store_error_is_reraised_not_double_wrapped(
    store: StorageConversationStore,
) -> None:
    """An error that is already a ConversationStoreError must propagate
    unchanged, so the original diagnostic is not buried under a second
    wrapper."""
    original = ConversationStoreError("already normalized")

    async def _boom(session: AsyncSession) -> None:
        raise original

    with pytest.raises(ConversationStoreError) as exc_info:
        await store._run(_boom, "run pre-normalized failure")
    assert exc_info.value is original
    assert exc_info.value.__cause__ is None


async def test_failure_message_never_leaks_content(
    store: StorageConversationStore,
) -> None:
    """M13: conversation content is tenant-sensitive."""
    sentinel = "SENSITIVE-CONTENT-ABC"

    async def _boom(session: AsyncSession) -> None:
        raise ValueError(sentinel)

    with pytest.raises(ConversationStoreError) as exc_info:
        await store._run(_boom, "run failing action")
    assert sentinel not in str(exc_info.value)


# --------------------------------------------------------------------------
# M16 — both store implementations satisfy one identical contract
# --------------------------------------------------------------------------


@pytest.fixture(params=["in_memory", "storage"])
def any_store(request: pytest.FixtureRequest, data_store: RelationalDataStore) -> IConversationStore:
    if request.param == "in_memory":
        return InMemoryConversationStore()
    return StorageConversationStore(data_store)


async def test_parity_sequences(any_store: IConversationStore) -> None:
    for index in range(1, 4):
        assert (await _append(any_store, str(index))).sequence == index


async def test_parity_ordering_and_limit(any_store: IConversationStore) -> None:
    for index in range(1, 11):
        await _append(any_store, str(index))
    turns = await any_store.recent_turns(TENANT, CONVERSATION, limit=2)
    assert [t.sequence for t in turns] == [9, 10]


async def test_parity_tenant_isolation(any_store: IConversationStore) -> None:
    await _append(any_store, "a", tenant="tenant-a")
    await _append(any_store, "b", tenant="tenant-b")
    assert len(await any_store.recent_turns("tenant-a", CONVERSATION, limit=10)) == 1


async def test_parity_empty_conversation(any_store: IConversationStore) -> None:
    assert await any_store.recent_turns(TENANT, "unused", limit=5) == []


async def test_parity_through_the_manager(any_store: IConversationStore) -> None:
    """The manager behaves identically regardless of which store backs it."""
    manager = AIMemoryManager(any_store, max_history_turns=2)
    from kortex.engines.ai.models import LLMRequest, LLMResponse

    for index in range(1, 6):
        await manager.append_history(
            TENANT,
            CONVERSATION,
            LLMRequest(
                request_id=f"r{index}",
                tenant_id=TENANT,
                user_id="user-1",
                conversation_id=CONVERSATION,
                prompt=f"u{index}",
            ),
            LLMResponse(request_id=f"r{index}", text_content=f"a{index}"),
        )
    entries = await manager.get_context(TENANT, CONVERSATION)
    assert len(entries) == 4
    assert entries[0].endswith("u4")
    assert entries[-1].endswith("a5")


# --------------------------------------------------------------------------
# Sanity: the table is only reachable through the store
# --------------------------------------------------------------------------


async def test_store_is_the_only_writer(store: StorageConversationStore, data_store: RelationalDataStore) -> None:
    await _append(store, "one")

    async def _count(session: AsyncSession) -> int:
        result = await session.execute(select(AIConversationTurnRow))
        return len(list(result.scalars().all()))

    assert await data_store.execute_in_transaction(_count) == 1
