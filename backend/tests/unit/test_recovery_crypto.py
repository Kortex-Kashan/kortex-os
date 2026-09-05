"""Unit tests for Recovery Engine cryptographic manager and envelope decryption."""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kortex.engines.recovery.crypto import RecoveryCryptoManager
from kortex.engines.recovery.exceptions import (
    RecoveryEncryptionError,
    RecoveryKeyError,
)


def test_crypto_key_resolution_explicit() -> None:
    """Verify explicit 32-byte key resolution."""
    raw_key = b"\x11" * 32
    mgr = RecoveryCryptoManager(key=raw_key, key_id="explicit-key")
    assert mgr.key_id == "explicit-key"
    assert mgr.is_key_available is True


def test_crypto_key_resolution_env_backup_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify resolution of KORTEX_BACKUP_KEY from environment."""
    raw_key = b"\x22" * 32
    hex_key = raw_key.hex()
    monkeypatch.setenv("KORTEX_BACKUP_KEY", hex_key)
    monkeypatch.delenv("KORTEX_MASTER_KEY", raising=False)

    mgr = RecoveryCryptoManager()
    assert mgr.is_key_available is True


def test_crypto_key_resolution_env_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify fallback to KORTEX_MASTER_KEY when BACKUP_KEY is absent."""
    raw_key = b"\x33" * 32
    hex_key = raw_key.hex()
    monkeypatch.delenv("KORTEX_BACKUP_KEY", raising=False)
    monkeypatch.setenv("KORTEX_MASTER_KEY", hex_key)

    mgr = RecoveryCryptoManager()
    assert mgr.is_key_available is True


def test_crypto_fail_closed_on_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """CRITICAL: If key material is absent, fail closed immediately."""
    monkeypatch.delenv("KORTEX_BACKUP_KEY", raising=False)
    monkeypatch.delenv("KORTEX_MASTER_KEY", raising=False)

    mgr = RecoveryCryptoManager()
    assert mgr.is_key_available is False
    with pytest.raises(RecoveryKeyError, match=r"No valid 32-byte cryptographic key"):
        mgr.decrypt_bytes(b"\x00" * 64)


def test_crypto_invalid_key_length_rejection() -> None:
    """Verify rejection of keys that are not exactly 32 bytes (256 bits)."""
    with pytest.raises(RecoveryKeyError, match="Provided key material cannot be parsed"):
        RecoveryCryptoManager.parse_key_bytes(b"\x00" * 16)

    with pytest.raises(RecoveryKeyError, match="Provided key material cannot be parsed"):
        RecoveryCryptoManager.parse_key_bytes(b"\x00" * 64)


def test_crypto_successful_decryption() -> None:
    """Verify successful AES-256-GCM authenticated decryption."""
    key = b"\x44" * 32
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    plaintext = b"KORTEX OS BACKUP PAYLOAD DATA 2026"
    key_id = "test-key-id"
    aad = f"kortex-backup-v1:{key_id}".encode()

    ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext, aad)
    # Envelope format: nonce (12 bytes) || ciphertext_and_tag (where tag is last 16 bytes)
    sealed = nonce + ciphertext_and_tag

    mgr = RecoveryCryptoManager(key=key, key_id=key_id)
    decrypted = mgr.decrypt_bytes(sealed, key_id=key_id)
    assert decrypted == plaintext


def test_crypto_wrong_key_failure() -> None:
    """Verify decryption fails closed when executed with the wrong key."""
    correct_key = b"\x55" * 32
    wrong_key = b"\x66" * 32
    key_id = "test-key-id"

    aesgcm = AESGCM(correct_key)
    nonce = os.urandom(12)
    plaintext = b"CONFIDENTIAL BACKUP"
    aad = f"kortex-backup-v1:{key_id}".encode()
    ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext, aad)
    sealed = nonce + ciphertext_and_tag

    mgr = RecoveryCryptoManager(key=wrong_key, key_id=key_id)
    with pytest.raises(RecoveryEncryptionError, match=r"AES-256-GCM authentication/decryption failed"):
        mgr.decrypt_bytes(sealed, key_id=key_id)


def test_crypto_tampered_ciphertext_failure() -> None:
    """Verify decryption fails closed when ciphertext bytes are modified."""
    key = b"\x77" * 32
    key_id = "test-key-id"
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    plaintext = b"CRITICAL SYSTEM STATE"
    aad = f"kortex-backup-v1:{key_id}".encode()
    ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext, aad)

    # Tamper with the ciphertext byte
    tampered_bytes = bytearray(nonce + ciphertext_and_tag)
    tampered_bytes[20] ^= 0xFF

    mgr = RecoveryCryptoManager(key=key, key_id=key_id)
    with pytest.raises(RecoveryEncryptionError, match=r"AES-256-GCM authentication/decryption failed"):
        mgr.decrypt_bytes(bytes(tampered_bytes), key_id=key_id)


def test_crypto_tampered_aad_failure() -> None:
    """Verify decryption fails closed when associated data differs."""
    key = b"\x88" * 32
    key_id = "correct-key-id"
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    plaintext = b"TENANT DATA"
    aad = f"kortex-backup-v1:{key_id}".encode()
    ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext, aad)
    sealed = nonce + ciphertext_and_tag

    mgr = RecoveryCryptoManager(key=key, key_id="wrong-key-id")
    with pytest.raises(RecoveryEncryptionError, match=r"AES-256-GCM authentication/decryption failed"):
        mgr.decrypt_bytes(sealed, key_id="wrong-key-id")


def test_crypto_truncated_envelope_failure() -> None:
    """Verify failure on envelope shorter than nonce + minimum tag."""
    key = b"\x99" * 32
    mgr = RecoveryCryptoManager(key=key)

    # Shorter than 12B nonce + 16B tag = 28 bytes
    with pytest.raises(RecoveryEncryptionError, match="truncated or corrupted"):
        mgr.decrypt_bytes(b"\x00" * 10)


def test_crypto_secret_leakage_prevention() -> None:
    """Verify raw cryptographic key bytes are never leaked in string representations."""
    secret = b"SUPER_SECRET_RECOVERY_KEY_12345"
    mgr = RecoveryCryptoManager(key=secret, key_id="test-safe")
    rep = str(mgr)
    assert secret.decode("utf-8", errors="ignore") not in rep
    assert secret.hex() not in rep
