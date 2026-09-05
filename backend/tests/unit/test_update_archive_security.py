"""Unit tests for Update Engine archive security defenses.

Phase 7 — Production Hardening — Update Engine.
Verifies defense-in-depth against hostile ZIP archives:
Zip slip traversal, absolute paths, drive letters, UNC paths, symlinks, bombs, and duplicates.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from kortex.engines.update.constants import (
    MAX_FILE_COUNT,
    MAX_SINGLE_FILE_SIZE_BYTES,
)
from kortex.engines.update.exceptions import (
    UpdateArchiveSecurityError,
    UpdatePathTraversalError,
    UpdateZipBombError,
)
from kortex.engines.update.staging import UpdateStagingManager


def create_zip_in_memory(files: dict[str, bytes | str]) -> bytes:
    """Helper to create a zip archive in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(name, data)
    return buf.getvalue()


def test_valid_archive_passes(tmp_path: Path) -> None:
    """Verify that a safe, standard zip archive passes validation."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    zip_bytes = create_zip_in_memory(
        {
            "backend/src/kortex/core/version.py": "VERSION = '0.2.0'\n",
            "README.md": "# KORTEX Update\n",
        }
    )
    zip_path = tmp_path / "update.zip"
    zip_path.write_bytes(zip_bytes)

    staging.validate_archive_security(zip_path)


def test_path_traversal_dot_dot_rejected(tmp_path: Path) -> None:
    """Verify rejection of directory traversal '..' in zip member name."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../../evil.py", "malicious_code()")

    zip_path = tmp_path / "evil_traversal.zip"
    zip_path.write_bytes(buf.getvalue())

    with pytest.raises(UpdatePathTraversalError) as exc_info:
        staging.validate_archive_security(zip_path)
    assert "traversal" in str(exc_info.value).lower()


def test_absolute_path_rejected(tmp_path: Path) -> None:
    """Verify rejection of absolute Unix-style paths in zip member name."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("/etc/kortex.conf", "malicious_content")

    zip_path = tmp_path / "evil_abs.zip"
    zip_path.write_bytes(buf.getvalue())

    with pytest.raises(UpdatePathTraversalError) as exc_info:
        staging.validate_archive_security(zip_path)
    assert "absolute" in str(exc_info.value).lower()


def test_drive_letter_colon_rejected(tmp_path: Path) -> None:
    """Verify rejection of Windows drive letter or colon paths."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("C:/Windows/System32/kortex.dll", "payload")

    zip_path = tmp_path / "evil_drive.zip"
    zip_path.write_bytes(buf.getvalue())

    with pytest.raises(UpdatePathTraversalError) as exc_info:
        staging.validate_archive_security(zip_path)
    assert "colon" in str(exc_info.value).lower() or "drive" in str(exc_info.value).lower()


def test_symlink_member_rejected(tmp_path: Path) -> None:
    """Verify rejection of symlink members in archive."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("symlink_target")
        # POSIX symlink file type attribute: S_IFLNK (0o120000) in upper 16 bits
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "/etc/passwd")

    zip_path = tmp_path / "evil_symlink.zip"
    zip_path.write_bytes(buf.getvalue())

    with pytest.raises(UpdateArchiveSecurityError) as exc_info:
        staging.validate_archive_security(zip_path)
    assert "symlink" in str(exc_info.value).lower()


def test_single_file_size_limit_rejected(tmp_path: Path) -> None:
    """Verify rejection when a single file exceeds MAX_SINGLE_FILE_SIZE_BYTES."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    zip_path = tmp_path / "evil_large.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test.txt", "data")
    zip_path.write_bytes(buf.getvalue())

    fake_member = zipfile.ZipInfo("large_file.dat")
    fake_member.file_size = MAX_SINGLE_FILE_SIZE_BYTES + 1

    from unittest.mock import patch

    with patch("zipfile.ZipFile.infolist", return_value=[fake_member]):
        with pytest.raises(UpdateZipBombError) as exc_info:
            staging.validate_archive_security(zip_path)
        assert "exceeds" in str(exc_info.value)


def test_file_count_limit_rejected(tmp_path: Path) -> None:
    """Verify rejection when member count exceeds MAX_FILE_COUNT."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(MAX_FILE_COUNT + 1):
            zf.writestr(f"file_{i}.txt", "x")

    zip_path = tmp_path / "evil_count.zip"
    zip_path.write_bytes(buf.getvalue())

    with pytest.raises(UpdateZipBombError) as exc_info:
        staging.validate_archive_security(zip_path)
    assert "limit" in str(exc_info.value).lower()


def test_unc_path_rejected(tmp_path: Path) -> None:
    """Verify rejection of Windows UNC-style paths (\\\\server\\share\\...) in zip member name."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("\\\\attacker-host\\share\\payload.dll", "payload")

    zip_path = tmp_path / "evil_unc.zip"
    zip_path.write_bytes(buf.getvalue())

    with pytest.raises(UpdatePathTraversalError) as exc_info:
        staging.validate_archive_security(zip_path)
    assert "absolute" in str(exc_info.value).lower()


def test_extraction_never_creates_filesystem_links(tmp_path: Path) -> None:
    """Verify secure extraction never creates a symlink or hardlink on disk, regardless of
    archive member metadata -- extraction always writes plain regular file bytes via
    copyfileobj, so a 'hardlink escape' (for which the ZIP format has no representable
    member type) is structurally impossible, not merely undefended.
    """
    from unittest.mock import patch

    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    zip_bytes = create_zip_in_memory({"app/safe_file.py": "print('ok')\n"})
    zip_path = tmp_path / "safe.zip"
    zip_path.write_bytes(zip_bytes)

    with (
        patch("os.link", side_effect=AssertionError("os.link must never be called during extraction")),
        patch("os.symlink", side_effect=AssertionError("os.symlink must never be called during extraction")),
    ):
        staged_dir = staging.extract_staged_archive(zip_path, "upd-linktest-01")

    assert (staged_dir / "app" / "safe_file.py").is_file()


def test_duplicate_entries_rejected(tmp_path: Path) -> None:
    """Verify rejection when zip contains duplicate entry filenames."""
    staging = UpdateStagingManager(staging_base_dir=tmp_path / "staging")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app/main.py", "v1")
        zf.writestr("app/main.py", "v2")

    zip_path = tmp_path / "evil_dup.zip"
    zip_path.write_bytes(buf.getvalue())

    with pytest.raises(UpdateArchiveSecurityError) as exc_info:
        staging.validate_archive_security(zip_path)
    assert "duplicate" in str(exc_info.value).lower()
