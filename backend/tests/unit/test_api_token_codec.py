"""Unit tests for `kortex.api.token_codec` — the opaque session-token
encoding Rust stores verbatim in the OS keychain (see that module's
docstring for why this must not rely on `TokenPayload.model_dump_json()`)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kortex.api.token_codec import decode_token, encode_token
from kortex.engines.security.models import TokenPayload


def _token(**overrides: object) -> TokenPayload:
    now = datetime.now(timezone.utc)
    defaults: dict = {
        "token_id": "tok-1",
        "principal_id": "alice",
        "principal_type": "USER",
        "tenant_id": "tenant-1",
        "issued_at_utc": now,
        "expires_at_utc": now + timedelta(minutes=15),
        "signature": bytes(range(256)) * 2,  # exercises every byte value, not valid UTF-8
    }
    defaults.update(overrides)
    return TokenPayload(**defaults)


class TestRoundTrip:
    def test_round_trips_all_fields_including_binary_signature(self) -> None:
        token = _token()
        decoded = decode_token(encode_token(token))
        assert decoded == token

    def test_round_trips_when_signature_is_none(self) -> None:
        token = _token(signature=None)
        decoded = decode_token(encode_token(token))
        assert decoded.signature is None

    def test_encoded_blob_is_ascii_and_url_safe(self) -> None:
        blob = encode_token(_token())
        blob.encode("ascii")  # must not raise
        assert "/" not in blob and "+" not in blob  # urlsafe alphabet only


class TestMalformedInput:
    @pytest.mark.parametrize(
        "garbage",
        ["not-base64-!!!", "", "aGVsbG8=", "e30="],  # "hello", "{}" base64-encoded — valid base64, invalid token
    )
    def test_malformed_or_incomplete_blob_raises_value_error(self, garbage: str) -> None:
        with pytest.raises(ValueError):
            decode_token(garbage)
