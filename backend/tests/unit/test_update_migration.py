"""Unit tests for Update Engine Alembic migration orchestration.

Phase 7 — Production Hardening — Update Engine.
Verifies forward-only Alembic schema migrations, strict downgrade prevention,
and source revision compatibility checks.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kortex.engines.update.exceptions import (
    UpdateSchemaIncompatibleError,
)
from kortex.engines.update.migrator import UpdateMigrator
from kortex.engines.update.models import (
    CompatibilityMetadata,
    DatabaseMigrationMetadata,
    PackageMetadata,
    UpdateManifest,
    VersionMetadata,
)


def create_manifest(
    requires_migration: bool = True,
    target_revision: str = "rev_002",
    supported_sources: list[str] | None = None,
) -> UpdateManifest:
    return UpdateManifest(
        manifest_id="mf-mig-test",
        format_version="1.0",
        created_at="2026-09-05T00:00:00Z",
        expires_at="2026-09-12T00:00:00Z",
        version=VersionMetadata(
            target_version="0.2.0",
            min_supported_version="0.1.0",
            release_channel="stable",
        ),
        compatibility=CompatibilityMetadata(
            platforms=["windows", "linux"],
            architectures=["x86_64"],
        ),
        package=PackageMetadata(
            filename="upd.zip",
            sha256="abc",
            size_bytes=100,
            uncompressed_bytes=200,
            file_count=1,
        ),
        database=DatabaseMigrationMetadata(
            requires_migration=requires_migration,
            target_revision=target_revision,
            supported_source_revisions=supported_sources or ["rev_001"],
            reversible=False,
        ),
        components=[],
        signatures=[],
    )


def test_no_migration_required_returns_early(tmp_path: Path) -> None:
    """Verify that if manifest requires no migration, migrator returns without running alembic."""
    migrator = UpdateMigrator()
    manifest = create_manifest(requires_migration=False)

    result = migrator.run_forward_migration(manifest, db_url="sqlite:///test.db")
    assert result["migrated"] is False
    assert result["current_revision"] is None


def test_already_at_target_revision(tmp_path: Path) -> None:
    """Verify that if live schema is already at target revision, migration is skipped."""
    migrator = UpdateMigrator()
    manifest = create_manifest(requires_migration=True, target_revision="rev_002")

    with patch.object(migrator, "get_current_revision", return_value="rev_002"):
        result = migrator.run_forward_migration(manifest, db_url="sqlite:///test.db")
        assert result["migrated"] is False
        assert result["target_revision"] == "rev_002"


def test_unsupported_source_revision_rejected(tmp_path: Path) -> None:
    """Verify rejection if live revision is not in manifest's supported_source_revisions."""
    migrator = UpdateMigrator()
    manifest = create_manifest(
        requires_migration=True,
        target_revision="rev_005",
        supported_sources=["rev_003", "rev_004"],
    )

    with patch.object(migrator, "get_current_revision", return_value="rev_001"):
        with pytest.raises(UpdateSchemaIncompatibleError) as exc_info:
            migrator.run_forward_migration(manifest, db_url="sqlite:///test.db")
        assert "not in manifest's supported source revisions" in str(exc_info.value)


def test_downgrade_attempt_rejected(tmp_path: Path) -> None:
    """Verify downgrade attempt is strictly rejected (Case I / Section 16)."""
    migrator = UpdateMigrator()
    manifest = create_manifest(
        requires_migration=True,
        target_revision="rev_001",
        supported_sources=["rev_002"],
    )

    # Mock revisions where current revision is a descendant of target revision
    rev1 = MagicMock()
    rev1.revision = "rev_001"
    rev1.down_revision = None

    rev2 = MagicMock()
    rev2.revision = "rev_002"
    rev2.down_revision = "rev_001"

    with (
        patch.object(migrator, "get_current_revision", return_value="rev_002"),
        patch.object(migrator, "_get_revision_map", return_value={"rev_001": rev1, "rev_002": rev2}),
    ):
        with pytest.raises(UpdateSchemaIncompatibleError) as exc_info:
            migrator.run_forward_migration(manifest, db_url="sqlite:///test.db")
        assert "Alembic downgrade is strictly forbidden" in str(exc_info.value)


