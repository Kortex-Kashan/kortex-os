"""KORTEX Recovery Engine cryptographic verification and envelope decryption.

Phase 7 — Production Hardening — Recovery Engine.
Consumes AES-256-GCM sealed envelopes created by Backup Engine.
Enforces fail-closed authentication: tag mismatch, missing keys, or tampering
immediately aborts before any filesystem mutation. Zero plaintext fallback.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import logging
import os
from pathlib import Path

from kortex.engines.backup.constants import CHUNK_SIZE_BYTES
from kortex.engines.backup.models import EncryptionMetadata
from kortex.engines.recovery.exceptions import (
    RecoveryEncryptionError,
    RecoveryKeyError,
)
from kortex.engines.security.exceptions import CryptoProviderError
from kortex.engines.security.providers.local_crypto import LocalCrypto

logger = logging.getLogger("kortex.engines.recovery.crypto")

_AES_256_KEY_BYTES = 32
_NONCE_BYTES = 12
_TAG_BYTES = 16


class RecoveryCryptoManager:
    """Manages envelope decryption and cryptographic verification for recovery."""

    def __init__(
        self,
        key: bytes | None = None,
        key_id: str = "kortex-master-key",
    ) -> None:
        self._key_id = key_id
        self._crypto = LocalCrypto()
        self._key = key if key is not None else self._resolve_key_from_env()

    @property
    def key_id(self) -> str:
        """Active logical key identifier."""
        return self._key_id

    @property
    def is_key_available(self) -> bool:
        """True if a valid 32-byte cryptographic key is loaded."""
        return self._key is not None and len(self._key) == _AES_256_KEY_BYTES

    @staticmethod
    def parse_key_bytes(raw_val: str | bytes) -> bytes:
        """Parse raw string, hex, or base64 into exact 32-byte symmetric key."""
        if isinstance(raw_val, bytes) and len(raw_val) == _AES_256_KEY_BYTES:
            return raw_val

        val_str = raw_val.decode("utf-8") if isinstance(raw_val, bytes) else str(raw_val).strip()

        # Case 1: 64-character hex string (32 bytes)
        if len(val_str) == 64:
            with contextlib.suppress(binascii.Error, ValueError):
                raw = binascii.unhexlify(val_str)
                if len(raw) == _AES_256_KEY_BYTES:
                    return raw

        # Case 2: Base64 string
        with contextlib.suppress(Exception):
            raw = base64.b64decode(val_str, validate=True)
            if len(raw) == _AES_256_KEY_BYTES:
                return raw

        # Case 3: Raw UTF-8 bytes representation (32 chars)
        raw_bytes = val_str.encode("utf-8")
        if len(raw_bytes) == _AES_256_KEY_BYTES:
            return raw_bytes

        raise RecoveryKeyError("Provided key material cannot be parsed into a valid 32-byte symmetric key.")

    @staticmethod
    def _resolve_key_from_env() -> bytes | None:
        """Resolve 32-byte key from environment variables."""
        for var_name in ("KORTEX_BACKUP_KEY", "KORTEX_MASTER_KEY"):
            val = os.environ.get(var_name)
            if not val:
                continue
            try:
                return RecoveryCryptoManager.parse_key_bytes(val)
            except RecoveryKeyError:
                logger.debug("Environment variable %s does not contain valid 32-byte key.", var_name)
        return None

    def decrypt_bytes(
        self,
        sealed: bytes,
        key_id: str | None = None,
        expected_decrypted_sha256: str | None = None,
        key_override: bytes | None = None,
    ) -> bytes:
        """Decrypt in-memory sealed ciphertext using AES-256-GCM.

        Sealed envelope structure: Nonce (12B) || Ciphertext || Tag (16B).
        Associated Data (AAD): 'kortex-backup-v1:{key_id}'.
        """
        active_key = key_override or self._key
        if active_key is None or len(active_key) != _AES_256_KEY_BYTES:
            raise RecoveryKeyError(
                "Cannot decrypt backup artifact: No valid 32-byte cryptographic key was provided "
                "or configured in environment ('KORTEX_BACKUP_KEY' or 'KORTEX_MASTER_KEY')."
            )

        if len(sealed) < _NONCE_BYTES + _TAG_BYTES:
            raise RecoveryEncryptionError("Ciphertext payload is truncated or corrupted.")

        nonce = sealed[:_NONCE_BYTES]
        ciphertext = sealed[_NONCE_BYTES:-_TAG_BYTES]
        tag = sealed[-_TAG_BYTES:]

        active_key_id = key_id or self._key_id
        associated_data = f"kortex-backup-v1:{active_key_id}".encode()

        try:
            plaintext = self._crypto.decrypt_aes_gcm(
                nonce=nonce,
                ciphertext=ciphertext,
                tag=tag,
                key=active_key,
                associated_data=associated_data,
            )
        except CryptoProviderError as exc:
            raise RecoveryEncryptionError(
                f"AES-256-GCM authentication/decryption failed (invalid tag, altered ciphertext, or wrong key): {exc}"
            ) from exc

        if expected_decrypted_sha256 is not None:
            calculated_sha = hashlib.sha256(plaintext).hexdigest()
            if calculated_sha != expected_decrypted_sha256:
                raise RecoveryEncryptionError(
                    f"Decrypted payload integrity mismatch: expected SHA-256 {expected_decrypted_sha256}, "
                    f"got {calculated_sha}."
                )

        return plaintext

    def decrypt_file(
        self,
        source_path: Path,
        dest_path: Path,
        metadata: EncryptionMetadata | None = None,
        key_override: bytes | None = None,
    ) -> None:
        """Read encrypted artifact, decrypt, flush, fsync, and atomically replace to dest_path."""
        if not source_path.is_file():
            raise RecoveryEncryptionError(f"Encrypted artifact source not found: '{source_path}'")

        try:
            with source_path.open("rb") as sf:
                sealed = sf.read()
        except OSError as exc:
            raise RecoveryEncryptionError(f"Failed to read encrypted artifact '{source_path}': {exc}") from exc

        key_id = metadata.key_id if metadata else self._key_id
        expected_sha = metadata.decrypted_sha256 if metadata else None

        plaintext = self.decrypt_bytes(
            sealed=sealed,
            key_id=key_id,
            expected_decrypted_sha256=expected_sha,
            key_override=key_override,
        )

        dest_path.parent.mkdir(parents=True, exist_ok=True)
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
            raise RecoveryEncryptionError(f"Failed to write decrypted staging container: {exc}") from exc

    @staticmethod
    def compute_sha256(path: Path) -> tuple[str, int]:
        """Compute SHA-256 digest and byte size of a file via chunked streaming."""
        hasher = hashlib.sha256()
        total_bytes = 0
        try:
            with path.open("rb") as f:
                while chunk := f.read(CHUNK_SIZE_BYTES):
                    hasher.update(chunk)
                    total_bytes += len(chunk)
        except OSError as exc:
            raise RecoveryEncryptionError(f"Failed to compute SHA-256 for '{path}': {exc}") from exc

        return hasher.hexdigest(), total_bytes
