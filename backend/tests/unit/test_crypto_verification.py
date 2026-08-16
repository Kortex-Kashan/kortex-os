"""Adversarial cryptographic tests for the Security Engine M1 crypto provider
and verification facade.

Proves security properties (fail-closed on tampering, no algorithm downgrade,
no nonce reuse, no key-material leakage) rather than merely exercising code
paths. Per the M1 security standard: no absolute "unbreachable" claims — these
tests demonstrate specific, defensible, adversarially-verified properties.
"""

from __future__ import annotations

import pytest

from kortex.engines.security.crypto import VerificationService
from kortex.engines.security.exceptions import CryptoProviderError, InvalidSignatureError
from kortex.engines.security.models import CryptographicSignature
from kortex.engines.security.providers.local_crypto import LocalCrypto

# Standard FIPS 180-4 SHA-256 test vector.
_SHA256_ABC_KNOWN_VECTOR = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.fixture()
def crypto() -> LocalCrypto:
    return LocalCrypto()


# -- SHA-256 -------------------------------------------------------------------


def test_sha256_known_test_vector(crypto: LocalCrypto) -> None:
    assert crypto.hash_sha256(b"abc") == _SHA256_ABC_KNOWN_VECTOR


def test_sha256_is_deterministic(crypto: LocalCrypto) -> None:
    assert crypto.hash_sha256(b"same input") == crypto.hash_sha256(b"same input")


def test_verify_sha256_accepts_matching_hash(crypto: LocalCrypto) -> None:
    digest = crypto.hash_sha256(b"payload")
    assert crypto.verify_sha256(b"payload", digest) is True


def test_verify_sha256_rejects_mismatched_hash(crypto: LocalCrypto) -> None:
    digest = crypto.hash_sha256(b"payload")
    assert crypto.verify_sha256(b"different payload", digest) is False


# -- Ed25519: modified payload / wrong key --------------------------------------


def test_ed25519_sign_and_verify_round_trip(crypto: LocalCrypto) -> None:
    private_key, public_key = crypto.generate_ed25519_keypair()
    signature = crypto.sign_ed25519(b"important message", private_key)
    assert crypto.verify_ed25519(b"important message", signature, public_key) is True


def test_ed25519_modified_payload_fails_verification(crypto: LocalCrypto) -> None:
    """A valid signature over the original payload must NOT verify against a
    one-byte-modified payload."""
    private_key, public_key = crypto.generate_ed25519_keypair()
    original = b"transfer:100:account-A"
    tampered = b"transfer:900:account-A"  # one meaningful byte changed
    signature = crypto.sign_ed25519(original, private_key)
    assert crypto.verify_ed25519(tampered, signature, public_key) is False


def test_ed25519_wrong_public_key_fails_verification(crypto: LocalCrypto) -> None:
    """A signature created by Key A must NOT verify under Key B's public key."""
    private_key_a, _public_key_a = crypto.generate_ed25519_keypair()
    _private_key_b, public_key_b = crypto.generate_ed25519_keypair()
    signature = crypto.sign_ed25519(b"message", private_key_a)
    assert crypto.verify_ed25519(b"message", signature, public_key_b) is False


def test_ed25519_invalid_signature_length_fails_closed(crypto: LocalCrypto) -> None:
    _private_key, public_key = crypto.generate_ed25519_keypair()
    assert crypto.verify_ed25519(b"message", b"too-short", public_key) is False


def test_ed25519_invalid_public_key_length_fails_closed(crypto: LocalCrypto) -> None:
    private_key, _public_key = crypto.generate_ed25519_keypair()
    signature = crypto.sign_ed25519(b"message", private_key)
    assert crypto.verify_ed25519(b"message", signature, b"not-a-real-key") is False


def test_ed25519_invalid_private_key_material_raises_explicitly(crypto: LocalCrypto) -> None:
    """Invalid signing key material must raise explicitly, never silently sign
    with substitute/default key material."""
    with pytest.raises(CryptoProviderError):
        crypto.sign_ed25519(b"message", b"garbage-not-a-key")


# -- AES-256-GCM: tampering ------------------------------------------------------


def test_aes_gcm_encrypt_decrypt_round_trip(crypto: LocalCrypto) -> None:
    key = b"\x11" * 32
    nonce, ciphertext, tag = crypto.encrypt_aes_gcm(b"top secret plaintext", key)
    plaintext = crypto.decrypt_aes_gcm(nonce, ciphertext, tag, key)
    assert plaintext == b"top secret plaintext"


def test_aes_gcm_ciphertext_tampering_fails_closed(crypto: LocalCrypto) -> None:
    key = b"\x22" * 32
    nonce, ciphertext, tag = crypto.encrypt_aes_gcm(b"plaintext data", key)
    tampered_ciphertext = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]
    with pytest.raises(CryptoProviderError):
        crypto.decrypt_aes_gcm(nonce, tampered_ciphertext, tag, key)


