"""Migration verification tests for the Alembic baseline revision
(Database Migration Wiring, Production Hardening work package).

Proves a fresh database reaches an equivalent schema via
`alembic upgrade head` to what `Base.metadata.create_all()` already
produces in production -- two independently-verified paths to the same
schema. `create_all()` remains the production boot path unchanged
(`kortex.core.kernel.Kernel.boot()`); this file does not touch it.

All tests here are plain sync `def` functions, not `async def` --
`alembic.command.upgrade()` internally drives the async migration
environment (`alembic/env.py`) via its own `asyncio.run()` call, which
cannot be nested inside a pytest-asyncio-managed event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

# Import every module that defines a SQLAlchemy ORM model so
# `Base.metadata` is fully populated independently of whether
# `alembic/env.py` has already run in this process -- mirrors that
# file's own explicit import list exactly.
import kortex.core.idempotency
import kortex.core.outbox
import kortex.engines.ai.persistence
import kortex.engines.connector.models
import kortex.engines.document.models
import kortex.engines.knowledge.persistence
import kortex.engines.security.models
import kortex.engines.workflow.persistence
import kortex.modules.finance.persistence  # noqa: F401
from kortex.core.db import DatabaseEngineManager

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"

# The 30 real production tables the baseline revision creates (mirrors
# `alembic/env.py`'s own explicit model-import list exactly). Deliberately
# NOT derived from the live `Base.metadata.tables.keys()` at test-run time:
# `Base` is a single, process-wide, mutable SQLAlchemy declarative base, and
# other test modules legitimately register their own test-only tables on
# it too (e.g. `tests/unit/test_db.py`'s `SampleUser`/`test_users`, used to
# exercise `DatabaseEngineManager` generically) -- whichever such modules
# happen to already be imported into the same pytest process by the time
# this test runs is collection-order-dependent, not a stable "real schema"
# oracle. The baseline migration is a frozen, non-dynamic revision script
# that only ever creates these 30 tables regardless of what else has been
# imported into the process, so this fixed list is the correct comparison
# target, not a live read of a registry that other tests also mutate.
_PRODUCTION_TABLE_NAMES = frozenset(
    {
        "ai_agent_tasks",
        "ai_conversation_turns",
        "ai_decision_records",
        "ai_governance_policies",
        "ai_tenant_quotas",
        "approval_decisions",
        "approval_delegations",
        "approval_requests",
        "connector_action_history",
        "connector_profiles",
        "documents",
        "document_operation_history",
        "document_operation_profiles",
        "document_template_schemas",
        "document_versions",
        "event_outbox",
        "external_executions",
        "finance_invoices",
        "idempotency_records",
        "knowledge_annotations",
        "knowledge_packs",
        "knowledge_records",
        "security_audit_records",
        "security_principals",
        "security_role_permissions",
        "security_secrets",
        "workflow_definitions",
        "workflow_instances",
        "workflow_schedules",
        "workflow_step_runs",
    }
)


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _sync_url_for_inspection(sqlite_path: Path) -> str:
    return f"sqlite:///{sqlite_path.as_posix()}"


def test_alembic_upgrade_head_succeeds_on_empty_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST A: empty database -> `alembic upgrade head` succeeds."""
    db_path = tmp_path / "alembic_upgrade.db"
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", db_url)

    command.upgrade(_alembic_config(db_url), "head")

    assert db_path.exists()


