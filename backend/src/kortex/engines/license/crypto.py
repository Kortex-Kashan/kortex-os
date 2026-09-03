"""
KORTEX License Engine Cryptographic Protocol & Canonicalization (Milestone M5.7).

Implements KORTEX constrained canonicalization profile, RFC 4648 Base64URL
packaging, and Ed25519 signature verification via LocalCryptoProvider.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kortex.engines.license.config import (
    _OFFICIAL_ROOT_KID,
    COMPILED_VENDOR_ROOT_KEYS,
    ED25519_PUBLIC_KEY_LENGTH_BYTES,
    ED25519_SIGNATURE_LENGTH_BYTES,
    SUPPORTED_ALGORITHM,
    SUPPORTED_SCHEMA_VERSION,
    SUPPORTED_TOKEN_TYPE,
)
from kortex.engines.license.exceptions import (
    InvalidKeyFormatError,
    InvalidTokenSignatureError,
    MalformedTokenError,
    UnknownKeyIdentifierError,
    UnsupportedAlgorithmError,
)
from kortex.engines.license.models import LicenseTokenClaims
from kortex.engines.security.providers.local_crypto import LocalCrypto


def b64url_encode(data: bytes) -> str:
    """Encode bytes to URL-safe Base64 without padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(s: str) -> bytes:
    """Decode unpadded URL-safe Base64 string to bytes."""
    rem = len(s) % 4
    if rem > 0:
        s += "=" * (4 - rem)
    try:
        return base64.urlsafe_b64decode(s.encode("ascii"))
    except (binascii.Error, UnicodeError) as exc:
        raise MalformedTokenError(f"Invalid base64url data: {exc}") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Object pairs hook for json.loads that strictly rejects duplicate keys."""
    d: dict[str, Any] = {}
    for k, v in pairs:
        if k in d:
            raise MalformedTokenError(f"Duplicate JSON key found in payload: {k!r}")
        d[k] = v
    return d


def parse_json_safe(text: str) -> dict[str, Any]:
    """Parse JSON string, failing closed on syntax errors and duplicate keys."""
    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise MalformedTokenError(f"Malformed JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise MalformedTokenError(f"Expected JSON object, got {type(parsed).__name__}")
    return parsed


def _normalize_canonical_value(val: Any) -> Any:
    """Recursively enforce KORTEX constrained canonicalization restrictions."""
    if isinstance(val, bool) or val is None:
        return val
    if isinstance(val, (float,)):
        raise MalformedTokenError("Floating-point numbers are forbidden in license payloads")
    if isinstance(val, int):
        return val
    if isinstance(val, datetime):
        val = val.replace(tzinfo=UTC) if val.tzinfo is None else val.astimezone(UTC)
        return val.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(val, str):
        return val
    if isinstance(val, (list, tuple)):
        return [_normalize_canonical_value(item) for item in val]
    if isinstance(val, dict):
        return {str(k): _normalize_canonical_value(v) for k, v in val.items()}
    raise MalformedTokenError(f"Unsupported data type for canonical serialization: {type(val).__name__}")


def canonicalize_json(data: dict[str, Any]) -> bytes:
    """Produce the deterministic UTF-8 bytes for a JSON object.

    Follows KORTEX constrained canonicalization profile:
    - Unicode code point key ordering
    - Compact separators (',', ':')
    - UTF-8 byte encoding
    - No insignificant whitespace
    - Integers only; floats/NaN/Inf forbidden
    - Datetimes serialized strictly as UTC 'YYYY-MM-DDTHH:MM:SSZ'
    """
    normalized = _normalize_canonical_value(data)
    json_str = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return json_str.encode("utf-8")


class LicenseCryptoEngine:
    """Cryptographic verification engine backed by LocalCrypto."""

    def __init__(
        self,
        trusted_root_keys: dict[str, bytes] | None = None,
        crypto_provider: LocalCrypto | None = None,
    ) -> None:
        if trusted_root_keys is not None:
            self._trusted_root_keys = dict(trusted_root_keys)
        else:
            self._trusted_root_keys = dict(COMPILED_VENDOR_ROOT_KEYS)
        self._crypto_provider = crypto_provider or LocalCrypto()

        for kid, key_bytes in self._trusted_root_keys.items():
            if not isinstance(key_bytes, bytes) or len(key_bytes) != ED25519_PUBLIC_KEY_LENGTH_BYTES:
                raise InvalidKeyFormatError(
                    f"Trusted root key for {kid!r} must be exactly {ED25519_PUBLIC_KEY_LENGTH_BYTES} bytes"
                )

    @property
    def trusted_root_keys(self) -> dict[str, bytes]:
        return dict(self._trusted_root_keys)

    def encode_token(
        self,
        claims: LicenseTokenClaims,
        signing_private_key: bytes,
        kid: str = _OFFICIAL_ROOT_KID,
    ) -> str:
        """Encode and sign a LicenseTokenClaims instance into a compact dot-separated token string."""
        if not isinstance(signing_private_key, bytes) or len(signing_private_key) != 32:
            raise InvalidKeyFormatError("Signing private key must be 32 raw bytes")

        header = {
            "alg": SUPPORTED_ALGORITHM,
            "kid": kid,
            "typ": SUPPORTED_TOKEN_TYPE,
            "v": SUPPORTED_SCHEMA_VERSION,
        }
        header_bytes = canonicalize_json(header)
        header_b64 = b64url_encode(header_bytes)

        payload_dict = claims.model_dump(mode="json")
        payload_bytes = canonicalize_json(payload_dict)
        payload_b64 = b64url_encode(payload_bytes)

        signed_bytes = f"{header_b64}.{payload_b64}".encode("ascii")

        # Generate Ed25519 signature
        priv_key = Ed25519PrivateKey.from_private_bytes(signing_private_key)
        signature = priv_key.sign(signed_bytes)
        signature_b64 = b64url_encode(signature)

        return f"{header_b64}.{payload_b64}.{signature_b64}"

    def decode_and_verify_token(
        self,
        token: str,
    ) -> tuple[dict[str, Any], LicenseTokenClaims, str, str]:
        """Decode, verify signature, and parse claims from a compact token string.

        Returns:
            tuple of (header_dict, claims, kid, signature_hex)
        Raises:
            MalformedTokenError, UnsupportedAlgorithmError, UnknownKeyIdentifierError,
            InvalidTokenSignatureError, InvalidKeyFormatError.
        """
        if not isinstance(token, str) or not token.strip():
            raise MalformedTokenError("Token must be a non-empty string")

        parts = token.strip().split(".")
        if len(parts) != 3:
            raise MalformedTokenError(f"Token must have exactly 3 dot-separated parts, got {len(parts)}")

        header_b64, payload_b64, signature_b64 = parts

        header_bytes = b64url_decode(header_b64)
        header = parse_json_safe(header_bytes.decode("utf-8", errors="replace"))

        # Validate header envelope
        alg = header.get("alg")
        if alg != SUPPORTED_ALGORITHM:
            raise UnsupportedAlgorithmError(f"Unsupported algorithm {alg!r}, expected {SUPPORTED_ALGORITHM!r}")

        typ = header.get("typ")
        if typ != SUPPORTED_TOKEN_TYPE:
            raise MalformedTokenError(f"Invalid token type {typ!r}, expected {SUPPORTED_TOKEN_TYPE!r}")

        v = header.get("v")
        if v != SUPPORTED_SCHEMA_VERSION:
            raise MalformedTokenError(f"Unsupported token version {v!r}, expected {SUPPORTED_SCHEMA_VERSION}")

        kid = header.get("kid")
        if not kid or not isinstance(kid, str):
            raise MalformedTokenError("Token header must contain a non-empty 'kid' string")

        if kid not in self._trusted_root_keys:
            raise UnknownKeyIdentifierError(f"Key identifier {kid!r} not found in trusted root keys")

        root_public_key = self._trusted_root_keys[kid]

        # Verify signature
        sig_bytes = b64url_decode(signature_b64)
        if len(sig_bytes) != ED25519_SIGNATURE_LENGTH_BYTES:
            raise InvalidTokenSignatureError(
                f"Signature must be exactly {ED25519_SIGNATURE_LENGTH_BYTES} bytes, got {len(sig_bytes)}"
            )

        signed_bytes = f"{header_b64}.{payload_b64}".encode("ascii")
        is_valid = self._crypto_provider.verify_ed25519(
            data=signed_bytes,
            signature=sig_bytes,
            public_key=root_public_key,
        )
        if not is_valid:
            raise InvalidTokenSignatureError("Ed25519 signature verification failed")

        # Parse and validate claims
        payload_bytes = b64url_decode(payload_b64)
        payload_dict = parse_json_safe(payload_bytes.decode("utf-8", errors="replace"))

        claims = LicenseTokenClaims.model_validate(payload_dict)

        return header, claims, kid, sig_bytes.hex()
