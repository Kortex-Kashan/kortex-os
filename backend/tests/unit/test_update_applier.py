"""Unit tests for Update Engine atomic component applier and rollback mechanisms.

Phase 7 — Production Hardening — Update Engine.
Verifies atomic file replacement, protected path defenses, rollback copy retention, and reverse swap recovery.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.engines.update.applier import UpdateApplier
from kortex.engines.update.exceptions import UpdateSwapError


def test_swap_components_with_rollback_preservation(tmp_path: Path) -> None:
    """Verify swapping preserves pre-update live files in .rollback copies."""
    target_root = tmp_path / "app_root"
    target_root.mkdir()

    # Pre-existing live file
    live_file = target_root / "module.py"
    live_file.write_text("VERSION = '1.0'\n")

    # Staged updated file
    staging_dir = tmp_path / "staging"
    staged_file = staging_dir / "module.py"
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    staged_file.write_text("VERSION = '2.0'\n")

    applier = UpdateApplier(target_root=target_root)
    swapped = applier.swap_components(staging_dir=staging_dir, update_id="upd-01")

    assert len(swapped) == 1
    rollback_file = target_root / "module.py.rollback_upd-01"
    assert rollback_file.is_file()
    assert rollback_file.read_text() == "VERSION = '1.0'\n"

    # Live file is now updated
    assert live_file.read_text() == "VERSION = '2.0'\n"


def test_swap_new_component_without_existing_live_file(tmp_path: Path) -> None:
    """Verify adding a completely new file creates no rollback copy."""
    target_root = tmp_path / "app_root"
    target_root.mkdir()

    staging_dir = tmp_path / "staging"
    staged_file = staging_dir / "new_feature" / "feature.py"
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    staged_file.write_text("FEATURE = True\n")

    applier = UpdateApplier(target_root=target_root)
    swapped = applier.swap_components(staging_dir=staging_dir, update_id="upd-02")

    assert len(swapped) == 1
    live_file = target_root / "new_feature" / "feature.py"
    assert live_file.is_file()
    assert live_file.read_text() == "FEATURE = True\n"
    # No rollback copy for newly added files
    assert not (target_root / "new_feature" / "feature.py.rollback_upd-02").exists()


def test_reverse_swap_restores_live_state(tmp_path: Path) -> None:
    """Verify reverse_swap reverts modified files and deletes added files."""
    target_root = tmp_path / "app_root"
    target_root.mkdir()

    # 1. Existing file
    live_file = target_root / "existing.py"
    live_file.write_text("OLD\n")

    # 2. Stage both an edit and a new file
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    (staging_dir / "existing.py").write_text("NEW\n")
    (staging_dir / "brand_new.py").write_text("BRAND_NEW\n")

    applier = UpdateApplier(target_root=target_root)
    swapped = applier.swap_components(staging_dir=staging_dir, update_id="upd-03")

    # Reconstruct records for reverse swap
    records: list[tuple[Path, Path | None]] = []
    for p in swapped:
        if p.endswith(".rollback_upd-03"):
            orig = Path(p.replace(".rollback_upd-03", ""))
            records.append((orig, Path(p)))
        else:
            records.append((Path(p), None))

    # Reverse the swap
    applier.reverse_swap(records)

    assert live_file.read_text() == "OLD\n"
    assert not (target_root / "brand_new.py").exists()
    assert not (target_root / "existing.py.rollback_upd-03").exists()


def test_protected_path_swap_rejected(tmp_path: Path) -> None:
    """Verify attempt to swap into protected system topologies is strictly rejected."""
    target_root = tmp_path / "app_root"
    target_root.mkdir()

    staging_dir = tmp_path / "staging"
    evil_file = staging_dir / "storage_data" / "backups" / "evil.bak"
    evil_file.parent.mkdir(parents=True, exist_ok=True)
    evil_file.write_text("malicious\n")

    applier = UpdateApplier(target_root=target_root)
    with pytest.raises(UpdateSwapError) as exc_info:
        applier.swap_components(staging_dir=staging_dir, update_id="upd-evil")
    assert "Protected system topology path" in str(exc_info.value)
