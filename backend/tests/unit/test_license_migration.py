"""
Unit tests for License Engine Alembic migration (b4e89f123c5a).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_ALEMBIC_SCRIPT_DIR = _BACKEND_DIR / "alembic"


def _run_migration_test(db_path: str) -> None:
    os.environ["KORTEX_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_SCRIPT_DIR))

    # 1. Upgrade from empty to head
    command.upgrade(cfg, "head")


def _run_downgrade_test(db_path: str) -> None:
    os.environ["KORTEX_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_SCRIPT_DIR))

    # Downgrade 1 revision back to baseline 81d6d64c51ba
    command.downgrade(cfg, "-1")


@pytest.mark.asyncio
async def test_license_table_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    import asyncio

    db_file = tmp_path / "test_migration.db"
    db_path = str(db_file).replace("\\", "/")

    # Run upgrade to head in thread where no asyncio loop is currently running
    await asyncio.to_thread(_run_migration_test, db_path)

    # Inspect table via async engine
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "kortex_licenses" in tables

        columns = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_columns("kortex_licenses"))
        col_names = {c["name"] for c in columns}
        required_columns = {
            "id",
            "license_id",
            "tenant_id",
            "active_tenant_id",
            "scope",
            "tier",
            "status",
            "raw_token",
            "kid",
            "signature_hex",
            "issued_at",
            "not_before",
            "expires_at",
            "grace_period_days",
            "features_json",
            "quotas_json",
            "activated_at",
            "activated_by",
            "revoked_at",
            "revocation_reason",
            "highest_observed_at",
            "grace_event_emitted",
            "created_at",
            "updated_at",
        }
        assert required_columns.issubset(col_names)

    await engine.dispose()

    # Run downgrade back to 81d6d64c51ba in thread
    await asyncio.to_thread(_run_downgrade_test, db_path)

    engine_post = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine_post.connect() as conn:
        tables_post = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "kortex_licenses" not in tables_post

    await engine_post.dispose()
