"""
KORTEX Relational DataStore Implementation.

Implements the IDataStore protocol wrapping DatabaseEngineManager for async database
sessions and isolated transaction block execution (SQLite / PostgreSQL).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.engines.storage.interfaces import IDataStore

logger = logging.getLogger("kortex.engines.storage.stores.data_store")

_ActionResultT = TypeVar("_ActionResultT")


class RelationalDataStore(IDataStore):
    """Relational database persistence store implementing IDataStore."""

    def __init__(self, db_manager: DatabaseEngineManager) -> None:
        """Initialize RelationalDataStore with a DatabaseEngineManager instance.

        Args:
            db_manager: Core DatabaseEngineManager handling SQLAlchemy engine & sessionmaker.
        """
        self._db_manager = db_manager
        logger.debug("Initialized RelationalDataStore with DatabaseEngineManager")

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Acquire an asynchronous SQLAlchemy session from DatabaseEngineManager.

        Yields:
            AsyncSession instance.
        """
        async for session in self._db_manager.get_session():
            yield session

    async def execute_in_transaction(
        self, action: Callable[[AsyncSession], Awaitable[_ActionResultT]]
    ) -> _ActionResultT:
        """Execute a callable block within an isolated database transaction block.

        Args:
            action: Async callable accepting an AsyncSession parameter.

        Returns:
            Result returned by the action callable.

        Raises:
            RuntimeError: If the session provider yielded no session. Previously
                this path fell through and implicitly returned `None` into
                callers that all treat the result as a real value; it is now an
                explicit error instead of a confusing downstream failure.
        """
        async for session in self._db_manager.get_session():
            async with session.begin():
                result = await action(session)
                return result
        raise RuntimeError("DatabaseEngineManager.get_session() yielded no session.")
