"""
KORTEX Security Engine — Verification Service Facade (Milestone M1).

Implements `IVerificationService` by delegating every cryptographic operation
to a configured `ICryptoProvider`. This module contains no cryptographic
primitives of its own.

    VerificationService
            |
            v
    ICryptoProvider
            |
            v
    LocalCrypto  (providers/local_crypto.py)

M1 scope: SHA-256 checksum verification and Ed25519 signature verification
only. Encryption/decryption primitives exist on `ICryptoProvider` for a later
milestone (M2 SecretStore) and are intentionally not exposed through this
facade — M1 must not turn `VerificationService` into a secret-management
system.
"""

from __future__ import annotations

from kortex.engines.security.exceptions import InvalidSignatureError
from kortex.engines.security.interfaces import ICryptoProvider
from kortex.engines.security.models import CryptographicSignature


class VerificationService:
    """M1 verification facade — SHA-256 checksums and Ed25519 signatures only.

    Implements `kortex.engines.security.interfaces.IVerificationService`.
    """

    def __init__(self, crypto_provider: ICryptoProvider) -> None:
        """Initialize with an injected cryptographic provider.

        This facade never constructs its own provider — the caller supplies
        one (e.g. `LocalCrypto()`), keeping algorithm selection out of this
        module entirely.
        """
        self._crypto_provider = crypto_provider

    # -- Checksums ------------------------------------------------------------

    def compute_checksum(self, data: bytes) -> str:
        """Compute a SHA-256 checksum of `data` via the configured crypto provider."""
        return self._crypto_provider.hash_sha256(data)

    def verify_checksum(self, data: bytes, expected_checksum: str) -> bool:
        """Verify `data` matches `expected_checksum` via the configured crypto provider."""
        return self._crypto_provider.verify_sha256(data, expected_checksum)

    # -- Signatures -----------------------------------------------------------

    def sign(self, data: bytes, private_key: bytes, public_key: bytes) -> CryptographicSignature:
        """Sign `data` with an Ed25519 keypair and return a `CryptographicSignature`.

        The private key is used only for this call, is never retained by this
        facade, never logged, and never included in the returned model.
        """
        signature_bytes = self._crypto_provider.sign_ed25519(data, private_key)
        return CryptographicSignature(
            algorithm="ed25519",
            signature=signature_bytes,
            public_key=public_key,
        )

    def verify_signature(self, data: bytes, signature: CryptographicSignature) -> bool:
        """Verify `data` against a `CryptographicSignature`.

        Returns False for any algorithm mismatch, malformed key/signature, or
        failed verification. Never raises for a normal verification failure —
        callers that require hard-failure semantics should use
        `verify_signature_strict` instead.
        """
        if signature.algorithm != "ed25519":
            return False
        return self._crypto_provider.verify_ed25519(data, signature.signature, signature.public_key)

    def verify_signature_strict(self, data: bytes, signature: CryptographicSignature) -> None:
        """Verify `data` against a `CryptographicSignature`, raising on failure.

        Raises `InvalidSignatureError` if verification fails for any reason
        (algorithm mismatch, malformed key/signature, or a genuine signature
        mismatch) — for call sites that must not silently ignore a boolean
        failure result.
        """
        if not self.verify_signature(data, signature):
            raise InvalidSignatureError("Signature verification failed: payload, key, or algorithm mismatch.")
