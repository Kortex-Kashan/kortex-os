"""
KORTEX Security Engine — Local Software Cryptographic Provider (Milestone M1).

Implements `ICryptoProvider` using the vetted `cryptography` library exclusively.
No cryptographic primitive in this module is implemented by hand — every
operation delegates to `cryptography`'s audited implementations.

Fixed algorithm suite (never caller-selectable, per Security Engine spec S11-S13):
    - SHA-256      : integrity checksums (Python stdlib `hashlib`)
    - Ed25519      : asymmetric digital signatures
    - AES-256-GCM  : authenticated symmetric encryption

Explicitly out of scope for this provider (see Security Engine M1 decision
report, Section 7.B / 10.J-3):
    - XChaCha20-Poly1305 — the `cryptography` package does not provide the
      extended-nonce XChaCha20 variant; adding it would require `pynacl`,
      which M1 is explicitly authorized not to add. This remains an open
      architectural decision for a later milestone.
    - Key persistence / key management — deferred to M2 (SecretStore).

This provider never persists, logs, or returns key material except as the
explicit return values of `generate_ed25519_keypair`, which the caller alone
is responsible for handling securely.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from kortex.engines.security.exceptions import CryptoProviderError

_AES_256_GCM_KEY_LENGTH_BYTES = 32
_AES_GCM_NONCE_LENGTH_BYTES = 12
_AES_GCM_TAG_LENGTH_BYTES = 16
_ED25519_SIGNATURE_LENGTH_BYTES = 64
_ED25519_PUBLIC_KEY_LENGTH_BYTES = 32


class LocalCrypto:
    """Local software cryptographic provider backed by the `cryptography` library.

    Implements `kortex.engines.security.interfaces.ICryptoProvider`.
    """

    # -- SHA-256 -----------------------------------------------------------

    def hash_sha256(self, data: bytes) -> str:
        """Compute the SHA-256 digest of `data`, returned as a lowercase hex string.

        Deterministic: identical input always produces identical output.
        """
        return hashlib.sha256(data).hexdigest()

    def verify_sha256(self, data: bytes, expected_hash: str) -> bool:
        """Verify `data` matches `expected_hash` using a constant-time comparison.

        `hmac.compare_digest` is used here purely as a timing-safe string
        comparison utility — this is NOT a substitute for Ed25519 signatures
        and provides no authenticity guarantee, only integrity-against-typos.
        """
        actual_hash = self.hash_sha256(data)
        return hmac.compare_digest(actual_hash, expected_hash.lower())

    # -- Ed25519 -------------------------------------------------------------

    def generate_ed25519_keypair(self) -> tuple[bytes, bytes]:
        """Generate a fresh Ed25519 keypair. Returns (private_key_bytes, public_key_bytes).

        Both keys are raw 32-byte encodings. The caller is solely responsible
        for secure handling of the private key — this provider never persists it.
        """
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        private_bytes = private_key.private_bytes(
            encoding=Encoding.Raw,
            format=PrivateFormat.Raw,
            encryption_algorithm=NoEncryption(),
        )
        public_bytes = public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
        return private_bytes, public_bytes

    def sign_ed25519(self, data: bytes, private_key: bytes) -> bytes:
        """Sign `data` using a raw 32-byte Ed25519 private key. Returns a 64-byte signature.

        Raises `CryptoProviderError` for malformed private key material — never
        silently signs with substitute/default key material.
        """
        try:
            key = Ed25519PrivateKey.from_private_bytes(private_key)
        except (ValueError, TypeError) as exc:
            raise CryptoProviderError("Invalid Ed25519 private key material: expected 32 raw bytes.") from exc
        return key.sign(data)

    def verify_ed25519(self, data: bytes, signature: bytes, public_key: bytes) -> bool:
        """Verify an Ed25519 `signature` over `data` against a raw 32-byte public key.

        Returns False for any malformed key/signature or verification failure.
        Never raises for an invalid signature — verification failure is a
        normal, expected outcome, not an exceptional one. Fails closed: any
        error path returns False, never True.
        """
        if len(signature) != _ED25519_SIGNATURE_LENGTH_BYTES:
            return False
        if len(public_key) != _ED25519_PUBLIC_KEY_LENGTH_BYTES:
            return False
        try:
            key = Ed25519PublicKey.from_public_bytes(public_key)
        except (ValueError, TypeError):
            return False
        try:
            key.verify(signature, data)
        except InvalidSignature:
            return False
        return True

    # -- AES-256-GCM ---------------------------------------------------------

    def encrypt_aes_gcm(
        self,
        plaintext: bytes,
        key: bytes,
        associated_data: bytes | None = None,
    ) -> tuple[bytes, bytes, bytes]:
        """Encrypt `plaintext` under AES-256-GCM with a fresh random 96-bit nonce.

        Returns (nonce, ciphertext, tag). `key` MUST be exactly 32 bytes (256 bits).

        A fresh cryptographically-secure nonce (`os.urandom`) is generated for
        EVERY call — there is no caller-supplied-nonce parameter, by design,
        removing any possibility of nonce reuse through this API.
        """
        if len(key) != _AES_256_GCM_KEY_LENGTH_BYTES:
            raise CryptoProviderError(
                f"AES-256-GCM requires a {_AES_256_GCM_KEY_LENGTH_BYTES}-byte key, got {len(key)} bytes."
            )
        nonce = os.urandom(_AES_GCM_NONCE_LENGTH_BYTES)
        aesgcm = AESGCM(key)
        sealed = aesgcm.encrypt(nonce, plaintext, associated_data)
        # `cryptography`'s AESGCM.encrypt() returns ciphertext with the 16-byte
        # authentication tag appended at the end.
        ciphertext, tag = sealed[:-_AES_GCM_TAG_LENGTH_BYTES], sealed[-_AES_GCM_TAG_LENGTH_BYTES:]
        return nonce, ciphertext, tag

    def decrypt_aes_gcm(
        self,
        nonce: bytes,
        ciphertext: bytes,
        tag: bytes,
        key: bytes,
        associated_data: bytes | None = None,
    ) -> bytes:
        """Decrypt and authenticate AES-256-GCM `ciphertext`+`tag` under `key`/`nonce`.

        Raises `CryptoProviderError` on any tampering (ciphertext, tag, or
        associated data) or invalid key/nonce length. Decryption failure NEVER
        returns partial or garbage plaintext — it always raises.
        """
        if len(key) != _AES_256_GCM_KEY_LENGTH_BYTES:
            raise CryptoProviderError(
                f"AES-256-GCM requires a {_AES_256_GCM_KEY_LENGTH_BYTES}-byte key, got {len(key)} bytes."
            )
        if len(nonce) != _AES_GCM_NONCE_LENGTH_BYTES:
            raise CryptoProviderError(
                f"AES-256-GCM requires a {_AES_GCM_NONCE_LENGTH_BYTES}-byte nonce, got {len(nonce)} bytes."
            )
        if len(tag) != _AES_GCM_TAG_LENGTH_BYTES:
            raise CryptoProviderError(
                f"AES-256-GCM requires a {_AES_GCM_TAG_LENGTH_BYTES}-byte authentication tag, got {len(tag)} bytes."
            )
        aesgcm = AESGCM(key)
        sealed = ciphertext + tag
        try:
            return aesgcm.decrypt(nonce, sealed, associated_data)
        except InvalidTag as exc:
            raise CryptoProviderError(
                "AES-256-GCM authentication failed: ciphertext, tag, or associated data has been tampered with."
            ) from exc