def test_all_base_metadata_tables_exist_after_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST B: every `Base.metadata` table exists after migration."""
    db_path = tmp_path / "alembic_tables.db"
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", db_url)

    command.upgrade(_alembic_config(db_url), "head")

    inspector = sa.inspect(sa.create_engine(_sync_url_for_inspection(db_path)))
    migrated_tables = set(inspector.get_table_names()) - {"alembic_version"}

    assert migrated_tables == set(_PRODUCTION_TABLE_NAMES)


def _columns(inspector: sa.Inspector, table_name: str) -> dict[str, tuple[str, bool]]:
    return {col["name"]: (str(col["type"]), col["nullable"]) for col in inspector.get_columns(table_name)}


def _pk(inspector: sa.Inspector, table_name: str) -> list[str]:
    columns = inspector.get_pk_constraint(table_name)["constrained_columns"]
    return sorted(col for col in columns if col is not None)


def _fks(inspector: sa.Inspector, table_name: str) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        (tuple(sorted(fk["constrained_columns"])), fk["referred_table"], tuple(sorted(fk["referred_columns"])))
        for fk in inspector.get_foreign_keys(table_name)
    }


def _uniques(inspector: sa.Inspector, table_name: str) -> set[tuple[str, ...]]:
    return {tuple(sorted(uc["column_names"])) for uc in inspector.get_unique_constraints(table_name)}


def _indexes(inspector: sa.Inspector, table_name: str) -> set[tuple[tuple[str, ...], bool]]:
    return {
        (tuple(sorted(col for col in ix["column_names"] if col is not None)), bool(ix["unique"]))
        for ix in inspector.get_indexes(table_name)
    }


def _assert_table_schema_equivalent(left: sa.Inspector, right: sa.Inspector, table_name: str) -> None:
    assert _columns(left, table_name) == _columns(right, table_name), f"column mismatch in {table_name!r}"
    assert _pk(left, table_name) == _pk(right, table_name), f"primary key mismatch in {table_name!r}"
    assert _fks(left, table_name) == _fks(right, table_name), f"foreign key mismatch in {table_name!r}"
    assert _uniques(left, table_name) == _uniques(right, table_name), f"unique constraint mismatch in {table_name!r}"
    assert _indexes(left, table_name) == _indexes(right, table_name), f"index mismatch in {table_name!r}"


def test_create_all_and_alembic_schema_are_equivalent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST C: `Base.metadata.create_all()`'s schema and the Alembic
    baseline's schema must be equivalent -- tables, columns, types,
    nullability, primary keys, foreign keys, unique constraints, and
    indexes -- proving the baseline genuinely represents current state,
    not a divergent reinterpretation of it."""
    create_all_path = tmp_path / "create_all.db"
    alembic_path = tmp_path / "alembic.db"

    async def _build_via_create_all() -> None:
        manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{create_all_path.as_posix()}")
        await manager.create_all_tables()
        await manager.disconnect()

    asyncio.run(_build_via_create_all())

    alembic_url = f"sqlite+aiosqlite:///{alembic_path.as_posix()}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", alembic_url)
    command.upgrade(_alembic_config(alembic_url), "head")

    create_all_inspector = sa.inspect(sa.create_engine(_sync_url_for_inspection(create_all_path)))
    alembic_inspector = sa.inspect(sa.create_engine(_sync_url_for_inspection(alembic_path)))

    # `create_all()` legitimately creates every table currently registered
    # on the live, process-wide `Base.metadata` -- including any test-only
    # tables other already-imported test modules registered on it (see
    # `_PRODUCTION_TABLE_NAMES`'s own comment). Scoping both sides to the
    # fixed real-production-table set is what makes this a meaningful
    # "do these two independently-built schemas agree on KORTEX's actual
    # schema" comparison, not an accidental process-wide-state comparison.
    create_all_tables = set(create_all_inspector.get_table_names()) & _PRODUCTION_TABLE_NAMES
    alembic_tables = set(alembic_inspector.get_table_names()) - {"alembic_version"}
    assert create_all_tables == _PRODUCTION_TABLE_NAMES
    assert alembic_tables == _PRODUCTION_TABLE_NAMES

    for table_name in sorted(create_all_tables):
        _assert_table_schema_equivalent(create_all_inspector, alembic_inspector, table_name)


def test_existing_application_boot_remains_functional(tmp_path: Path) -> None:
    """TEST D: adding Alembic does not touch the existing `create_all()`
    boot path -- a fresh `DatabaseEngineManager` still connects and
    creates all tables exactly as before. Full Kernel boot (every
    engine's own dependencies) is already covered by the broader
    regression suite; this test's scope is narrowly "did adding Alembic
    break create_all()", not a Kernel boot re-audit."""

    async def _boot() -> None:
        manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{(tmp_path / 'boot.db').as_posix()}")
        await manager.connect()
        await manager.create_all_tables()
        await manager.disconnect()

    asyncio.run(_boot())


def test_upgrade_head_when_already_at_head_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST E: running `upgrade head` twice in a row is safe."""
    db_path = tmp_path / "idempotent.db"
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", db_url)

    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")


def test_expected_alembic_head_is_correctly_recognized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST F: the script directory has exactly one head, and it matches
    what `alembic_version` records after a real upgrade."""
    db_path = tmp_path / "head_check.db"
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", db_url)

    cfg = _alembic_config(db_url)
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1

    command.upgrade(cfg, "head")

    engine = sa.create_engine(_sync_url_for_inspection(db_path))
    with engine.connect() as conn:
        result = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    assert result == heads[0]
