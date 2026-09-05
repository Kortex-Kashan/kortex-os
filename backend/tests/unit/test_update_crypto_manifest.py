"""Unit tests for Update Engine cryptographic operations and manifest parsing.

Phase 7 — Production Hardening — Update Engine.
Verifies Ed25519 digital signatures, canonical JSON formatting, SHA-256 integrity,
strict duplicate-key rejection, and manifest expiration policies.
"""

from __future__ import annotations

import base64
import datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kortex.engines.update.crypto import (
    UpdateCryptoManager,
    canonical_manifest_bytes,
    compute_bytes_sha256,
    compute_file_sha256,
    parse_json_safe,
    verify_file_sha256,
)
from kortex.engines.update.exceptions import (
    UpdateChecksumMismatchError,
    UpdateKeyNotFoundError,
    UpdateManifestError,
    UpdateSignatureError,
)
from kortex.engines.update.manifest import UpdateManifestParser
from kortex.engines.update.models import UpdateManifest


@pytest.fixture
def crypto_keypair() -> tuple[Ed25519PrivateKey, str, bytes]:
    """Generate a test Ed25519 keypair and return (private_key, key_id, public_key_bytes)."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    pub_bytes = public_key.public_bytes_raw()
    key_id = "test-key-2026"
    return private_key, key_id, pub_bytes


def test_sha256_computation_and_verification(tmp_path: Path) -> None:
    """Verify SHA-256 calculation and verification over bytes and files."""
    data = b"Hello KORTEX Update Engine!"
    expected_hash = "12e87d6e737f7b892c765c5acf10b960d33625cb98e6b5ca97fc041f7593813b"

    computed = compute_bytes_sha256(data)
    assert computed == expected_hash

    # Test file calculation
    test_file = tmp_path / "test.bin"
    test_file.write_bytes(data)
    file_computed = compute_file_sha256(test_file)
    assert file_computed == expected_hash

    # Verify matching hash
    assert verify_file_sha256(test_file, expected_hash) is True
    assert verify_file_sha256(test_file, "wrong_hash") is False

    # Test verify_artifact
    crypto = UpdateCryptoManager()
    crypto.verify_artifact(test_file, expected_hash)
    with pytest.raises(UpdateChecksumMismatchError):
        crypto.verify_artifact(test_file, "wrong_hash")


def test_canonical_manifest_serialization() -> None:
    """Verify deterministic canonical JSON representation (sorted keys, compact)."""
    sample_dict = {
        "zeta": 1,
        "alpha": "two",
        "beta": [3, 4],
        "signature": "sig1",
    }

    canonical = canonical_manifest_bytes(sample_dict)
    assert b"signature" not in canonical  # signature field must be excluded
    decoded = canonical.decode("utf-8")
    assert decoded.index('"alpha"') < decoded.index('"beta"') < decoded.index('"zeta"')


def test_ed25519_sign_and_verify(crypto_keypair: tuple[Ed25519PrivateKey, str, bytes]) -> None:
    """Verify Ed25519 manifest signature creation and verification."""
    private_key, key_id, pub_bytes = crypto_keypair
    crypto = UpdateCryptoManager(vendor_keys={key_id: pub_bytes})

    manifest_dict = {
        "manifest_version": "kortex-update-manifest-v1.0",
        "manifest_id": "mf-valid-01",
        "created_at": "2026-09-05T00:00:00Z",
        "expires_at": "2026-09-12T00:00:00Z",
        "key_id": key_id,
        "version": {
            "target_version": "0.2.0",
            "min_supported_version": "0.1.0",
            "release_channel": "stable",
        },
        "compatibility": {
            "platforms": ["win32", "linux"],
            "architectures": ["x86_64"],
            "python_version_min": "3.11",
        },
        "package": {
            "filename": "upd.zip",
            "sha256": "abc123hash",
            "size_bytes": 100,
            "uncompressed_bytes": 200,
            "file_count": 2,
        },
        "database": {"requires_migration": False},
        "metadata": {},
    }

    # Sign using crypto manager
    raw_priv = private_key.private_bytes_raw()
    sig_str = crypto.sign_manifest(manifest_dict, raw_priv)
    manifest_dict["signature"] = sig_str

    # Verify signature
    assert crypto.verify_manifest(manifest_dict) is True


def test_ed25519_invalid_signature(crypto_keypair: tuple[Ed25519PrivateKey, str, bytes]) -> None:
    """Verify rejection of invalid/corrupted Ed25519 signature."""
    _, key_id, pub_bytes = crypto_keypair
    crypto = UpdateCryptoManager(vendor_keys={key_id: pub_bytes})

    # base64url encode invalid signature of 64 bytes
    invalid_sig = base64.urlsafe_b64encode(b"x" * 64).decode("ascii").rstrip("=")

    manifest_dict = {
        "manifest_version": "kortex-update-manifest-v1.0",
        "manifest_id": "mf-tampered-01",
        "key_id": key_id,
        "signature": invalid_sig,
        "version": {"target_version": "0.2.0", "min_supported_version": "0.1.0"},
    }

    with pytest.raises(UpdateSignatureError):
        crypto.verify_manifest(manifest_dict)


def test_ed25519_unknown_key_id(crypto_keypair: tuple[Ed25519PrivateKey, str, bytes]) -> None:
    """Verify rejection when key_id is not present in trusted store."""
    crypto = UpdateCryptoManager(vendor_keys={"other-key": b"\x00" * 32})

    manifest_dict = {
        "manifest_version": "kortex-update-manifest-v1.0",
        "manifest_id": "mf-unknown-key",
        "key_id": "untrusted-key",
        "signature": base64.urlsafe_b64encode(b"x" * 64).decode("ascii").rstrip("="),
    }

    with pytest.raises(UpdateKeyNotFoundError):
        crypto.verify_manifest(manifest_dict)


def test_strict_duplicate_key_json_rejection() -> None:
    """Verify parser rejects duplicate JSON keys (preventing parser differentials)."""
    json_with_duplicate = '{"version": "0.1.0", "version": "0.2.0"}'

    with pytest.raises(UpdateManifestError) as exc_info:
        parse_json_safe(json_with_duplicate)
    assert "Duplicate JSON key" in str(exc_info.value)


def test_manifest_parser_valid() -> None:
    """Verify UpdateManifestParser parses and validates valid manifest."""
    future_date = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=7)).isoformat()
    manifest_data = {
        "manifest_version": "kortex-update-manifest-v1.0",
        "manifest_id": "mf-test-valid",
        "created_at": "2026-09-05T00:00:00Z",
        "expires_at": future_date,
        "key_id": "k1",
        "signature": "sig1",
        "version": {
            "target_version": "0.2.0",
            "min_supported_version": "0.1.0",
            "release_channel": "stable",
        },
        "compatibility": {
            "platforms": ["win32"],
            "architectures": ["x86_64"],
            "python_version_min": "3.11",
        },
        "package": {
            "filename": "upd.zip",
            "sha256": "abc123hash",
            "size_bytes": 100,
            "uncompressed_bytes": 200,
            "file_count": 1,
        },
        "database": {"requires_migration": False},
        "metadata": {},
    }

    manifest = UpdateManifestParser.parse_dict(manifest_data)
    assert isinstance(manifest, UpdateManifest)
    assert manifest.manifest_id == "mf-test-valid"


def test_manifest_parser_expired() -> None:
    """Verify UpdateManifestParser rejects expired manifests."""
    past_date = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)).isoformat()
    manifest_data = {
        "manifest_version": "kortex-update-manifest-v1.0",
        "manifest_id": "mf-test-expired",
        "created_at": "2026-08-01T00:00:00Z",
        "expires_at": past_date,
        "key_id": "k1",
        "signature": "sig1",
        "version": {
            "target_version": "0.2.0",
            "min_supported_version": "0.1.0",
            "release_channel": "stable",
        },
        "compatibility": {
            "platforms": ["win32"],
            "architectures": ["x86_64"],
        },
        "package": {
            "filename": "upd.zip",
            "sha256": "abc123hash",
            "size_bytes": 100,
            "uncompressed_bytes": 200,
            "file_count": 1,
        },
        "database": {"requires_migration": False},
        "metadata": {},
    }

    with pytest.raises(UpdateManifestError) as exc_info:
        UpdateManifestParser.parse_dict(manifest_data)
    assert "expired" in str(exc_info.value)
