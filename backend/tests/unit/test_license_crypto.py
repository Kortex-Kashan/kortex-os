"""
Unit tests for KORTEX License Engine Cryptographic Protocol and Canonicalization (M5.7).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from kortex.engines.license.config import (
    _OFFICIAL_ROOT_KID,
)
from kortex.engines.license.crypto import (
    LicenseCryptoEngine,
    b64url_decode,
    b64url_encode,
    canonicalize_json,
    parse_json_safe,
)
from kortex.engines.license.exceptions import (
    InvalidKeyFormatError,
    InvalidTokenSignatureError,
    MalformedTokenError,
    UnknownKeyIdentifierError,
    UnsupportedAlgorithmError,
)
from kortex.engines.license.models import (
    LicenseScopeEnum,
    LicenseTier,
    LicenseTokenClaims,
)

# Test key material derived from deterministic seed
_TEST_SEED = bytes.fromhex("3f34ef585ba20e9dc048c2d9f6ce9ab55515dacc578f721d78635ad38af42782")
_TEST_PRIV = Ed25519PrivateKey.from_private_bytes(_TEST_SEED)
_TEST_PRIV_BYTES = _TEST_PRIV.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
_TEST_PUB_BYTES = _TEST_PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _sample_claims() -> LicenseTokenClaims:
    return LicenseTokenClaims(
        schema_version=1,
        license_id="a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        issuer="kortex.ai",
        subject_tenant_id="11111111-2222-4333-8444-555555555555",
        scope=LicenseScopeEnum.TENANT,
        tier=LicenseTier.ENTERPRISE,
        issued_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        not_before=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC),
        grace_period_days=14,
        features=["document_ocr", "process_mining"],
        quotas={"max_connectors": 10, "max_users": 50},
    )


def test_b64url_roundtrip() -> None:
    raw = b"Hello, KORTEX OS License Cryptography!"
    encoded = b64url_encode(raw)
    assert "=" not in encoded
    decoded = b64url_decode(encoded)
    assert decoded == raw


def test_canonicalize_json_key_ordering() -> None:
    # Different key insertion orders must yield identical canonical bytes
    d1 = {"z": 1, "a": 2, "m": {"b": 3, "a": 4}}
    d2 = {"a": 2, "m": {"a": 4, "b": 3}, "z": 1}
    assert canonicalize_json(d1) == canonicalize_json(d2)
    assert canonicalize_json(d1) == b'{"a":2,"m":{"a":4,"b":3},"z":1}'


def test_canonicalize_json_whitespace_invariance() -> None:
    d = {"key": "value", "list": [1, 2, 3]}
    b = canonicalize_json(d)
    assert b" " not in b
    assert b == b'{"key":"value","list":[1,2,3]}'


def test_canonicalize_json_rejects_floats() -> None:
    with pytest.raises(MalformedTokenError, match="Floating-point numbers are forbidden"):
        canonicalize_json({"price": 99.99})


def test_canonicalize_json_datetime_normalization() -> None:
    dt_utc = datetime(2026, 9, 3, 12, 30, 45, tzinfo=UTC)
    res = canonicalize_json({"ts": dt_utc})
    assert res == b'{"ts":"2026-09-03T12:30:45Z"}'


def test_parse_json_safe_rejects_duplicate_keys() -> None:
    raw = '{"license_id": "1", "tier": "COMMUNITY", "tier": "ENTERPRISE"}'
    with pytest.raises(MalformedTokenError, match="Duplicate JSON key"):
        parse_json_safe(raw)


def test_parse_json_safe_valid() -> None:
    raw = '{"license_id": "1", "tier": "COMMUNITY"}'
    parsed = parse_json_safe(raw)
    assert parsed == {"license_id": "1", "tier": "COMMUNITY"}


def test_token_encode_decode_roundtrip() -> None:
    engine = LicenseCryptoEngine(trusted_root_keys={_OFFICIAL_ROOT_KID: _TEST_PUB_BYTES})
    claims = _sample_claims()
    token = engine.encode_token(claims, _TEST_PRIV_BYTES, kid=_OFFICIAL_ROOT_KID)

    assert token.count(".") == 2
    header, parsed_claims, kid, sig_hex = engine.decode_and_verify_token(token)

    assert header["alg"] == "Ed25519"
    assert header["kid"] == _OFFICIAL_ROOT_KID
    assert parsed_claims.license_id == claims.license_id
    assert parsed_claims.tier == claims.tier
    assert parsed_claims.features == claims.features
    assert parsed_claims.quotas == claims.quotas
    assert kid == _OFFICIAL_ROOT_KID
    assert len(sig_hex) == 128


def test_token_decode_tampered_signature() -> None:
    engine = LicenseCryptoEngine(trusted_root_keys={_OFFICIAL_ROOT_KID: _TEST_PUB_BYTES})
    claims = _sample_claims()
    token = engine.encode_token(claims, _TEST_PRIV_BYTES, kid=_OFFICIAL_ROOT_KID)

    parts = token.split(".")
    # Tamper with signature
    sig_raw = bytearray(b64url_decode(parts[2]))
    sig_raw[0] ^= 0xFF
    bad_token = f"{parts[0]}.{parts[1]}.{b64url_encode(bytes(sig_raw))}"

    with pytest.raises(InvalidTokenSignatureError, match="signature verification failed"):
        engine.decode_and_verify_token(bad_token)


def test_token_decode_tampered_payload() -> None:
    engine = LicenseCryptoEngine(trusted_root_keys={_OFFICIAL_ROOT_KID: _TEST_PUB_BYTES})
    claims = _sample_claims()
    token = engine.encode_token(claims, _TEST_PRIV_BYTES, kid=_OFFICIAL_ROOT_KID)

    parts = token.split(".")
    payload_dict = json.loads(b64url_decode(parts[1]).decode("utf-8"))
    payload_dict["tier"] = "PROFESSIONAL"  # Alter tier without updating signature
    bad_payload_b64 = b64url_encode(json.dumps(payload_dict).encode("utf-8"))
    bad_token = f"{parts[0]}.{bad_payload_b64}.{parts[2]}"

    with pytest.raises(InvalidTokenSignatureError):
        engine.decode_and_verify_token(bad_token)


def test_token_decode_unknown_kid() -> None:
    engine = LicenseCryptoEngine(trusted_root_keys={_OFFICIAL_ROOT_KID: _TEST_PUB_BYTES})
    claims = _sample_claims()
    token = engine.encode_token(claims, _TEST_PRIV_BYTES, kid="unknown-root-key")

    with pytest.raises(UnknownKeyIdentifierError, match="unknown-root-key"):
        engine.decode_and_verify_token(token)


def test_token_decode_unsupported_algorithm() -> None:
    engine = LicenseCryptoEngine(trusted_root_keys={_OFFICIAL_ROOT_KID: _TEST_PUB_BYTES})
    claims = _sample_claims()
    token = engine.encode_token(claims, _TEST_PRIV_BYTES, kid=_OFFICIAL_ROOT_KID)

    parts = token.split(".")
    header_dict = json.loads(b64url_decode(parts[0]).decode("utf-8"))
    header_dict["alg"] = "RS256"
    bad_header_b64 = b64url_encode(json.dumps(header_dict).encode("utf-8"))
    bad_token = f"{bad_header_b64}.{parts[1]}.{parts[2]}"

    with pytest.raises(UnsupportedAlgorithmError, match="RS256"):
        engine.decode_and_verify_token(bad_token)


def test_token_decode_malformed_parts_count() -> None:
    engine = LicenseCryptoEngine(trusted_root_keys={_OFFICIAL_ROOT_KID: _TEST_PUB_BYTES})
    with pytest.raises(MalformedTokenError, match="exactly 3 dot-separated parts"):
        engine.decode_and_verify_token("part1.part2")


def test_crypto_engine_invalid_root_key_length() -> None:
    with pytest.raises(InvalidKeyFormatError, match="must be exactly 32 bytes"):
        LicenseCryptoEngine(trusted_root_keys={"bad-key": b"too_short"})
