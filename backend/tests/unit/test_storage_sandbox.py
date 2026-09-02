"""
Unit tests for KORTEX Storage Engine Path Sandbox Validator (Milestone 2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.engines.storage.sandbox import PathSandboxError, PathSandboxValidator


def test_sandbox_initialization(tmp_path: Path) -> None:
    """Test initializing PathSandboxValidator creates directory if missing."""
    sandbox_dir = tmp_path / "storage_root"
    validator = PathSandboxValidator(sandbox_dir)
    assert validator.base_directory == sandbox_dir.resolve()
    assert sandbox_dir.exists()


def test_valid_sandboxed_path_resolution(tmp_path: Path) -> None:
    """Test resolving valid relative paths inside the sandbox."""
    validator = PathSandboxValidator(tmp_path)
    target = validator.resolve_sandboxed_path("subfolder/file.txt")
    assert target == (tmp_path / "subfolder" / "file.txt").resolve()


def test_path_traversal_attack_prevention(tmp_path: Path) -> None:
    """Test that path traversal attempts (../) raise PathSandboxError."""
    validator = PathSandboxValidator(tmp_path)
    with pytest.raises(PathSandboxError, match="escapes sandbox base directory"):
        validator.resolve_sandboxed_path("../../etc/passwd")


def test_absolute_path_outside_sandbox(tmp_path: Path) -> None:
    """Test that providing an absolute path outside the sandbox raises PathSandboxError."""
    sandbox_dir = tmp_path / "sandbox"
    outside_dir = tmp_path / "outside" / "secret.txt"
    outside_dir.parent.mkdir(parents=True, exist_ok=True)
    outside_dir.write_text("secret")

    validator = PathSandboxValidator(sandbox_dir)
    with pytest.raises(PathSandboxError):
        validator.resolve_sandboxed_path(outside_dir)


def test_get_relative_string(tmp_path: Path) -> None:
    """Test converting full sandboxed paths to relative POSIX strings."""
    validator = PathSandboxValidator(tmp_path)
    full_path = tmp_path / "invoices" / "2026" / "inv_001.pdf"
    rel_str = validator.get_relative_string(full_path)
    assert rel_str == "invoices/2026/inv_001.pdf"
