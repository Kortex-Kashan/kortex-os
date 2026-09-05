"""KORTEX Update Engine cryptographic primitives and signature validation.

Phase 7 — Production Hardening — Update Engine.
Delegates exclusively to `LocalCrypto` for SHA-256 and Ed25519 operations.
Zero homegrown cryptography.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, cast

from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.update.exceptions import (
    UpdateChecksumMismatchError,
    UpdateKeyNotFoundError,
    UpdateManifestError,
    UpdateSignatureError,
)

# Standard compiled vendor root public keys (Ed25519 32-byte public keys)
# In production, official release packages are signed with these authoritative keys.
COMPILED_VENDOR_UPDATE_KEYS: dict[str, bytes] = {
    # Official KORTEX Vendor Release Root 2026 (Pre-seeded valid 32-byte key)
    "kortex-vendor-release-root-2026": bytes.fromhex(
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c"
    ),
    # Secondary rotation key
    "kortex-vendor-release-root-2027": bytes.fromhex(
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025"
    ),
}


def b64url_encode(data: bytes) -> str:
    """Encode bytes to URL-safe Base64 without padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(s: str) -> bytes:
    """Decode unpadded URL-safe Base64 or standard Base64 string to bytes."""
    s_clean = s.strip()
    # Check if it's hex
    if len(s_clean) == 128 and all(c in "0123456789abcdefABCDEF" for c in s_clean):
        return bytes.fromhex(s_clean)

    rem = len(s_clean) % 4
    if rem > 0:
        s_clean += "=" * (4 - rem)
    try:
        return base64.urlsafe_b64decode(s_clean.encode("ascii"))
    except (binascii.Error, UnicodeError) as exc:
        raise UpdateSignatureError(f"Invalid signature encoding: {exc}") from exc


def parse_json_safe(raw_json: str | bytes) -> dict[str, Any]:
    """Parse JSON string or bytes, strictly rejecting duplicate keys."""

    def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for k, v in pairs:
            if k in result:
                raise UpdateManifestError(f"Duplicate JSON key found: {k!r}")
            result[k] = v
        return result

    try:
        return cast(dict[str, Any], json.loads(raw_json, object_pairs_hook=_reject_duplicates))
    except json.JSONDecodeError as exc:
        raise UpdateManifestError(f"Malformed JSON payload: {exc}") from exc


def canonical_manifest_bytes(manifest_dict: dict[str, Any]) -> bytes:
    """Serialize a manifest dict into deterministic canonical bytes.

    Excludes the 'signature' field, sorts keys recursively, and formats without whitespace.
    """
    cleaned: dict[str, Any] = {k: v for k, v in manifest_dict.items() if k != "signature"}
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_bytes_sha256(data: bytes) -> str:
    """Compute hex-encoded SHA-256 checksum for byte data."""
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(file_path: Path | str, chunk_size: int = 65536) -> str:
    """Compute hex-encoded SHA-256 checksum for a file on disk."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found for checksum: {path}")

    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_file_sha256(file_path: Path | str, expected_sha256: str) -> bool:
    """Verify that a file's SHA-256 checksum matches the expected digest in constant time."""
    actual = compute_file_sha256(file_path)
    return hmac.compare_digest(actual.lower(), expected_sha256.lower().strip())


class UpdateCryptoManager:
    """Cryptographic subsystem for Update Engine."""

    def __init__(
        self,
        vendor_keys: dict[str, bytes] | None = None,
        trusted_public_keys: dict[str, str | bytes] | None = None,
    ) -> None:
        self._local_crypto = LocalCrypto()
        self._vendor_keys: dict[str, bytes] = dict(vendor_keys or COMPILED_VENDOR_UPDATE_KEYS)
        if trusted_public_keys:
            for k, v in trusted_public_keys.items():
                if isinstance(v, str):
                    try:
                        self._vendor_keys[k] = base64.b64decode(v)
                    except Exception:
                        self._vendor_keys[k] = v.encode()
                else:
                    self._vendor_keys[k] = v

    def compute_sha256(self, data: bytes) -> str:
        """Compute SHA-256 digest for byte data."""
        return compute_bytes_sha256(data)

    def canonical_manifest_bytes(self, manifest_dict: dict[str, Any]) -> bytes:
        """Serialize a manifest dict into deterministic canonical bytes."""
        return canonical_manifest_bytes(manifest_dict)

    def register_vendor_key(self, key_id: str, public_key_bytes: bytes) -> None:
        """Register or override a trusted vendor public key (32 raw bytes)."""
        if len(public_key_bytes) != 32:
            raise ValueError(f"Vendor public key must be 32 bytes, got {len(public_key_bytes)}")
        self._vendor_keys[key_id] = public_key_bytes

    def get_vendor_key(self, key_id: str) -> bytes:
        """Resolve trusted vendor public key by key ID."""
        key = self._vendor_keys.get(key_id)
        if key is None:
            raise UpdateKeyNotFoundError(f"Unknown or untrusted vendor signing key ID: {key_id!r}")
        return key

    def sign_manifest(self, manifest_dict: dict[str, Any], private_key: bytes) -> str:
        """Sign a manifest dictionary with an Ed25519 private key.

        Returns base64url-encoded signature.
        """
        canonical_bytes = canonical_manifest_bytes(manifest_dict)
        signature = self._local_crypto.sign_ed25519(canonical_bytes, private_key)
        return b64url_encode(signature)

    def verify_manifest(self, manifest_dict: dict[str, Any]) -> bool:
        """Verify the cryptographic authenticity and integrity of an update manifest.

        Fails closed on any invalid signature, missing key, or malformed data.
        """
        key_id = manifest_dict.get("key_id")
        if not key_id or not isinstance(key_id, str):
            raise UpdateKeyNotFoundError("Manifest missing required 'key_id' field")

        signature_str = manifest_dict.get("signature")
        if not signature_str or not isinstance(signature_str, str):
            raise UpdateSignatureError("Manifest missing required 'signature' field")

        public_key = self.get_vendor_key(key_id)
        sig_bytes = b64url_decode(signature_str)
        canonical_bytes = canonical_manifest_bytes(manifest_dict)

        valid = self._local_crypto.verify_ed25519(
            data=canonical_bytes,
            signature=sig_bytes,
            public_key=public_key,
        )
        if not valid:
            raise UpdateSignatureError(
                f"Cryptographic signature verification failed for manifest '{manifest_dict.get('manifest_id')}'"
            )
        return True

    def verify_artifact(self, artifact_path: Path | str, expected_sha256: str) -> None:
        """Verify artifact SHA-256 digest against expected manifest hash."""
        if not verify_file_sha256(artifact_path, expected_sha256):
            actual = compute_file_sha256(artifact_path)
            raise UpdateChecksumMismatchError(f"Artifact digest mismatch: expected '{expected_sha256}', got '{actual}'")
