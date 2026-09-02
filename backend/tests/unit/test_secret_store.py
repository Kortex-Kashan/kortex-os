"""Adversarial tests for the Security Engine M2 encrypted `SecretStore`.

Covers: master-key decoding/validation, AEAD envelope construction and
fail-closed parsing, AAD binding (tenant/handle/metadata), cross-tenant and
cross-handle ciphertext-relocation resistance, duplicate-handle replacement,
delete semantics, storage-failure normalization, and plaintext-leakage
prohibitions — proving the ratified M2 architecture decisions, not merely
exercising code paths.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, NoReturn

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.kernel import Kernel
from kortex.engines.security.exceptions import (
    MasterKeyError,
    SecretDecryptionError,
    SecretNotFoundError,
    SecretStoreError,
    SecurityEngineError,
)
from kortex.engines.security.models import SecretEntry
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.security.secrets import SecretStore
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x11" * 32
_OTHER_MASTER_KEY = b"\x22" * 32


async def _make_store(
    tmp_path: Path, master_key: bytes = _TEST_MASTER_KEY
) -> tuple[Kernel, StorageEngine, SecretStore]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "secret_store_test"))
    kernel.register_engine(storage_engine)
    await storage_engine.initialize(kernel)
    await storage_engine.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()
    store = SecretStore(data_store=storage_engine.data, crypto_provider=LocalCrypto(), master_key=master_key)
    return kernel, storage_engine, store


class _FailingDataStore:
    """Fake IDataStore whose transactions always fail — proves storage
    failures normalize to SecretStoreError rather than False/None."""

    async def get_session(self) -> Any:  # pragma: no cover - not exercised
        raise NotImplementedError

    async def execute_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> NoReturn:
        raise RuntimeError("simulated storage outage")


# -- Master key decoding (Decision 1) --------------------------------------------


def test_decode_master_key_accepts_valid_hex() -> None:
    hex_key = _TEST_MASTER_KEY.hex()
    assert SecretStore.decode_master_key(hex_key) == _TEST_MASTER_KEY


def test_decode_master_key_accepts_valid_base64() -> None:
    b64_key = base64.b64encode(_TEST_MASTER_KEY).decode("ascii")
    assert SecretStore.decode_master_key(b64_key) == _TEST_MASTER_KEY


def test_decode_master_key_rejects_missing() -> None:
    for bad in (None, "", "   "):
        with pytest.raises(MasterKeyError):
            SecretStore.decode_master_key(bad)  # type: ignore[arg-type]


def test_decode_master_key_rejects_wrong_length_hex() -> None:
    with pytest.raises(MasterKeyError):
        SecretStore.decode_master_key("aa" * 16)  # 32 chars = 16 bytes, too short


def test_decode_master_key_rejects_64_char_string_that_is_not_valid_hex() -> None:
    """Exactly 64 characters (the hex-length branch) but containing a
    non-hexadecimal character — distinct from the wrong-length case above."""
    not_hex_but_right_length = "g" * 64
    with pytest.raises(MasterKeyError):
        SecretStore.decode_master_key(not_hex_but_right_length)


def test_decode_master_key_rejects_wrong_length_base64() -> None:
    short_key = base64.b64encode(b"\x00" * 16).decode("ascii")
    with pytest.raises(MasterKeyError):
        SecretStore.decode_master_key(short_key)


def test_decode_master_key_rejects_garbage_string() -> None:
    with pytest.raises(MasterKeyError):
        SecretStore.decode_master_key("not-hex-and-not-base64-!!!")


def test_decode_master_key_error_never_exposes_key_material() -> None:
    with pytest.raises(MasterKeyError) as exc_info:
        SecretStore.decode_master_key("not-a-valid-key-encoding")
    assert "not-a-valid-key-encoding" not in str(exc_info.value)


def test_secret_store_constructor_rejects_wrong_length_master_key() -> None:
    crypto = LocalCrypto()

    class _DummyDataStore:
        async def get_session(self) -> Any:  # pragma: no cover
            raise NotImplementedError

        async def execute_in_transaction(  # pragma: no cover
            self, action: Callable[[AsyncSession], Awaitable[Any]]
        ) -> NoReturn:
            raise NotImplementedError

    with pytest.raises(MasterKeyError):
        SecretStore(data_store=_DummyDataStore(), crypto_provider=crypto, master_key=b"\x00" * 16)


def test_secret_store_constructor_error_never_exposes_key_material() -> None:
    crypto = LocalCrypto()
    marker_key = b"\xde\xad\xbe\xef"

    class _DummyDataStore:
        async def get_session(self) -> Any:  # pragma: no cover
            raise NotImplementedError

        async def execute_in_transaction(  # pragma: no cover
            self, action: Callable[[AsyncSession], Awaitable[Any]]
        ) -> NoReturn:
            raise NotImplementedError

    with pytest.raises(MasterKeyError) as exc_info:
        SecretStore(data_store=_DummyDataStore(), crypto_provider=crypto, master_key=marker_key)
    assert marker_key.hex() not in str(exc_info.value)


# -- Valid round trip -------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_then_get_round_trip(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)

    entry = await store.put_secret("secret:kortex/smtp_pass", "tenant-a", "hunter2")
    plaintext = await store.get_secret("secret:kortex/smtp_pass", "tenant-a")

    assert plaintext == "hunter2"
    assert isinstance(entry, SecretEntry)
    assert entry.algorithm == "aes-256-gcm"
    assert entry.encrypted_payload != b"hunter2"  # never stores plaintext


@pytest.mark.asyncio
async def test_get_missing_secret_raises_not_found(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)

    with pytest.raises(SecretNotFoundError):
        await store.get_secret("secret:kortex/does-not-exist", "tenant-a")


@pytest.mark.asyncio
async def test_get_never_returns_none_or_empty_string_for_missing_secret(tmp_path: Path) -> None:
    """`get_secret`'s own return type is `str` — this proves the failure path
    is an exception, never a value a caller could mistake for a real secret."""
    _kernel, _storage, store = await _make_store(tmp_path)

    with pytest.raises(SecretNotFoundError):
        result = await store.get_secret("secret:kortex/missing", "tenant-a")
        assert result is not None and result != ""  # unreachable — documents intent


# -- Cross-tenant / cross-handle isolation (Decisions 4, 5) ----------------------


@pytest.mark.asyncio
async def test_cross_tenant_access_denied(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    await store.put_secret("secret:kortex/shared-name", "tenant-a", "tenant-a-secret")

    with pytest.raises(SecretNotFoundError):
        await store.get_secret("secret:kortex/shared-name", "tenant-b")


@pytest.mark.asyncio
async def test_wrong_tenant_aad_fails_closed_on_direct_ciphertext_relocation(tmp_path: Path) -> None:
    """Simulates an attacker copying tenant-a's stored row into tenant-b's
    namespace at the storage layer (bypassing the store's own lookup) — the
    AAD binding must still cause decryption to fail."""
    _kernel, _storage, store = await _make_store(tmp_path)
    await store.put_secret("secret:kortex/x", "tenant-a", "tenant-a-secret")

    # Read the raw envelope as tenant-a produced it, then attempt to decrypt
    # it as if it belonged to tenant-b (same handle, different tenant).
    async def _read_raw(session: AsyncSession) -> bytes:
        from sqlalchemy import select

        from kortex.engines.security.models import SecretRecord

        res = await session.execute(
            select(SecretRecord).where(
                SecretRecord.tenant_id == "tenant-a", SecretRecord.secret_handle == "secret:kortex/x"
            )
        )
        return res.scalar_one().encrypted_payload

    raw_envelope = await store._data_store.execute_in_transaction(_read_raw)

    with pytest.raises(SecretDecryptionError):
        store._decrypt("tenant-b", "secret:kortex/x", raw_envelope)


@pytest.mark.asyncio
async def test_wrong_handle_aad_fails_closed_on_direct_ciphertext_relocation(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    await store.put_secret("secret:kortex/original-handle", "tenant-a", "value")

    async def _read_raw(session: AsyncSession) -> bytes:
        from sqlalchemy import select

        from kortex.engines.security.models import SecretRecord

        res = await session.execute(
            select(SecretRecord).where(
                SecretRecord.tenant_id == "tenant-a", SecretRecord.secret_handle == "secret:kortex/original-handle"
            )
        )
        return res.scalar_one().encrypted_payload

    raw_envelope = await store._data_store.execute_in_transaction(_read_raw)

    with pytest.raises(SecretDecryptionError):
        store._decrypt("tenant-a", "secret:kortex/different-handle", raw_envelope)


@pytest.mark.asyncio
async def test_ciphertext_relocation_between_tenants_via_public_api_fails_closed(tmp_path: Path) -> None:
    """End-to-end: even with the same handle string, tenant-b can never read
    a secret that tenant-a stored, through the public `ISecretStore` API alone."""
    _kernel, _storage, store = await _make_store(tmp_path)
    await store.put_secret("secret:kortex/rotating", "tenant-a", "tenant-a-only")
    await store.put_secret("secret:kortex/rotating", "tenant-b", "tenant-b-only")

    assert await store.get_secret("secret:kortex/rotating", "tenant-a") == "tenant-a-only"
    assert await store.get_secret("secret:kortex/rotating", "tenant-b") == "tenant-b-only"


# -- Ciphertext / nonce / tag / AAD tampering (reuses M1 crypto, proven again at this layer) --


@pytest.mark.asyncio
async def test_ciphertext_tampering_fails_closed(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    envelope = store._encrypt("tenant-a", "secret:kortex/x", "value")
    tampered = envelope[:-1] + bytes([envelope[-1] ^ 0xFF])  # flip last ciphertext byte

    with pytest.raises(SecretDecryptionError):
        store._decrypt("tenant-a", "secret:kortex/x", tampered)


@pytest.mark.asyncio
async def test_tag_tampering_fails_closed(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    envelope = store._encrypt("tenant-a", "secret:kortex/x", "value")
    # Tag occupies offset 30..46
    tampered = bytearray(envelope)
    tampered[30] ^= 0xFF
    with pytest.raises(SecretDecryptionError):
        store._decrypt("tenant-a", "secret:kortex/x", bytes(tampered))


@pytest.mark.asyncio
async def test_nonce_tampering_fails_closed(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    envelope = store._encrypt("tenant-a", "secret:kortex/x", "value")
    # Nonce occupies offset 18..30
    tampered = bytearray(envelope)
    tampered[18] ^= 0xFF
    with pytest.raises(SecretDecryptionError):
        store._decrypt("tenant-a", "secret:kortex/x", bytes(tampered))


@pytest.mark.asyncio
async def test_key_id_tampering_fails_closed(tmp_path: Path) -> None:
    """key_id occupies offset 2..18 and is authenticated via AAD — tampering
    with it must fail the AEAD check, not silently accept a different key identity."""
    _kernel, _storage, store = await _make_store(tmp_path)
    envelope = store._encrypt("tenant-a", "secret:kortex/x", "value")
    tampered = bytearray(envelope)
    tampered[2] ^= 0xFF
    with pytest.raises(SecretDecryptionError):
        store._decrypt("tenant-a", "secret:kortex/x", bytes(tampered))


# -- Envelope truncation / version / algorithm (Decision 3) ---------------------


@pytest.mark.asyncio
async def test_envelope_truncation_fails_closed(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    envelope = store._encrypt("tenant-a", "secret:kortex/x", "value")

    for truncated_length in (0, 1, 10, 45):  # 45 < the 46-byte minimum header
        with pytest.raises(SecretDecryptionError):
            store._decrypt("tenant-a", "secret:kortex/x", envelope[:truncated_length])


@pytest.mark.asyncio
async def test_unsupported_envelope_version_fails_closed(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    envelope = store._encrypt("tenant-a", "secret:kortex/x", "value")
    tampered = bytes([0x02]) + envelope[1:]  # version byte -> unsupported value

    with pytest.raises(SecretDecryptionError):
        store._decrypt("tenant-a", "secret:kortex/x", tampered)


@pytest.mark.asyncio
async def test_unsupported_envelope_algorithm_fails_closed(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    envelope = store._encrypt("tenant-a", "secret:kortex/x", "value")
    tampered = envelope[:1] + bytes([0x99]) + envelope[2:]  # algorithm_id -> unsupported

    with pytest.raises(SecretDecryptionError):
        store._decrypt("tenant-a", "secret:kortex/x", tampered)


@pytest.mark.asyncio
async def test_envelope_encrypted_under_different_master_key_fails_closed(tmp_path: Path) -> None:
    """A different master key produces a different key_id — decrypting under
    the currently-configured store must reject it before even attempting AEAD."""
    _kernel, _storage, store = await _make_store(tmp_path)
    _kernel2, _storage2, other_store = await _make_store(tmp_path, master_key=_OTHER_MASTER_KEY)

    envelope_from_other_key = other_store._encrypt("tenant-a", "secret:kortex/x", "value")

    with pytest.raises(SecretDecryptionError):
        store._decrypt("tenant-a", "secret:kortex/x", envelope_from_other_key)


# -- Duplicate handle replacement (Decision 6) -----------------------------------


@pytest.mark.asyncio
async def test_duplicate_handle_replaces_ciphertext_in_place(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    first = await store.put_secret("secret:kortex/rotatable", "tenant-a", "old-value")
    second = await store.put_secret("secret:kortex/rotatable", "tenant-a", "new-value")

    assert await store.get_secret("secret:kortex/rotatable", "tenant-a") == "new-value"
    assert first.encrypted_payload != second.encrypted_payload  # fresh nonce/tag/ciphertext


@pytest.mark.asyncio
async def test_duplicate_handle_replacement_generates_fresh_nonce(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    first = await store.put_secret("secret:kortex/rotatable2", "tenant-a", "same-value")
    second = await store.put_secret("secret:kortex/rotatable2", "tenant-a", "same-value")

    nonce_1 = first.encrypted_payload[18:30]
    nonce_2 = second.encrypted_payload[18:30]
    assert nonce_1 != nonce_2  # identical plaintext, still a fresh nonce every time


@pytest.mark.asyncio
async def test_duplicate_handle_replacement_updates_timestamp(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    first = await store.put_secret("secret:kortex/ts", "tenant-a", "v1")
    second = await store.put_secret("secret:kortex/ts", "tenant-a", "v2")

    assert second.updated_at_utc >= first.updated_at_utc


@pytest.mark.asyncio
async def test_duplicate_handle_replacement_never_exposes_old_plaintext(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    await store.put_secret("secret:kortex/overwrite", "tenant-a", "original-secret-value")
    second = await store.put_secret("secret:kortex/overwrite", "tenant-a", "replacement-value")

    assert b"original-secret-value" not in second.encrypted_payload
    assert await store.get_secret("secret:kortex/overwrite", "tenant-a") == "replacement-value"


# -- Delete semantics (Decision 7) -----------------------------------------------


@pytest.mark.asyncio
async def test_delete_existing_secret_returns_true(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    await store.put_secret("secret:kortex/deletable", "tenant-a", "value")

    assert await store.delete_secret("secret:kortex/deletable", "tenant-a") is True
    with pytest.raises(SecretNotFoundError):
        await store.get_secret("secret:kortex/deletable", "tenant-a")


@pytest.mark.asyncio
async def test_delete_missing_secret_returns_false(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)

    assert await store.delete_secret("secret:kortex/never-existed", "tenant-a") is False


@pytest.mark.asyncio
async def test_delete_is_tenant_scoped(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    await store.put_secret("secret:kortex/shared", "tenant-a", "a-value")
    await store.put_secret("secret:kortex/shared", "tenant-b", "b-value")

    assert await store.delete_secret("secret:kortex/shared", "tenant-a") is True
    assert await store.get_secret("secret:kortex/shared", "tenant-b") == "b-value"


# -- Storage failure normalization (Decisions 6, 7) ------------------------------


@pytest.mark.asyncio
async def test_get_secret_storage_failure_raises_secret_store_error_not_false() -> None:
    store = SecretStore(data_store=_FailingDataStore(), crypto_provider=LocalCrypto(), master_key=_TEST_MASTER_KEY)

    with pytest.raises(SecretStoreError):
        await store.get_secret("secret:kortex/x", "tenant-a")


@pytest.mark.asyncio
async def test_put_secret_storage_failure_raises_secret_store_error() -> None:
    store = SecretStore(data_store=_FailingDataStore(), crypto_provider=LocalCrypto(), master_key=_TEST_MASTER_KEY)

    with pytest.raises(SecretStoreError):
        await store.put_secret("secret:kortex/x", "tenant-a", "value")


@pytest.mark.asyncio
async def test_delete_secret_storage_failure_raises_secret_store_error_never_false() -> None:
    store = SecretStore(data_store=_FailingDataStore(), crypto_provider=LocalCrypto(), master_key=_TEST_MASTER_KEY)

    with pytest.raises(SecretStoreError):
        await store.delete_secret("secret:kortex/x", "tenant-a")


# -- Malformed Python input normalization (M2.1 hardening, Decision 8) ----------


def test_local_crypto_hash_sha256_normalizes_non_bytes_input() -> None:
    crypto = LocalCrypto()
    with pytest.raises(SecurityEngineError):
        crypto.hash_sha256("not-bytes")  # type: ignore[arg-type]


def test_local_crypto_verify_sha256_normalizes_non_bytes_and_non_str_input() -> None:
    crypto = LocalCrypto()
    with pytest.raises(SecurityEngineError):
        crypto.verify_sha256(None, "abc")  # type: ignore[arg-type]
    with pytest.raises(SecurityEngineError):
        crypto.verify_sha256(b"data", None)  # type: ignore[arg-type]


def test_local_crypto_encrypt_aes_gcm_normalizes_non_bytes_input() -> None:
    crypto = LocalCrypto()
    with pytest.raises(SecurityEngineError):
        crypto.encrypt_aes_gcm(None, b"\x00" * 32)  # type: ignore[arg-type]
    with pytest.raises(SecurityEngineError):
        crypto.encrypt_aes_gcm(b"data", 12345)  # type: ignore[arg-type]
    with pytest.raises(SecurityEngineError):
        crypto.encrypt_aes_gcm(b"data", b"\x00" * 32, associated_data="not-bytes")  # type: ignore[arg-type]


def test_local_crypto_decrypt_aes_gcm_normalizes_non_bytes_input() -> None:
    crypto = LocalCrypto()
    key = b"\x00" * 32
    nonce, ciphertext, tag = crypto.encrypt_aes_gcm(b"data", key)
    with pytest.raises(SecurityEngineError):
        crypto.decrypt_aes_gcm(None, ciphertext, tag, key)  # type: ignore[arg-type]
    with pytest.raises(SecurityEngineError):
        crypto.decrypt_aes_gcm(nonce, None, tag, key)  # type: ignore[arg-type]
    with pytest.raises(SecurityEngineError):
        crypto.decrypt_aes_gcm(nonce, ciphertext, None, key)  # type: ignore[arg-type]
    with pytest.raises(SecurityEngineError):
        crypto.decrypt_aes_gcm(nonce, ciphertext, tag, None)  # type: ignore[arg-type]


def test_local_crypto_verify_ed25519_never_raises_for_malformed_types_returns_false() -> None:
    """`verify_ed25519`'s documented contract is 'never raises, returns bool' —
    M2.1 hardening preserves this exact contract for wrong-type input too."""
    crypto = LocalCrypto()
    assert crypto.verify_ed25519(None, b"\x00" * 64, b"\x00" * 32) is False  # type: ignore[arg-type]
    assert crypto.verify_ed25519(b"data", None, b"\x00" * 32) is False  # type: ignore[arg-type]
    assert crypto.verify_ed25519(b"data", b"\x00" * 64, None) is False  # type: ignore[arg-type]


# -- Plaintext leakage prohibitions -----------------------------------------------


@pytest.mark.asyncio
async def test_master_key_never_appears_in_any_secret_entry_repr(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    entry = await store.put_secret("secret:kortex/x", "tenant-a", "value")

    assert _TEST_MASTER_KEY.hex() not in repr(entry)
    assert _TEST_MASTER_KEY.hex() not in str(entry)
    assert _TEST_MASTER_KEY not in entry.encrypted_payload


@pytest.mark.asyncio
async def test_plaintext_value_never_appears_in_stored_envelope(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    entry = await store.put_secret("secret:kortex/x", "tenant-a", "extremely-sensitive-value-12345")

    assert b"extremely-sensitive-value-12345" not in entry.encrypted_payload


@pytest.mark.asyncio
async def test_decryption_failure_error_never_contains_ciphertext_or_key(tmp_path: Path) -> None:
    _kernel, _storage, store = await _make_store(tmp_path)
    envelope = store._encrypt("tenant-a", "secret:kortex/x", "sensitive-plaintext-value")
    tampered = envelope[:-1] + bytes([envelope[-1] ^ 0xFF])

    with pytest.raises(SecretDecryptionError) as exc_info:
        store._decrypt("tenant-a", "secret:kortex/x", tampered)

    assert "sensitive-plaintext-value" not in str(exc_info.value)
    assert _TEST_MASTER_KEY.hex() not in str(exc_info.value)


# -- AEAD algorithm ratification (Decision 2) ------------------------------------


@pytest.mark.asyncio
async def test_secret_entry_always_records_aes_256_gcm_algorithm(tmp_path: Path) -> None:
    """No XChaCha20-Poly1305, no ChaCha20-Poly1305 substitution, no
    caller-selectable algorithm — every persisted entry records the fixed
    'aes-256-gcm' label, and the envelope's algorithm_id byte (offset 1) is
    always 0x01 regardless of input."""
    _kernel, _storage, store = await _make_store(tmp_path)

    entry = await store.put_secret("secret:kortex/algo-check", "tenant-a", "value")

    assert entry.algorithm == "aes-256-gcm"
    assert entry.encrypted_payload[1] == 0x01
