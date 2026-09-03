"""Alembic migration environment for KORTEX OS.

Targets the single, canonical `kortex.core.db.Base.metadata` -- no
separate metadata registry or duplicate model definitions are created
here. Every module that defines a SQLAlchemy ORM model (a `BaseModel`
subclass with `__tablename__`) must be imported below so its table is
registered on `Base.metadata` before Alembic compares/creates schema;
`Base.metadata.create_all()` (`kortex.core.db.DatabaseEngineManager`)
relies on the same registration happening as a side effect of engine
construction during a real boot -- this environment reproduces that
registration explicitly and minimally, since a bare `alembic` CLI
invocation never boots the Kernel.

The database URL is resolved with the exact same precedence
`DatabaseEngineManager.__init__` already uses (`KORTEX_DATABASE_URL`
environment variable, falling back to the existing per-user default
SQLite location via `_default_sqlite_url()`) -- reusing that function
directly rather than re-deriving the default, so there is exactly one
place the default is decided, not two.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# `backend/alembic/env.py` -> `backend/src` on sys.path, mirroring
# `pyproject.toml`'s `[tool.pytest.ini_options] pythonpath = ["src"]` --
# a bare `alembic` CLI invocation never goes through pytest's path setup.
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Import every module that defines a SQLAlchemy ORM model so its table
# is registered on `Base.metadata`. Kept as an explicit, minimal list
# (not a wildcard package scan) so it is obvious exactly what schema
# this migration environment targets.
import kortex.core.idempotency  # noqa: E402
import kortex.core.outbox  # noqa: E402
import kortex.engines.ai.persistence  # noqa: E402
import kortex.engines.connector.models  # noqa: E402
import kortex.engines.document.models  # noqa: E402
import kortex.engines.knowledge.persistence  # noqa: E402
import kortex.engines.license.tables  # noqa: E402
import kortex.engines.security.models  # noqa: E402
import kortex.engines.workflow.persistence  # noqa: E402
import kortex.modules.finance.persistence  # noqa: E402
import kortex.modules.hr_payroll.persistence  # noqa: E402
import kortex.modules.operations.persistence  # noqa: E402,F401
from kortex.core.db import Base, _default_sqlite_url  # noqa: E402

# this is the Alembic Config object, which provides access to the
# values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging (alembic.ini's
# [loggers]/[handlers]/[formatters] sections).
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The canonical, single metadata target -- see module docstring.
target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """Same precedence as `DatabaseEngineManager.__init__`, minus the
    constructor-argument level (there is no live `DatabaseEngineManager`
    instance in a bare `alembic` CLI invocation): `KORTEX_DATABASE_URL`
    environment variable, else the existing computed default."""
    return os.environ.get("KORTEX_DATABASE_URL") or _default_sqlite_url()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode -- emits SQL to stdout/a script
    without a live DB connection (`alembic upgrade head --sql`)."""
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """KORTEX's engine is SQLAlchemy 2.0 async-only (`aiosqlite`/
    `asyncpg`) -- there is no sync driver configured anywhere in this
    project, so migrations run through an async engine via
    `AsyncConnection.run_sync`, the standard SQLAlchemy-recommended
    pattern for driving Alembic's sync migration context from an async
    engine, rather than introducing a second, sync-only driver
    dependency purely for this environment."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolve_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live async DB connection."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
