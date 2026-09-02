"""
Unit tests for Database Engine Manager and ORM Base Model.
"""

from pathlib import Path

import pytest
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel, DatabaseDialect, DatabaseEngineManager


class SampleUser(BaseModel):
    __tablename__ = "test_users"
    username: Mapped[str] = mapped_column(String(50), nullable=False)


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
