"""KORTEX Backup Engine cryptographic envelope management.

Phase 7 — Production Hardening — Backup Engine.
Enforces AES-256-GCM authenticated encryption using LocalCrypto.
Fails closed if keys are unavailable or misconfigured; zero plaintext fallback.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
from pathlib import Path

from kortex.engines.backup.constants import CHUNK_SIZE_BYTES
from kortex.engines.backup.exceptions import BackupEncryptionError
from kortex.engines.backup.models import EncryptionMetadata
from kortex.engines.security.exceptions import CryptoProviderError
from kortex.engines.security.providers.local_crypto import LocalCrypto

logger = logging.getLogger("kortex.engines.backup.crypto")

_AES_256_KEY_BYTES = 32
_NONCE_BYTES = 12
_TAG_BYTES = 16


class BackupCryptoManager:
    """Manages envelope encryption and verification for backup artifacts."""

    def __init__(
        self,
        key: bytes | None = None,
        key_id: str = "kortex-master-key",
        encryption_required: bool = True,
    ) -> None:
        """Initialize the crypto manager.

        Args:
            key: Raw 32-byte symmetric key. If None, resolved from environment.
            key_id: Logical key identifier to embed in metadata.
            encryption_required: If True, key resolution failure raises immediately.
        """
        self._key_id = key_id
        self._encryption_required = encryption_required
        self._crypto = LocalCrypto()
        self._key = key if key is not None else self._resolve_key_from_env()

        if self._encryption_required and not self.is_key_available:
            raise BackupEncryptionError(
                "Backup encryption is required by default, but no valid 32-byte key was provided "
                "or found in environment ('KORTEX_BACKUP_KEY' or 'KORTEX_MASTER_KEY'). "
                "Fail-closed policy enforced; backup aborted."
            )

    @property
    def key_id(self) -> str:
        """Active logical key identifier."""
        return self._key_id

    @property
    def is_key_available(self) -> bool:
        """True if a valid 32-byte cryptographic key is loaded."""
        return self._key is not None and len(self._key) == _AES_256_KEY_BYTES

    @staticmethod
    def _resolve_key_from_env() -> bytes | None:
        """Attempt to resolve a 32-byte key from environment variables."""
        for var_name in ("KORTEX_BACKUP_KEY", "KORTEX_MASTER_KEY"):
            val = os.environ.get(var_name)
            if not val:
                continue

            # Case 1: 64-character hex string (32 bytes)
            if len(val) == 64:
                try:
                    raw = binascii.unhexlify(val)
                    if len(raw) == _AES_256_KEY_BYTES:
                        return raw
                except (binascii.Error, ValueError):
                    pass

            # Case 2: Base64 string
            try:
                raw = base64.b64decode(val, validate=True)
                if len(raw) == _AES_256_KEY_BYTES:
                    return raw
            except Exception as exc:
                logger.debug("Value for %s is not valid base64: %s", var_name, exc)

            # Case 3: Raw bytes representation (32 chars)
            raw_bytes = val.encode("utf-8")
            if len(raw_bytes) == _AES_256_KEY_BYTES:
                return raw_bytes

        return None

    def encrypt_bytes(self, plaintext: bytes) -> tuple[bytes, EncryptionMetadata]:
        """Encrypt in-memory plaintext using AES-256-GCM.

        Returns:
            Tuple of (ciphertext_payload, EncryptionMetadata).
        """
        if not self.is_key_available:
            raise BackupEncryptionError("Cannot encrypt backup: cryptographic key is unavailable.")

        assert self._key is not None
        decrypted_sha = hashlib.sha256(plaintext).hexdigest()
        associated_data = f"kortex-backup-v1:{self._key_id}".encode()

        try:
            nonce, ciphertext, tag = self._crypto.encrypt_aes_gcm(
                plaintext=plaintext,
                key=self._key,
                associated_data=associated_data,
            )
        except CryptoProviderError as exc:
            raise BackupEncryptionError(f"AES-256-GCM encryption failed: {exc}") from exc

        # Sealed format: 12-byte nonce + ciphertext + 16-byte tag
        sealed = nonce + ciphertext + tag
        encrypted_sha = hashlib.sha256(sealed).hexdigest()

        metadata = EncryptionMetadata(
            algorithm="AES-256-GCM",
            key_id=self._key_id,
            nonce_hex=nonce.hex(),
            tag_hex=tag.hex(),
            encrypted_sha256=encrypted_sha,
            decrypted_sha256=decrypted_sha,
            key_version=1,
        )
        return sealed, metadata

    def decrypt_bytes(self, sealed: bytes, metadata: EncryptionMetadata) -> bytes:
        """Decrypt in-memory sealed ciphertext using AES-256-GCM.

        Returns:
            Decrypted plaintext bytes.
        """
        if not self.is_key_available:
            raise BackupEncryptionError("Cannot decrypt backup: cryptographic key is unavailable.")

        assert self._key is not None
        if len(sealed) < _NONCE_BYTES + _TAG_BYTES:
            raise BackupEncryptionError("Ciphertext payload is truncated or corrupted.")

        nonce = sealed[:_NONCE_BYTES]
        ciphertext = sealed[_NONCE_BYTES:-_TAG_BYTES]
        tag = sealed[-_TAG_BYTES:]

        associated_data = f"kortex-backup-v1:{metadata.key_id}".encode()

        try:
            plaintext = self._crypto.decrypt_aes_gcm(
                nonce=nonce,
                ciphertext=ciphertext,
                tag=tag,
                key=self._key,
                associated_data=associated_data,
            )
        except CryptoProviderError as exc:
            raise BackupEncryptionError(f"AES-256-GCM authentication/decryption failed: {exc}") from exc

        calculated_sha = hashlib.sha256(plaintext).hexdigest()
        if calculated_sha != metadata.decrypted_sha256:
            raise BackupEncryptionError(
                f"Decrypted payload integrity mismatch: expected {metadata.decrypted_sha256}, got {calculated_sha}."
            )

        return plaintext

    def encrypt_file(self, source_path: Path, dest_path: Path) -> EncryptionMetadata:
        """Read source file, encrypt with AES-256-GCM, and atomically write to dest_path."""
        if not source_path.is_file():
            raise BackupEncryptionError(f"Source file not found for encryption: {source_path}")

        try:
            with source_path.open("rb") as sf:
                data = sf.read()
        except OSError as exc:
            raise BackupEncryptionError(f"Failed to read source file for encryption: {exc}") from exc

        sealed, metadata = self.encrypt_bytes(data)

        tmp_dest = dest_path.with_suffix(dest_path.suffix + ".cryptmp")
        try:
            with tmp_dest.open("wb") as df:
                df.write(sealed)
                df.flush()
                os.fsync(df.fileno())
            os.replace(tmp_dest, dest_path)
        except OSError as exc:
            if tmp_dest.exists():
                tmp_dest.unlink(missing_ok=True)
            raise BackupEncryptionError(f"Failed to write encrypted artifact: {exc}") from exc

        return metadata

    def decrypt_file(self, source_path: Path, dest_path: Path, metadata: EncryptionMetadata) -> None:
        """Read encrypted file, decrypt, and write to dest_path."""
        if not source_path.is_file():
            raise BackupEncryptionError(f"Source file not found for decryption: {source_path}")

        try:
            with source_path.open("rb") as sf:
                sealed = sf.read()
        except OSError as exc:
            raise BackupEncryptionError(f"Failed to read encrypted file: {exc}") from exc

        plaintext = self.decrypt_bytes(sealed, metadata)

        tmp_dest = dest_path.with_suffix(dest_path.suffix + ".dectmp")
        try:
            with tmp_dest.open("wb") as df:
                df.write(plaintext)
                df.flush()
                os.fsync(df.fileno())
            os.replace(tmp_dest, dest_path)
        except OSError as exc:
            if tmp_dest.exists():
                tmp_dest.unlink(missing_ok=True)
            raise BackupEncryptionError(f"Failed to write decrypted artifact: {exc}") from exc

    @staticmethod
    def compute_sha256(path: Path) -> tuple[str, int]:
        """Compute SHA-256 digest and byte size of a file using chunked streaming."""
        hasher = hashlib.sha256()
        total_bytes = 0
        try:
            with path.open("rb") as f:
                while chunk := f.read(CHUNK_SIZE_BYTES):
                    hasher.update(chunk)
                    total_bytes += len(chunk)
        except OSError as exc:
            raise BackupEncryptionError(f"Failed to compute SHA-256 for '{path}': {exc}") from exc

        return hasher.hexdigest(), total_bytes
