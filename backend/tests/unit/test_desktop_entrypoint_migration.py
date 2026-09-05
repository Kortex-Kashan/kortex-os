"""Targeted safety tests for `kortex.api.desktop_entrypoint`'s migration
compatibility logic (implementation_plan.md Part 2, Control 3).

Covers exactly the five cases the final pre-commit verification gate
required: fresh database, fully-migrated database, legacy
`create_all()`-only database, a deliberately partial/incomplete legacy
database, and a partially-migrated (but legitimately Alembic-tracked)
database. Each uses a real, on-disk SQLite file and the real `alembic`
package -- no mocking of the migration machinery itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine

from kortex.api.desktop_entrypoint import (
    _REVISION_CHAIN,
    resolve_alembic_config,
    run_migrations,
    stamp_revision_for_preexisting_database,
)
from kortex.core.db import Base

HEAD_REVISION = _REVISION_CHAIN[-1][0]
BASELINE_REVISION = _REVISION_CHAIN[0][0]
LICENSES_REVISION = _REVISION_CHAIN[1][0]


def _all_tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _current_alembic_revision(db_path: Path) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            return None  # Table doesn't exist -- never stamped at all.
        return row[0] if row else None
    finally:
        conn.close()


def _create_all_tables_directly(db_path: Path) -> None:
    """Mirrors `DatabaseEngineManager.create_all_tables()`'s actual DDL
    output exactly (same `Base.metadata`), via a plain sync engine -- no
    async plumbing needed for a one-shot schema creation in a test. This is
    the real mechanism that produced every "legacy" database this module's
    compatibility logic must handle: `Kernel.boot()`'s own unconditional
    `create_all_tables()`, run before this milestone's migration invocation
    ever existed.
    """
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


def _drop_tables(db_path: Path, tables: set[str]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        for table in tables:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _isolated_database_url(tmp_path, monkeypatch):
    """Every test in this file gets its own scratch SQLite file, matching
    the rest of this repository's test-isolation convention (explicit
    per-test `KORTEX_DATABASE_URL`, never the shared production default)."""
    db_path = tmp_path / "desktop_entrypoint_test.db"
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    return db_path


class TestFreshDatabase:
    """Case A: no application tables initially."""

    def test_full_migration_chain_applies_and_final_revision_is_recorded(self, _isolated_database_url):
        db_path = _isolated_database_url
        assert not db_path.exists()

        run_migrations()

        assert _current_alembic_revision(db_path) == HEAD_REVISION
        all_expected_tables = {t for _, tables in _REVISION_CHAIN for t in tables}
        assert all_expected_tables.issubset(_all_tables(db_path))


class TestFullyMigratedDatabase:
    """Case B: startup against an already-head database is a no-op."""

    def test_second_run_is_idempotent_with_no_duplicate_creation_errors(self, _isolated_database_url):
        db_path = _isolated_database_url
        run_migrations()
        tables_after_first_run = _all_tables(db_path)

        run_migrations()  # Must not raise (e.g. "table already exists").

        assert _current_alembic_revision(db_path) == HEAD_REVISION
        assert _all_tables(db_path) == tables_after_first_run


class TestLegacyCreateAllDatabase:
    """Case C: a database created entirely by `create_all()`, with no
    Alembic tracking at all -- the exact real-world scenario found and
    fixed during this milestone's own installed-artifact testing."""

    def test_complete_legacy_schema_is_stamped_at_head_not_recreated(self, _isolated_database_url):
        db_path = _isolated_database_url
        _create_all_tables_directly(db_path)
        assert "alembic_version" not in _all_tables(db_path)

        run_migrations()  # Must not raise "table already exists".

        assert _current_alembic_revision(db_path) == HEAD_REVISION

    def test_legacy_schema_with_present_but_empty_alembic_version_table_is_also_handled(self, _isolated_database_url):
        # The exact real case found in production testing: alembic_version
        # exists (e.g. from an interrupted prior attempt) but has zero rows.
        db_path = _isolated_database_url
        _create_all_tables_directly(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.commit()
        conn.close()

        run_migrations()

        assert _current_alembic_revision(db_path) == HEAD_REVISION


class TestPartialLegacyDatabase:
    """Case D: a deliberately incomplete legacy schema -- must NOT be
    silently stamped as current, and must fail safely rather than silently
    skip or corrupt state."""

    def test_missing_table_in_a_later_revision_is_not_silently_stamped_past_the_gap(self, _isolated_database_url):
        db_path = _isolated_database_url
        _create_all_tables_directly(db_path)
        # Simulate an interrupted/partial legacy create_all(): the
        # hr_payroll "level" is missing one of its six tables, and the
        # operations "level" is entirely absent -- baseline + licenses
        # remain fully intact.
        _drop_tables(db_path, {"hr_payroll_entries", "ops_vehicles", "ops_vehicle_tracking_records", "ops_incidents"})

        # Directly verify the stamp function's own decision first, in
        # isolation, before letting the full run_migrations() path (which
        # would then attempt -- and fail on -- the real upgrade) run.
        stamp_revision_for_preexisting_database(db_path)
        recorded = _current_alembic_revision(db_path)
        assert recorded == LICENSES_REVISION, (
            f"must stamp no further than the last FULLY verified revision ({LICENSES_REVISION}), "
            f"not guess past the incomplete hr_payroll level; got {recorded!r}"
        )

        # The remaining, genuinely inconsistent gap must surface as a loud,
        # diagnosable failure -- never a silent skip, never a silent stamp
        # past it.
        with pytest.raises(Exception, match="already exists"):
            command.upgrade(resolve_alembic_config(), "head")

    def test_incomplete_baseline_itself_is_left_entirely_unstamped(self, _isolated_database_url):
        db_path = _isolated_database_url
        _create_all_tables_directly(db_path)
        _drop_tables(db_path, {"documents"})  # Remove one baseline table.

        stamp_revision_for_preexisting_database(db_path)

        assert _current_alembic_revision(db_path) is None, (
            "an incomplete baseline must not be stamped at any revision at all"
        )


class TestPartiallyMigratedAlembicDatabase:
    """Case E: a database already legitimately tracked by Alembic at an
    intermediate revision -- the compatibility/stamp logic must never
    engage at all for this case, and the normal upgrade path must continue
    forward correctly from wherever it legitimately is."""

    def test_existing_intermediate_revision_is_preserved_and_upgraded_forward_not_skipped(self, _isolated_database_url):
        db_path = _isolated_database_url
        # Bring the database to a real, legitimately-tracked intermediate
        # state via Alembic itself (not create_all()).
        command.upgrade(resolve_alembic_config(), BASELINE_REVISION)
        assert _current_alembic_revision(db_path) == BASELINE_REVISION
        tables_at_baseline = _all_tables(db_path)

        run_migrations()

        assert _current_alembic_revision(db_path) == HEAD_REVISION
        all_expected_tables = {t for _, tables in _REVISION_CHAIN for t in tables}
        assert all_expected_tables.issubset(_all_tables(db_path))
        # Nothing from the already-applied baseline was dropped/recreated.
        assert tables_at_baseline.issubset(_all_tables(db_path))
