"""
KORTEX Core Database Foundation.

SQLAlchemy 2.0 Async persistence engine supporting SQLite (default local-first adapter)
and PostgreSQL (enterprise server adapter interface).
"""

from __future__ import annotations

import datetime
import enum
import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path

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
        default=lambda: datetime.datetime.now(datetime.UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
        server_default=func.now(),
        nullable=False,
    )


def _default_app_data_dir() -> Path:
    """Resolve a stable, per-user application-data directory for KORTEX's
    default local SQLite database.

    Deliberately NOT a path relative to the process's current working
    directory: a bare `./kortex_local.db` silently resolves to whatever
    directory the process happened to be launched from, meaning two
    processes started from different cwds see two unrelated "empty"
    databases, while two processes started from the *same* cwd (e.g. two
    test runs, or two app instances) silently share one SQLite file with no
    coordination. This resolves to the same absolute location for a given
    user/machine regardless of launch cwd, matching the OS's own convention
    for where a local-first desktop app should keep its data.
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif os.uname().sysname == "Darwin":  # pragma: no cover - platform-specific
        base = str(Path.home() / "Library" / "Application Support")
    else:  # pragma: no cover - platform-specific
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "KORTEX"


def _default_sqlite_url() -> str:
    """Compute the default SQLite connection URL used when no explicit
    `connection_url` (constructor argument or `KORTEX_DATABASE_URL`
    environment variable) is provided.

    This intentionally still resolves to one fixed, shared location per
    user/machine (not a fresh path per call) — several existing tests
    deliberately construct two independent `Kernel()`/`DatabaseEngineManager()`
    instances with no explicit connection and rely on the *default itself*
    being the shared thing that proves data survives across an independent
    "fresh session" (e.g. `test_document_lifecycle_persistence_survives_fresh_session`).
    What this fixes is narrower: the previous default was relative to the
    process's current working directory, so unrelated processes/tests
    launched from different cwds got silently different "shared" stores,
    and any two launched from the *same* cwd (a common accident, not the
    deliberate in-test pattern above) silently collided. Callers that need
    real isolation from every other test or run — the common case — must
    still pass an explicit `connection_url` (e.g. `sqlite+aiosqlite:///:memory:`
    or a `tmp_path`-scoped file), exactly as the majority of this suite
    already does.
    """
    data_dir = _default_app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = (data_dir / "kortex_local.db").as_posix()
    return f"sqlite+aiosqlite:///{db_path}"


class DatabaseEngineManager:
    """Manages SQLAlchemy async database engine connection and session factories.

    Defaults to SQLite for local-first zero-dependency desktop operation.
    Provides PostgreSQL adapter interface for enterprise server deployments.
    """

    def __init__(self, connection_url: str | None = None, dialect: DatabaseDialect = DatabaseDialect.SQLITE) -> None:
        self._dialect = dialect
        # Precedence: explicit constructor argument > KORTEX_DATABASE_URL
        # environment variable > safe computed default. This is the one
        # place the default is decided; nothing downstream needs to know
        # which branch was taken.
        self._url = connection_url or os.environ.get("KORTEX_DATABASE_URL") or _default_sqlite_url()

        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

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