def test_aes_gcm_tag_tampering_fails_closed(crypto: LocalCrypto) -> None:
    key = b"\x33" * 32
    nonce, ciphertext, tag = crypto.encrypt_aes_gcm(b"plaintext data", key)
    tampered_tag = bytes([tag[0] ^ 0xFF]) + tag[1:]
    with pytest.raises(CryptoProviderError):
        crypto.decrypt_aes_gcm(nonce, ciphertext, tampered_tag, key)


def test_aes_gcm_aad_tampering_fails_closed(crypto: LocalCrypto) -> None:
    key = b"\x44" * 32
    nonce, ciphertext, tag = crypto.encrypt_aes_gcm(b"plaintext data", key, associated_data=b"aad-A")
    with pytest.raises(CryptoProviderError):
        crypto.decrypt_aes_gcm(nonce, ciphertext, tag, key, associated_data=b"aad-B")


def test_aes_gcm_missing_aad_on_decrypt_fails_closed(crypto: LocalCrypto) -> None:
    key = b"\x55" * 32
    nonce, ciphertext, tag = crypto.encrypt_aes_gcm(b"plaintext data", key, associated_data=b"aad-A")
    with pytest.raises(CryptoProviderError):
        crypto.decrypt_aes_gcm(nonce, ciphertext, tag, key, associated_data=None)


def test_aes_gcm_nonce_uniqueness_large_sample(crypto: LocalCrypto) -> None:
    """A statistically meaningful sample of encryptions must never collide on
    nonce — proves fresh, unique nonce generation per call."""
    key = b"\x66" * 32
    sample_size = 5000
    nonces = {crypto.encrypt_aes_gcm(b"payload", key)[0] for _ in range(sample_size)}
    assert len(nonces) == sample_size
    for nonce in nonces:
        assert len(nonce) == 12  # standard 96-bit GCM nonce


@pytest.mark.parametrize("key_length", [0, 1, 16, 24, 31, 33, 64])
def test_aes_gcm_invalid_key_length_raises_on_encrypt(crypto: LocalCrypto, key_length: int) -> None:
    with pytest.raises(CryptoProviderError):
        crypto.encrypt_aes_gcm(b"plaintext", b"\x00" * key_length)


@pytest.mark.parametrize("key_length", [0, 1, 16, 24, 31, 33, 64])
def test_aes_gcm_invalid_key_length_raises_on_decrypt(crypto: LocalCrypto, key_length: int) -> None:
    valid_key = b"\x77" * 32
    nonce, ciphertext, tag = crypto.encrypt_aes_gcm(b"plaintext", valid_key)
    with pytest.raises(CryptoProviderError):
        crypto.decrypt_aes_gcm(nonce, ciphertext, tag, b"\x00" * key_length)


def test_aes_gcm_invalid_nonce_length_raises_on_decrypt(crypto: LocalCrypto) -> None:
    key = b"\x88" * 32
    _nonce, ciphertext, tag = crypto.encrypt_aes_gcm(b"plaintext", key)
    with pytest.raises(CryptoProviderError):
        crypto.decrypt_aes_gcm(b"short-nonce", ciphertext, tag, key)


def test_aes_gcm_invalid_tag_length_raises_on_decrypt(crypto: LocalCrypto) -> None:
    """A truncated/extended authentication tag must be rejected by explicit
    length check, not merely by a coincidental authentication failure."""
    key = b"\xaa" * 32
    nonce, ciphertext, _tag = crypto.encrypt_aes_gcm(b"plaintext", key)
    with pytest.raises(CryptoProviderError):
        crypto.decrypt_aes_gcm(nonce, ciphertext, b"\x00" * 8, key)


# -- Key material never leaks through exceptions --------------------------------


def test_aes_gcm_key_length_error_does_not_expose_key_bytes(crypto: LocalCrypto) -> None:
    secret_marker_key = b"\xde\xad\xbe\xef" * 3  # 12 bytes, wrong length
    with pytest.raises(CryptoProviderError) as exc_info:
        crypto.encrypt_aes_gcm(b"plaintext", secret_marker_key)
    assert secret_marker_key not in str(exc_info.value).encode(errors="ignore")
    assert secret_marker_key.hex() not in str(exc_info.value)


def test_ed25519_invalid_private_key_error_does_not_expose_key_bytes(crypto: LocalCrypto) -> None:
    secret_marker_key = b"not-a-real-private-key-material"
    with pytest.raises(CryptoProviderError) as exc_info:
        crypto.sign_ed25519(b"message", secret_marker_key)
    assert secret_marker_key.decode() not in str(exc_info.value)


def test_aes_gcm_tamper_error_does_not_expose_key_bytes(crypto: LocalCrypto) -> None:
    key = b"\x99" * 32
    nonce, ciphertext, tag = crypto.encrypt_aes_gcm(b"plaintext", key)
    tampered_tag = bytes([tag[0] ^ 0xFF]) + tag[1:]
    with pytest.raises(CryptoProviderError) as exc_info:
        crypto.decrypt_aes_gcm(nonce, ciphertext, tampered_tag, key)
    assert key.hex() not in str(exc_info.value)


# -- VerificationService: delegation, not duplication ---------------------------


