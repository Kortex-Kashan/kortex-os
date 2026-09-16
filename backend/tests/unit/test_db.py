"""
Unit tests for Database Engine Manager and ORM Base Model.
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import String, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel, DatabaseDialect, DatabaseEngineManager


class SampleUser(BaseModel):
    __tablename__ = "test_users"
    username: Mapped[str] = mapped_column(String(50), nullable=False)


class _CounterRow(BaseModel):
    __tablename__ = "test_db_concurrency_counters"
    version: Mapped[int] = mapped_column(nullable=False)


@pytest.mark.asyncio
async def test_database_manager_sqlite_lifecycle(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    db_manager = DatabaseEngineManager(connection_url=db_url)
    assert db_manager.dialect == DatabaseDialect.SQLITE
    assert db_manager.is_connected is False

    await db_manager.connect()
    assert db_manager.is_connected is True

    await db_manager.create_all_tables()

    # Session CRUD check
    async for session in db_manager.get_session():
        user = SampleUser(id="usr_1", username="kortex_admin")
        session.add(user)

    async for session in db_manager.get_session():
        fetched = await session.get(SampleUser, "usr_1")
        assert fetched is not None
        assert fetched.username == "kortex_admin"
        assert fetched.created_at is not None

    await db_manager.disconnect()
    assert db_manager.is_connected is False


@pytest.mark.asyncio
async def test_concurrent_sqlite_transactions_never_lose_a_committed_write() -> None:
    """DEFECT-002 regression: two concurrent `get_session()`/transaction
    cycles against the SAME in-memory SQLite engine, each racing many
    optimistic-lock-style read-current-version-then-write-next-version
    cycles against *different* rows, must never see a spuriously-failed
    write (a write whose expected version genuinely matches the row's own,
    self-consistent prior write).

    Root cause (see `DatabaseEngineManager.get_session()`'s own docstring):
    SQLite allows only one writer transaction at a time, but nothing
    upstream of `DatabaseEngineManager` ever enforced that at the
    application level — two concurrent async transactions sharing SQLite's
    single physical connection (`:memory:` via `StaticPool`) could have
    their statements interleave, so a session's own just-committed write
    was not always visible/durable by the time a *different* session's
    transaction ran its own read-modify-write cycle against a wholly
    unrelated row. This reproduced ~1-in-6 to 1-in-8 runs before the fix
    (`db.py`'s `_sqlite_write_lock`); this test exercises the identical
    mechanism directly, at the storage layer, many times over.
    """
    db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    await db_manager.connect()
    await db_manager.create_all_tables()

    async def _seed(session: AsyncSession) -> None:
        session.add(_CounterRow(id="row-a", version=1))
        session.add(_CounterRow(id="row-b", version=1))

    # DatabaseEngineManager itself has no execute_in_transaction convenience
    # (that lives on RelationalDataStore) -- seed directly via get_session,
    # exactly like the production data-access pattern this test exercises.
    async for session in db_manager.get_session():
        await _seed(session)

    async def bump_many(row_id: str, iterations: int) -> list[str]:
        failures: list[str] = []
        for i in range(iterations):
            async for session in db_manager.get_session():
                res = await session.execute(select(_CounterRow).where(_CounterRow.id == row_id))
                row = res.scalar_one()
                current = row.version
                next_version = current + 1
                # Yield control here, mirroring the real gap between a
                # WorkflowEngine step's read of `instance.version` and its
                # eventual write (step execution, another persistence call,
                # etc.) -- the exact window the original race exploited.
                await asyncio.sleep(0)
                stmt = (
                    update(_CounterRow)
                    .where(_CounterRow.id == row_id, _CounterRow.version == current)
                    .values(version=next_version)
                )
                result = await session.execute(stmt)
                if result.rowcount != 1:
                    failures.append(
                        f"{row_id} iteration {i}: expected version {current}, 0 rows updated (spurious conflict)"
                    )
                break  # one get_session() iteration per loop pass
        return failures

    failures_a, failures_b = await asyncio.gather(
        bump_many("row-a", 150),
        bump_many("row-b", 150),
    )

    assert failures_a == []
    assert failures_b == []

    await db_manager.disconnect()


@pytest.mark.asyncio
async def test_sequential_get_session_calls_on_same_task_never_hang() -> None:
    """DEFECT-002 follow-up validation: the actual call pattern used throughout
    the codebase (e.g. `WorkflowStore.update_instance` completing its own
    `execute_in_transaction`, THEN separately calling `self.get_instance(...)`
    which opens a second, independent session) is always sequential -- one
    session's `get_session()` generator is fully exhausted, releasing the
    SQLite write lock, before the next one is even created. This is the
    "legitimate application pattern" the lock must never penalize: proves a
    second `get_session()` acquisition on the SAME asyncio task, issued
    immediately after the first one's context has closed, completes promptly
    rather than hanging -- bounded by `wait_for` so a future regression fails
    fast with a clear timeout instead of hanging this test (or a CI run) forever.
    """
    db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    await db_manager.connect()
    await db_manager.create_all_tables()

    async def _run() -> None:
        async for session in db_manager.get_session():
            session.add(SampleUser(id="seq-1", username="first"))
        # First `get_session()`'s `async for` has fully exhausted here -- the
        # lock is released -- before this second, independent acquisition.
        async for session in db_manager.get_session():
            session.add(SampleUser(id="seq-2", username="second"))

    await asyncio.wait_for(_run(), timeout=5.0)

    async for session in db_manager.get_session():
        fetched_1 = await session.get(SampleUser, "seq-1")
        fetched_2 = await session.get(SampleUser, "seq-2")
    assert fetched_1 is not None
    assert fetched_2 is not None

    await db_manager.disconnect()


@pytest.mark.asyncio
async def test_nested_get_session_call_on_same_task_times_out_by_design() -> None:
    """DEFECT-002 follow-up validation: documents the one real boundary the
    SQLite write-serialization lock introduces. `asyncio.Lock` is NOT
    reentrant, so a genuine NESTED acquisition -- opening a second session on
    the SAME asyncio task while the first one's `get_session()` generator is
    still open (mid-`async for`, before it has yielded control back and been
    exhausted) -- would self-deadlock forever, since the task would be
    waiting on a lock it itself already holds and will never release until
    the inner wait completes.

    A repo-wide audit (this defect's validation pass) found NO call site
    that actually does this: every multi-call sequence in the codebase
    (`WorkflowStore.update_instance` -> `get_instance`, dispatcher's
    idempotency claim -> handler -> idempotency completion, etc.) is
    sequential, never nested -- each `execute_in_transaction`/`get_session()`
    call fully completes and releases the lock before the next one begins.

    This test locks that boundary in as an explicit, permanent, FAST-FAILING
    contract: if nesting were ever introduced, this asserts it manifests as a
    bounded `asyncio.TimeoutError` here (an obvious, immediate CI failure)
    rather than as an unbounded hang discovered only in production.
    """
    db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    await db_manager.connect()
    await db_manager.create_all_tables()

    # Manually drive the outer generator to its `yield` point (entering the
    # session/lock) without exhausting it -- mirrors what a hypothetical
    # nested caller would see: an open outer session, lock still held.
    outer_gen = db_manager.get_session()
    outer_session = await outer_gen.__anext__()
    outer_session.add(SampleUser(id="nest-outer", username="outer"))
    try:

        async def _nest_inner() -> None:
            # Still inside the outer session's open generator/lock -- a
            # second acquisition on the SAME task must never succeed here.
            async for _inner_session in db_manager.get_session():
                break

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_nest_inner(), timeout=1.0)
    finally:
        # Deterministically release the outer session/lock through
        # `get_session()`'s own `async with` exit path (a `GeneratorExit`
        # thrown at the `yield`) rather than relying on GC-timed
        # async-generator finalization -- guarantees the lock is free
        # before `disconnect()` and before any later test runs.
        await outer_gen.aclose()

    await db_manager.disconnect()
