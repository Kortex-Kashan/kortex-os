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


# --- DEFECT-001 regression: the platform's canonical "0x"-prefixed hex key
# representation (kernel_bootstrap.py::_resolve_key, docker/entrypoint.sh's
# require_key, docker/.env.example's own documented contract, and the exact
# format apps/desktop/src-tauri/src/secure_keys.rs generates via
# `format!("0x{}", hex_encode(&bytes))`) was never one of the formats
# BackupCryptoManager._resolve_key_from_env recognized, so a real Windows
# desktop install's KORTEX_MASTER_KEY was silently unresolvable and Backup
# failed closed on every operation. -----------------------------------------


def test_crypto_manager_env_0x_prefixed_hex_key_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST A: the exact representation the Windows desktop path generates
    (kernel_bootstrap.py's own canonical "0x" + 64 lowercase hex chars) must
    be accepted, mirroring _resolve_key's identical decoding of the same
    environment variable for every other platform consumer."""
    raw_key = b"\xcc" * 32
    prefixed_hex_key = "0x" + raw_key.hex()
    assert len(prefixed_hex_key) == 66
    monkeypatch.setenv("KORTEX_MASTER_KEY", prefixed_hex_key)

    mgr = BackupCryptoManager(key=None, encryption_required=True)
    assert mgr.is_key_available is True
    assert mgr._key == raw_key


def test_crypto_manager_0x_prefixed_key_supports_kortex_backup_key_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The "0x"-prefixed form must resolve from either accepted variable, not
    only the KORTEX_MASTER_KEY fallback path."""
    raw_key = b"\xdd" * 32
    monkeypatch.setenv("KORTEX_BACKUP_KEY", "0x" + raw_key.hex())
    monkeypatch.delenv("KORTEX_MASTER_KEY", raising=False)

    mgr = BackupCryptoManager(key=None, encryption_required=True)
    assert mgr.is_key_available is True
    assert mgr._key == raw_key


def test_crypto_manager_0x_prefix_does_not_disturb_bare_hex_or_raw_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST C: adding the "0x"-prefixed case must not change resolution of
    the two pre-existing, already-accepted formats it sits alongside."""
    # Bare 64-char hex, unprefixed -- Case 1, unchanged.
    raw_key = b"\xee" * 32
    monkeypatch.setenv("KORTEX_MASTER_KEY", raw_key.hex())
    assert len(raw_key.hex()) == 64
    mgr = BackupCryptoManager(key=None, encryption_required=True)
    assert mgr._key == raw_key

    # Exactly-32-byte raw UTF-8 value -- Case 3, unchanged. Deliberately does
    # NOT start with "0x" so it cannot be mistaken for Case 0.
    monkeypatch.setenv("KORTEX_MASTER_KEY", "y" * 32)
    mgr2 = BackupCryptoManager(key=None, encryption_required=True)
    assert mgr2._key == b"y" * 32


def test_crypto_manager_malformed_0x_prefixed_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST D: a value merely resembling the canonical form -- wrong length
    after the prefix, or non-hex characters -- must still be rejected and
    fail closed, never silently truncated/padded into a different key."""
    # Right prefix, right length, but not valid hex.
    monkeypatch.setenv("KORTEX_MASTER_KEY", "0x" + ("zz" * 32))
    with pytest.raises(BackupEncryptionError, match="Fail-closed policy enforced"):
        BackupCryptoManager(key=None, encryption_required=True)

    # Right prefix, valid hex, but decodes to fewer than 32 bytes.
    monkeypatch.setenv("KORTEX_MASTER_KEY", "0x" + ("ab" * 16))
    with pytest.raises(BackupEncryptionError, match="Fail-closed policy enforced"):
        BackupCryptoManager(key=None, encryption_required=True)

    # Right prefix, valid hex, but too long (33 bytes).
    monkeypatch.setenv("KORTEX_MASTER_KEY", "0x" + ("ab" * 33))
    with pytest.raises(BackupEncryptionError, match="Fail-closed policy enforced"):
        BackupCryptoManager(key=None, encryption_required=True)


def test_crypto_manager_0x_prefixed_key_failure_never_leaks_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST E: the fail-closed exception for a malformed "0x"-prefixed value
    must never include the key material itself."""
    invalid_value = "0x" + ("gg" * 32)  # invalid hex digits -> unresolved
    monkeypatch.setenv("KORTEX_MASTER_KEY", invalid_value)

    with pytest.raises(BackupEncryptionError) as exc_info:
        BackupCryptoManager(key=None, encryption_required=True)

    assert invalid_value not in str(exc_info.value)
    assert "gg" * 32 not in str(exc_info.value)


def test_crypto_manager_desktop_generated_key_real_encrypt_decrypt_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEST F (the critical regression): using EXACTLY the representation
    secure_keys.rs's `load_or_generate_hex_key` persists and injects as
    KORTEX_MASTER_KEY on a real Windows desktop install, the full Backup
    Engine crypto path -- key resolution, AES-256-GCM encryption, and
    decryption -- must succeed end to end. Never asserts against or prints
    the key value itself, only structural/behavioral properties."""
    import os as _os

    desktop_style_key_hex = _os.urandom(32).hex()  # mirrors hex_encode(&bytes) exactly
    desktop_style_key_env_value = "0x" + desktop_style_key_hex  # mirrors format!("0x{}", ...)
    monkeypatch.setenv("KORTEX_MASTER_KEY", desktop_style_key_env_value)

    mgr = BackupCryptoManager(key=None, key_id="desktop-master-key", encryption_required=True)
    assert mgr.is_key_available is True

    plaintext = b"real desktop-produced-key backup payload"
    sealed, metadata = mgr.encrypt_bytes(plaintext)
    assert metadata.algorithm == "AES-256-GCM"
    assert len(sealed) > len(plaintext)

    decrypted = mgr.decrypt_bytes(sealed, metadata)
    assert decrypted == plaintext


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
