"""
KORTEX Core Database Foundation.

SQLAlchemy 2.0 Async persistence engine supporting SQLite (default local-first adapter)
and PostgreSQL (enterprise server adapter interface).
"""

from __future__ import annotations

import datetime
import enum
import logging
from typing import AsyncGenerator, Optional

from sqlalchemy import DateTime, String, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger("kortex.core.db")


class DatabaseDialect(str, enum.Enum):
    """Supported database engine dialects."""

    SQLITE = "SQLITE"
    POSTGRESQL = "POSTGRESQL"


class Base(DeclarativeBase):
    """Abstract base class for all SQLAlchemy ORM domain models."""

    pass


class BaseModel(Base):
    """Abstract base ORM model providing standard audit columns."""

    __abstract__ = True

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
        server_default=func.now(),
        nullable=False,
    )


class DatabaseEngineManager:
    """Manages SQLAlchemy async database engine connection and session factories.

    Defaults to SQLite for local-first zero-dependency desktop operation.
    Provides PostgreSQL adapter interface for enterprise server deployments.
    """

    def __init__(self, connection_url: Optional[str] = None, dialect: DatabaseDialect = DatabaseDialect.SQLITE) -> None:
        self._dialect = dialect
        if connection_url:
            self._url = connection_url
        else:
            # Default SQLite local-first file database
            self._url = "sqlite+aiosqlite:///./kortex_local.db"

        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    @property
    def dialect(self) -> DatabaseDialect:
        """Current database dialect."""
        return self._dialect

    @property
    def is_connected(self) -> bool:
        """True if the database engine has been initialized."""
        return self._engine is not None

    async def connect(self) -> None:
        """Initialize the async SQLAlchemy database engine and sessionmaker."""
        if self._engine is not None:
            return

        connect_args = {}
        if "sqlite" in self._url:
            self._dialect = DatabaseDialect.SQLITE
            connect_args = {"check_same_thread": False}
        elif "postgresql" in self._url or "asyncpg" in self._url:
            self._dialect = DatabaseDialect.POSTGRESQL

        logger.info("Initializing database connection engine [%s]: %s", self._dialect.value, self._url.split("@")[-1])

        self._engine = create_async_engine(
            self._url,
            echo=False,
            future=True,
            connect_args=connect_args,
        )

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def create_all_tables(self) -> None:
        """Create all registered ORM tables in the database."""
        if not self._engine:
            await self.connect()
        assert self._engine is not None
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully.")

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield an async database session context."""
        if not self._session_factory:
            await self.connect()
        assert self._session_factory is not None
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def disconnect(self) -> None:
        """Dispose of the database engine connections cleanly."""
        if self._engine:
            logger.info("Closing database connection pool.")
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