@pytest.mark.asyncio
async def test_execute_forward_migration_forwards_full_manifest(tmp_path: Path) -> None:
    """execute_forward_migration (the async production entrypoint used by UpdateEngine.apply)
    must forward the actual verified manifest to run_forward_migration rather than a
    synthetic stand-in, so that supported_source_revisions is genuinely enforced.
    """
    migrator = UpdateMigrator()
    manifest = create_manifest(
        requires_migration=True,
        target_revision="rev_005",
        supported_sources=["rev_003", "rev_004"],
    )

    with patch.object(migrator, "get_current_revision", return_value="rev_001"):
        with pytest.raises(UpdateSchemaIncompatibleError) as exc_info:
            await migrator.execute_forward_migration(manifest, db_url="sqlite:///test.db")
        assert "not in manifest's supported source revisions" in str(exc_info.value)


def test_forward_migration_restores_database_url_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run_forward_migration must temporarily set KORTEX_DATABASE_URL for the duration of the
    Alembic upgrade call (env.py's online-migration path re-derives its URL from this env var,
    ignoring the Config object's own sqlalchemy.url), but MUST restore it to its prior value
    afterward -- otherwise the mutation leaks as global process state into every unrelated test
    or engine that later falls back to this same environment variable.
    """
    import os

    migrator = UpdateMigrator()
    manifest = create_manifest(
        requires_migration=True,
        target_revision="rev_002",
        supported_sources=["rev_001"],
    )

    rev1 = MagicMock()
    rev1.revision = "rev_001"
    rev1.down_revision = None
    rev2 = MagicMock()
    rev2.revision = "rev_002"
    rev2.down_revision = "rev_001"

    # Case 1: no prior value set -- must be cleared afterward, not left dangling.
    monkeypatch.delenv("KORTEX_DATABASE_URL", raising=False)
    with (
        patch.object(migrator, "get_current_revision", side_effect=["rev_001", "rev_002"]),
        patch.object(migrator, "_get_revision_map", return_value={"rev_001": rev1, "rev_002": rev2}),
        patch("alembic.command.upgrade"),
    ):
        migrator.run_forward_migration(manifest, db_url="sqlite:///leaked.db")
    assert os.environ.get("KORTEX_DATABASE_URL") is None

    # Case 2: a prior value existed -- must be restored exactly, not overwritten permanently.
    monkeypatch.setenv("KORTEX_DATABASE_URL", "sqlite+aiosqlite:///original.db")
    with (
        patch.object(migrator, "get_current_revision", side_effect=["rev_001", "rev_002"]),
        patch.object(migrator, "_get_revision_map", return_value={"rev_001": rev1, "rev_002": rev2}),
        patch("alembic.command.upgrade"),
    ):
        migrator.run_forward_migration(manifest, db_url="sqlite:///leaked-again.db")
    assert os.environ.get("KORTEX_DATABASE_URL") == "sqlite+aiosqlite:///original.db"


def test_successful_forward_migration(tmp_path: Path) -> None:
    """Verify execution of forward Alembic migration and revision confirmation."""
    migrator = UpdateMigrator()
    manifest = create_manifest(
        requires_migration=True,
        target_revision="rev_002",
        supported_sources=["rev_001"],
    )

    rev1 = MagicMock()
    rev1.revision = "rev_001"
    rev1.down_revision = None

    rev2 = MagicMock()
    rev2.revision = "rev_002"
    rev2.down_revision = "rev_001"

    with (
        patch.object(migrator, "get_current_revision", side_effect=["rev_001", "rev_002"]),
        patch.object(migrator, "_get_revision_map", return_value={"rev_001": rev1, "rev_002": rev2}),
        patch("alembic.command.upgrade") as mock_upgrade,
    ):
        result = migrator.run_forward_migration(manifest, db_url="sqlite:///test.db")
        assert result["migrated"] is True
        assert result["target_revision"] == "rev_002"
        mock_upgrade.assert_called_once()
