"""Unit tests for Backup Engine cryptographic operations and fail-closed policies."""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.exceptions import BackupEncryptionError


def test_crypto_manager_init_with_key() -> None:
    """Verify BackupCryptoManager initializes with a valid 32-byte key."""
    key = b"\x01" * 32
    mgr = BackupCryptoManager(key=key, key_id="custom-key-1", encryption_required=True)
    assert mgr.is_key_available is True
    assert mgr.key_id == "custom-key-1"


def test_crypto_manager_fail_closed_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify fail-closed policy: missing key raises BackupEncryptionError immediately."""
    monkeypatch.delenv("KORTEX_BACKUP_KEY", raising=False)
    monkeypatch.delenv("KORTEX_MASTER_KEY", raising=False)

    with pytest.raises(BackupEncryptionError, match="Fail-closed policy enforced"):
        BackupCryptoManager(key=None, encryption_required=True)


def test_crypto_manager_env_hex_key_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify resolution of 64-char hex key from environment."""
    raw_key = b"\xaa" * 32
    hex_key = raw_key.hex()
    monkeypatch.setenv("KORTEX_BACKUP_KEY", hex_key)

    mgr = BackupCryptoManager(key=None, encryption_required=True)
    assert mgr.is_key_available is True
    assert mgr._key == raw_key


def test_crypto_manager_env_base64_key_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify resolution of base64 key from environment."""
    import base64

    raw_key = b"\xbb" * 32
    b64_key = base64.b64encode(raw_key).decode("ascii")
    monkeypatch.setenv("KORTEX_MASTER_KEY", b64_key)

    mgr = BackupCryptoManager(key=None, encryption_required=True)
    assert mgr.is_key_available is True
    assert mgr._key == raw_key


def test_crypto_bytes_encrypt_decrypt_roundtrip() -> None:
    """Verify roundtrip AES-256-GCM encryption and decryption."""
    key = b"\x42" * 32
    mgr = BackupCryptoManager(key=key, key_id="roundtrip-key")

    plaintext = b"authoritative database and storage payload for testing"
    sealed, metadata = mgr.encrypt_bytes(plaintext)

    assert metadata.algorithm == "AES-256-GCM"
    assert metadata.key_id == "roundtrip-key"
    assert len(sealed) > len(plaintext)

    decrypted = mgr.decrypt_bytes(sealed, metadata)
    assert decrypted == plaintext


def test_crypto_tampering_detection() -> None:
    """Verify that tampering with ciphertext or authentication tag fails closed."""
    key = b"\x99" * 32
    mgr = BackupCryptoManager(key=key)

    plaintext = b"sensitive business records"
    sealed, metadata = mgr.encrypt_bytes(plaintext)

    # Tamper with a middle byte
    tampered_sealed = bytearray(sealed)
    tampered_sealed[15] ^= 0xFF

    with pytest.raises(BackupEncryptionError, match="authentication/decryption failed"):
        mgr.decrypt_bytes(bytes(tampered_sealed), metadata)


def test_crypto_wrong_key_fails() -> None:
    """Verify decryption under wrong key fails closed."""
    key1 = b"\x11" * 32
    key2 = b"\x22" * 32

    mgr1 = BackupCryptoManager(key=key1)
    mgr2 = BackupCryptoManager(key=key2)

    sealed, metadata = mgr1.encrypt_bytes(b"data to protect")

    with pytest.raises(BackupEncryptionError, match="authentication/decryption failed"):
        mgr2.decrypt_bytes(sealed, metadata)


def test_crypto_file_encrypt_decrypt(tmp_path: Path) -> None:
    """Verify file-level encryption and atomic write."""
    key = b"\x55" * 32
    mgr = BackupCryptoManager(key=key)

    source_file = tmp_path / "plain.txt"
    source_file.write_bytes(b"large file content here " * 500)

    encrypted_file = tmp_path / "enc.bin"
    decrypted_file = tmp_path / "restored.txt"

    metadata = mgr.encrypt_file(source_file, encrypted_file)
    assert encrypted_file.is_file()
    assert encrypted_file.stat().st_size > 0

    mgr.decrypt_file(encrypted_file, decrypted_file, metadata)
    assert decrypted_file.read_bytes() == source_file.read_bytes()


def test_compute_sha256(tmp_path: Path) -> None:
    """Verify chunked SHA-256 calculation."""
    test_file = tmp_path / "test_sha.bin"
    content = b"kortex sha verification test content"
    test_file.write_bytes(content)

    import hashlib

    expected_sha = hashlib.sha256(content).hexdigest()
    actual_sha, size = BackupCryptoManager.compute_sha256(test_file)

    assert actual_sha == expected_sha
    assert size == len(content)
