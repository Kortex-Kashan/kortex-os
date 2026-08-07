"""
Unit tests for KORTEX RelationalDataStore (Milestone 3).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.engines.storage.interfaces import IDataStore
from kortex.engines.storage.stores.data_store import RelationalDataStore


@pytest.mark.asyncio
async def test_relational_data_store_protocol_compliance(tmp_path) -> None:
    """Test RelationalDataStore satisfies IDataStore protocol."""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    db_manager = DatabaseEngineManager(connection_url=db_url)
    await db_manager.connect()

    data_store = RelationalDataStore(db_manager)
    assert isinstance(data_store, IDataStore)

    await db_manager.disconnect()


@pytest.mark.asyncio
async def test_get_session(tmp_path) -> None:
    """Test acquiring AsyncSession from RelationalDataStore."""
    db_path = tmp_path / "test_session.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    db_manager = DatabaseEngineManager(connection_url=db_url)
    await db_manager.connect()

    data_store = RelationalDataStore(db_manager)
    sessions = []
    async for session in data_store.get_session():
        sessions.append(session)
        assert isinstance(session, AsyncSession)

    assert len(sessions) == 1
    await db_manager.disconnect()


@pytest.mark.asyncio
async def test_execute_in_transaction(tmp_path) -> None:
    """Test executing a transaction block in RelationalDataStore."""
    db_path = tmp_path / "test_tx.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    db_manager = DatabaseEngineManager(connection_url=db_url)
    await db_manager.connect()

    data_store = RelationalDataStore(db_manager)

    async def sample_action(session: AsyncSession) -> int:
        res = await session.execute(text("SELECT 42"))
        return res.scalar_one()

    result = await data_store.execute_in_transaction(sample_action)
    assert result == 42

    await db_manager.disconnect()
