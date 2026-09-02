"""
KORTEX Security Engine — Encrypted Secret Vault (Milestone M2).

Implements `ISecretStore` over `IDataStore` exclusively — per
`engineering_constitution.md` Article 12 and `storage_strategy.md §12`,
Security Engine never opens a database connection, executes raw SQL, or
touches the filesystem directly.

    SecretStore
        |
        v
    IDataStore  (Storage Engine — plain persistence only)

Encryption uses `ICryptoProvider.encrypt_aes_gcm`/`decrypt_aes_gcm` exclusively
— AES-256-GCM is the sole active M2 AEAD (XChaCha20-Poly1305 deferred; standard
ChaCha20-Poly1305 is never substituted for it; the algorithm is never
caller-selectable).

AEAD envelope (ratified, `SecretEntry.encrypted_payload` layout):

    Offset  Length   Field
    0       1 byte   version        (0x01)
    1       1 byte   algorithm_id   (0x01 = AES-256-GCM)
    2       16 bytes key_id         (non-secret identity tag of the configured
                                      master key: sha256(master_key)[:16] — a
                                      public label, NOT an HKDF-derived key;
                                      M2 uses a single master key, per Decision 5)
    18      12 bytes nonce
    30      16 bytes tag
    46      N bytes  ciphertext

AAD (authenticates tenant, handle, and all interpretation-affecting envelope
metadata — altering any of it fails the AEAD tag check):

    uint32_be(len(tenant_id_utf8))      || tenant_id_utf8
    || uint32_be(len(secret_handle_utf8)) || secret_handle_utf8
    || version_byte || algorithm_id_byte || key_id_bytes

Key derivation: single master key + AAD only (Decision 5). No HKDF-derived
per-tenant DEKs in M2.

Master key origin: resolved by the caller (`SecurityEngine`), never by this
class — `SecretStore` always requires an already-decoded 32-byte key via
constructor injection, keeping this module fully testable without a Kernel.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.security.exceptions import (
    MasterKeyError,
    SecretDecryptionError,
    SecretNotFoundError,
    SecretStoreError,
    SecurityEngineError,
)
from kortex.engines.security.interfaces import ICryptoProvider, ISecretStore
from kortex.engines.security.models import SecretEntry, SecretRecord
from kortex.engines.storage.interfaces import IDataStore

_VERSION = 0x01
_ALGORITHM_ID_AES_256_GCM = 0x01
_MASTER_KEY_LENGTH_BYTES = 32
_KEY_ID_LENGTH_BYTES = 16
_NONCE_LENGTH_BYTES = 12
_TAG_LENGTH_BYTES = 16
_ENVELOPE_HEADER_LENGTH = 2 + _KEY_ID_LENGTH_BYTES + _NONCE_LENGTH_BYTES + _TAG_LENGTH_BYTES  # 46
_HEX_KEY_LENGTH_CHARS = 64
_ALGORITHM_LABEL = "aes-256-gcm"


class SecretStore(ISecretStore):
    """M2 encrypted secret vault. Implements `ISecretStore` over `IDataStore`."""

    def __init__(self, data_store: IDataStore, crypto_provider: ICryptoProvider, master_key: bytes) -> None:
        """Initialize SecretStore with an already-decoded 32-byte master key.

        Args:
            data_store: Storage Engine's `IDataStore` — the exclusive persistence path.
            crypto_provider: `ICryptoProvider` implementation (e.g. `LocalCrypto`).
            master_key: Already-decoded 32-byte (256-bit) root encryption key.
                `SecretStore` never resolves this itself — the caller (normally
                `SecurityEngine`) is responsible for sourcing and decoding it,
                which keeps this class fully deterministic and testable.

        Raises:
            MasterKeyError: If `master_key` is not exactly 32 bytes. Never
                includes the key's value in the error message.
        """
        if not isinstance(master_key, bytes) or len(master_key) != _MASTER_KEY_LENGTH_BYTES:
            actual = len(master_key) if isinstance(master_key, bytes) else type(master_key).__name__
            raise MasterKeyError(
                f"SecretStore master key must be exactly {_MASTER_KEY_LENGTH_BYTES} bytes, got {actual}."
            )
        self._data_store = data_store
        self._crypto = crypto_provider
        self._master_key = master_key
        # Non-secret identity tag for the configured master key — a public
        # label for the envelope's key_id field, never a derived encryption key.
        self._key_id = bytes.fromhex(crypto_provider.hash_sha256(master_key))[:_KEY_ID_LENGTH_BYTES]

    # -- Master key decoding (KORTEX_MASTER_KEY contract) ------------------------

    @staticmethod
    def decode_master_key(raw: str) -> bytes:
        """Decode a `KORTEX_MASTER_KEY` configuration value into 32 raw bytes.

        Accepted representations (Decision 1):
            - a 64-character hexadecimal string
            - a Base64 string decoding to exactly 32 bytes

        Fails closed (`MasterKeyError`) for anything else — missing, wrong
        length, or undecodable input. Never logs or includes the raw or
        decoded key value in any exception message.

        Args:
            raw: The configuration string value (already resolved from
                `KORTEX_MASTER_KEY` by the caller).

        Raises:
            MasterKeyError: If `raw` is empty, not a string, or does not
                decode to exactly 32 bytes via hex or Base64.
        """
        if not isinstance(raw, str) or not raw.strip():
            raise MasterKeyError("KORTEX_MASTER_KEY is missing or empty.")
        stripped = raw.strip()

        if len(stripped) == _HEX_KEY_LENGTH_CHARS:
            try:
                decoded = bytes.fromhex(stripped)
            except ValueError as exc:
                raise MasterKeyError(
                    f"KORTEX_MASTER_KEY is {_HEX_KEY_LENGTH_CHARS} characters but is not valid hexadecimal."
                ) from exc
        else:
            import base64

            try:
                decoded = base64.b64decode(stripped, validate=True)
            except Exception as exc:
                raise MasterKeyError(
                    "KORTEX_MASTER_KEY is not a valid 64-character hex string or Base64 string."
                ) from exc

        if len(decoded) != _MASTER_KEY_LENGTH_BYTES:
            raise MasterKeyError(
                f"KORTEX_MASTER_KEY must decode to exactly {_MASTER_KEY_LENGTH_BYTES} bytes, got {len(decoded)}."
            )
        return decoded

    # -- AAD construction ---------------------------------------------------------

    @staticmethod
    def _build_aad(tenant_id: str, secret_handle: str, version: int, algorithm_id: int, key_id: bytes) -> bytes:
        """Build the canonical, length-prefixed AAD binding tenant, handle, and
        every interpretation-affecting envelope metadata field (Decision 4).

        Length-prefixing (not delimiter-joining) makes the encoding injective:
        no two distinct `(tenant_id, secret_handle)` pairs can ever collide,
        regardless of what characters either contains (the canonical secret
        handle format itself already contains `:`, which rules out a naive
        delimited join).
        """
        tenant_bytes = tenant_id.encode("utf-8")
        handle_bytes = secret_handle.encode("utf-8")
        return (
            len(tenant_bytes).to_bytes(4, "big")
            + tenant_bytes
            + len(handle_bytes).to_bytes(4, "big")
            + handle_bytes
            + bytes([version, algorithm_id])
            + key_id
        )

    # -- Envelope packing / unpacking ----------------------------------------------

    def _pack_envelope(self, nonce: bytes, ciphertext: bytes, tag: bytes) -> bytes:
        """Serialize the ratified envelope layout (Decision 3), raw bytes, fixed order."""
        return bytes([_VERSION, _ALGORITHM_ID_AES_256_GCM]) + self._key_id + nonce + tag + ciphertext

    def _unpack_envelope(self, blob: bytes) -> tuple[int, int, bytes, bytes, bytes, bytes]:
        """Parse and validate an envelope blob before any decryption attempt.

        Fails closed on truncation, unsupported version/algorithm, or a
        key_id that does not match the currently-configured master key —
        each of these is rejected explicitly, before ever calling into
        `ICryptoProvider.decrypt_aes_gcm`.
        """
        if not isinstance(blob, bytes) or len(blob) < _ENVELOPE_HEADER_LENGTH:
            raise SecretDecryptionError("Encrypted envelope is truncated or malformed.")

        version = blob[0]
        algorithm_id = blob[1]
        key_id = blob[2 : 2 + _KEY_ID_LENGTH_BYTES]
        nonce_start = 2 + _KEY_ID_LENGTH_BYTES
        nonce = blob[nonce_start : nonce_start + _NONCE_LENGTH_BYTES]
        tag_start = nonce_start + _NONCE_LENGTH_BYTES
        tag = blob[tag_start : tag_start + _TAG_LENGTH_BYTES]
        ciphertext = blob[tag_start + _TAG_LENGTH_BYTES :]

        if version != _VERSION:
            raise SecretDecryptionError(f"Unsupported envelope version: {version}.")
        if algorithm_id != _ALGORITHM_ID_AES_256_GCM:
            raise SecretDecryptionError(f"Unsupported envelope algorithm_id: {algorithm_id}.")
        if key_id != self._key_id:
            raise SecretDecryptionError("Envelope key identity does not match the configured master key.")

        return version, algorithm_id, key_id, nonce, tag, ciphertext

    # -- Encrypt / decrypt wrappers -------------------------------------------------

    def _encrypt(self, tenant_id: str, secret_handle: str, plaintext: str) -> bytes:
        aad = self._build_aad(tenant_id, secret_handle, _VERSION, _ALGORITHM_ID_AES_256_GCM, self._key_id)
        nonce, ciphertext, tag = self._crypto.encrypt_aes_gcm(plaintext.encode("utf-8"), self._master_key, aad)
        return self._pack_envelope(nonce, ciphertext, tag)

    def _decrypt(self, tenant_id: str, secret_handle: str, blob: bytes) -> str:
        version, algorithm_id, key_id, nonce, tag, ciphertext = self._unpack_envelope(blob)
        aad = self._build_aad(tenant_id, secret_handle, version, algorithm_id, key_id)
        try:
            plaintext_bytes = self._crypto.decrypt_aes_gcm(nonce, ciphertext, tag, self._master_key, aad)
        except SecurityEngineError as exc:
            raise SecretDecryptionError(
                "Secret decryption failed: ciphertext, tag, or associated metadata mismatch."
            ) from exc
        return plaintext_bytes.decode("utf-8")

    # -- Storage-failure normalization ---------------------------------------------

    async def _run_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        """Run `action` via `IDataStore.execute_in_transaction`, normalizing any
        failure that is not already a `SecurityEngineError` into `SecretStoreError`
        — an underlying storage failure must never be silently converted into
        `False`/`None`/a missing-secret outcome.
        """
        try:
            return await self._data_store.execute_in_transaction(action)
        except SecurityEngineError:
            raise
        except Exception as exc:
            raise SecretStoreError("Secret storage operation failed.") from exc

    # -- ISecretStore ---------------------------------------------------------------

    async def get_secret(self, secret_handle: str, tenant_id: str) -> str:
        """Resolve a secret handle to its decrypted plaintext value.

        Raises:
            SecretNotFoundError: If no entry exists for `(tenant_id, secret_handle)`.
            SecretDecryptionError: If the stored envelope fails integrity/AAD checks.
            SecretStoreError: If the underlying storage operation fails.
        """

        async def _action(session: AsyncSession) -> bytes:
            stmt = select(SecretRecord).where(
                SecretRecord.tenant_id == tenant_id,
                SecretRecord.secret_handle == secret_handle,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                raise SecretNotFoundError(f"Secret handle '{secret_handle}' not found for this tenant.")
            return record.encrypted_payload

        encrypted_payload = await self._run_in_transaction(_action)
        return self._decrypt(tenant_id, secret_handle, encrypted_payload)

    async def put_secret(self, secret_handle: str, tenant_id: str, plaintext: str) -> SecretEntry:
        """Encrypt and persist a secret under a handle.

        If `secret_handle` already exists for `tenant_id`, replaces the
        ciphertext in place (fresh nonce and tag, generated by this call —
        the underlying `encrypt_aes_gcm` call never reuses a nonce), preserves
        the logical handle, and updates `updated_at` — never versions, never
        exposes the prior plaintext (Decision 6).
        """
        encrypted_payload = self._encrypt(tenant_id, secret_handle, plaintext)

        async def _action(session: AsyncSession) -> SecretEntry:
            stmt = select(SecretRecord).where(
                SecretRecord.tenant_id == tenant_id,
                SecretRecord.secret_handle == secret_handle,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is not None:
                record.encrypted_payload = encrypted_payload
            else:
                record = SecretRecord(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    secret_handle=secret_handle,
                    encrypted_payload=encrypted_payload,
                    algorithm=_ALGORITHM_LABEL,
                )
                session.add(record)
            await session.flush()
            return SecretEntry(
                secret_handle=record.secret_handle,
                encrypted_payload=record.encrypted_payload,
                algorithm=record.algorithm,
                created_at_utc=record.created_at,
                updated_at_utc=record.updated_at,
            )

        return cast(SecretEntry, await self._run_in_transaction(_action))

    async def delete_secret(self, secret_handle: str, tenant_id: str) -> bool:
        """Delete a secret entry.

        Returns:
            True if an entry existed and was deleted; False if no entry
            existed for `(tenant_id, secret_handle)`.

        Raises:
            SecretStoreError: If the underlying storage operation fails —
                never converted into `False`.
        """

        async def _action(session: AsyncSession) -> bool:
            stmt = select(SecretRecord).where(
                SecretRecord.tenant_id == tenant_id,
                SecretRecord.secret_handle == secret_handle,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                return False
            await session.delete(record)
            await session.flush()
            return True

        return cast(bool, await self._run_in_transaction(_action))