class _SpyCryptoProvider:
    """Instrumented fake ICryptoProvider used to prove VerificationService
    delegates rather than duplicating cryptographic logic."""

    def __init__(self) -> None:
        self.hash_sha256_calls: list[bytes] = []
        self.verify_sha256_calls: list[tuple[bytes, str]] = []
        self.sign_ed25519_calls: list[tuple[bytes, bytes]] = []
        self.verify_ed25519_calls: list[tuple[bytes, bytes, bytes]] = []

    def hash_sha256(self, data: bytes) -> str:
        self.hash_sha256_calls.append(data)
        return "SENTINEL_HASH_VALUE"

    def verify_sha256(self, data: bytes, expected_hash: str) -> bool:
        self.verify_sha256_calls.append((data, expected_hash))
        return True

    def generate_ed25519_keypair(self) -> tuple[bytes, bytes]:
        raise NotImplementedError

    def sign_ed25519(self, data: bytes, private_key: bytes) -> bytes:
        self.sign_ed25519_calls.append((data, private_key))
        return b"SENTINEL_SIGNATURE_BYTES_" + b"\x00" * 40  # arbitrary bytes, unused length-sensitive here

    def verify_ed25519(self, data: bytes, signature: bytes, public_key: bytes) -> bool:
        self.verify_ed25519_calls.append((data, signature, public_key))
        return False  # deliberately distinct from a "real" outcome to prove delegation

    def encrypt_aes_gcm(
        self, plaintext: bytes, key: bytes, associated_data: bytes | None = None
    ) -> tuple[bytes, bytes, bytes]:
        raise NotImplementedError

    def decrypt_aes_gcm(
        self, nonce: bytes, ciphertext: bytes, tag: bytes, key: bytes, associated_data: bytes | None = None
    ) -> bytes:
        raise NotImplementedError


def test_verification_service_compute_checksum_delegates_to_provider() -> None:
    spy = _SpyCryptoProvider()
    service = VerificationService(spy)
    result = service.compute_checksum(b"data")
    assert result == "SENTINEL_HASH_VALUE"  # exact provider return value, unmodified
    assert spy.hash_sha256_calls == [b"data"]


def test_verification_service_verify_checksum_delegates_to_provider() -> None:
    spy = _SpyCryptoProvider()
    service = VerificationService(spy)
    result = service.verify_checksum(b"data", "some-hash")
    assert result is True  # exact provider return value
    assert spy.verify_sha256_calls == [(b"data", "some-hash")]


def test_verification_service_verify_signature_delegates_to_provider() -> None:
    spy = _SpyCryptoProvider()
    service = VerificationService(spy)
    signature = CryptographicSignature(algorithm="ed25519", signature=b"\x01" * 64, public_key=b"\x02" * 32)
    result = service.verify_signature(b"data", signature)
    assert result is False  # exact provider return value (spy always returns False)
    assert spy.verify_ed25519_calls == [(b"data", b"\x01" * 64, b"\x02" * 32)]


def test_verification_service_verify_signature_rejects_unsupported_algorithm_without_calling_provider() -> None:
    spy = _SpyCryptoProvider()
    service = VerificationService(spy)
    signature = CryptographicSignature(algorithm="hmac-sha256", signature=b"\x01" * 64, public_key=b"\x02" * 32)
    result = service.verify_signature(b"data", signature)
    assert result is False
    assert spy.verify_ed25519_calls == []  # never delegated for an unsupported/mismatched algorithm


def test_verification_service_sign_delegates_and_never_retains_private_key() -> None:
    real_crypto = LocalCrypto()
    private_key, public_key = real_crypto.generate_ed25519_keypair()
    service = VerificationService(real_crypto)
    signature = service.sign(b"data", private_key, public_key)
    assert signature.public_key == public_key
    assert "private_key" not in CryptographicSignature.model_fields
    assert not hasattr(signature, "private_key")


def test_verification_service_sign_and_verify_round_trip_real_crypto() -> None:
    real_crypto = LocalCrypto()
    private_key, public_key = real_crypto.generate_ed25519_keypair()
    service = VerificationService(real_crypto)
    signature = service.sign(b"payload", private_key, public_key)
    assert service.verify_signature(b"payload", signature) is True
    assert service.verify_signature(b"tampered payload", signature) is False


def test_verification_service_verify_signature_strict_raises_on_failure() -> None:
    real_crypto = LocalCrypto()
    _private_key, public_key = real_crypto.generate_ed25519_keypair()
    forged_signature = CryptographicSignature(algorithm="ed25519", signature=b"\x00" * 64, public_key=public_key)
    service = VerificationService(real_crypto)
    with pytest.raises(InvalidSignatureError):
        service.verify_signature_strict(b"payload", forged_signature)


def test_verification_service_verify_signature_strict_passes_silently_on_success() -> None:
    real_crypto = LocalCrypto()
    private_key, public_key = real_crypto.generate_ed25519_keypair()
    service = VerificationService(real_crypto)
    signature = service.sign(b"payload", private_key, public_key)
    service.verify_signature_strict(b"payload", signature)  # must not raise
