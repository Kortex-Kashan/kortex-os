"""Adversarial and security tests for Backup Engine."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from kortex.engines.backup.constants import BackupScope
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.engine import BackupEngine
from kortex.engines.backup.exceptions import (
    BackupEncryptionError,
    BackupPathSecurityError,
    BackupScopeError,
)
from kortex.engines.backup.models import (
    BackupConfig,
    CreateBackupRequest,
    VerifyBackupRequest,
)
from kortex.engines.backup.repository import BackupRepository
from kortex.engines.backup.verifier import BackupVerifier


def test_adversarial_path_traversal_attacks(tmp_path: Path) -> None:
    """Verify repository aggressively rejects path traversal injection attacks."""
    repo = BackupRepository(tmp_path / "backups")

    malicious_inputs = [
        "../../etc/passwd",
        "..\\..\\windows\\system32\\cmd.exe",
        "/absolute/root/file.kortex-backup",
        "C:\\Windows\\System32\\calc.exe",
        "nested/../../traversal",
        "....//....//escape",
    ]

    for payload in malicious_inputs:
        with pytest.raises(BackupPathSecurityError):
            repo.resolve_artifact_path(payload)


@pytest.mark.asyncio
async def test_no_plaintext_fallback_invariant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CRITICAL SECURITY INVARIANT: If encryption key is missing, fail closed immediately."""
    monkeypatch.delenv("KORTEX_BACKUP_KEY", raising=False)
    monkeypatch.delenv("KORTEX_MASTER_KEY", raising=False)

    config = BackupConfig(
        backup_directory=str(tmp_path / "backups"),
        encryption_required=True,
    )
    engine = BackupEngine(config=config)
    await engine.initialize()

    # Attempting to create backup MUST fail closed with BackupEncryptionError
    with pytest.raises(BackupEncryptionError, match=r"Fail-closed policy enforced|Backup encryption required"):
        await engine.create_backup(CreateBackupRequest(scope=BackupScope.FULL_INSTANCE))


@pytest.mark.asyncio
async def test_invalid_scope_rejection(tmp_path: Path) -> None:
    """Verify engine rejects unknown or unauthorized scopes."""
    key = b"\x55" * 32
    crypto = BackupCryptoManager(key=key)
    config = BackupConfig(backup_directory=str(tmp_path / "backups"))
    engine = BackupEngine(config=config, crypto_manager=crypto)
    await engine.initialize()

    with pytest.raises(BackupScopeError, match="Invalid backup scope"):
        await engine.handle_backup_create(scope="TENANT_RESTRICTED")


def test_adversarial_zip_traversal_entry_rejection(tmp_path: Path) -> None:
    """Verify verifier blocks and flags ZIP files containing traversal entries."""
    repo = BackupRepository(tmp_path / "backups")
    bad_zip_path = repo.resolve_artifact_path("malicious.kortex-backup")

    # Create unencrypted ZIP containing a malicious path
    with zipfile.ZipFile(bad_zip_path, "w") as zf:
        zf.writestr("../../escaped_file.txt", b"malicious payload")
        zf.writestr("manifest.json", b"{}")
        zf.writestr("checksums.json", b"{}")

    verifier = BackupVerifier()
    res = verifier.verify_artifact(
        request=VerifyBackupRequest(backup_id="malicious"),
        repository=repo,
        encryption_key=None,
    )

    assert res.is_valid is False
    assert "Unsafe path in ZIP entry" in (res.error_message or "")


def test_secret_leakage_prevention(tmp_path: Path) -> None:
    """Verify diagnostics and models never expose raw cryptographic key bytes."""
    secret_key = b"SUPER_SECRET_KEY_MATERIAL_123456"
    crypto = BackupCryptoManager(key=secret_key, key_id="key-leak-check")
    config = BackupConfig(backup_directory=str(tmp_path / "backups"))
    engine = BackupEngine(config=config, crypto_manager=crypto)

    diag = engine.get_diagnostics().model_dump_json()
    assert "SUPER_SECRET_KEY_MATERIAL" not in diag
    assert secret_key.hex() not in diag

    health = json.dumps(engine.health())
    assert "SUPER_SECRET_KEY_MATERIAL" not in health
    assert secret_key.hex() not in health
