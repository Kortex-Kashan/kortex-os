"""Opaque refresh-token encoding (AI Studio Functional Stabilization, Phase F
security correction).

Deliberately lives in `kortex.engines.security`, not `kortex.api` (unlike
`kortex.api.token_codec`, which encodes the ordinary access `TokenPayload`):
the `kortex.security.auth.refresh` capability handler (`engine.py`) must be
able to decode a caller-supplied refresh-token string itself, since it is
registered `requires_authentication=False` and therefore never receives a
pre-decoded token from the transport layer the way an ordinary authenticated
capability does. Placing this codec inside the engine keeps that decode
step out of `kortex.api` without requiring `kortex.engines.security` to
import anything from the transport layer — the API layer already imports
freely from the Security Engine (`SecurityEngine`, `SecurityPrincipal`,
etc.), so `kortex.api.main` importing `encode_refresh_token` from here (to
serialize a freshly-issued `RefreshTokenPayload` onto the HTTP response) is
the architecturally correct dependency direction; the reverse would not be.

`RefreshTokenPayload` (`kortex.engines.security.models`) is a real,
Ed25519-signed credential — `AuthenticationManager.verify_refresh_token`
re-verifies that signature on every use regardless of how the bytes
arrived here, so this codec does not need to provide its own tamper
protection, exactly mirroring `kortex.api.token_codec`'s own reasoning.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from kortex.engines.security.models import RefreshTokenPayload


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def encode_refresh_token(token: RefreshTokenPayload) -> str:
    """Serialize a verified `RefreshTokenPayload` into an opaque, URL-safe string.

    Mirrors `kortex.api.token_codec.encode_token` exactly, including its
    reason for avoiding `model_dump_json()` (Pydantic v2's default JSON mode
    fails on the raw-binary `signature` field).
    """
    data = token.model_dump(mode="python")
    payload_json = json.dumps(data, default=_json_default)
    return base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("ascii")


def decode_refresh_token(blob: str) -> RefreshTokenPayload:
    """Reverse of `encode_refresh_token`.

    Raises `ValueError` on malformed input; callers must treat that
    identically to an invalid refresh token (never a distinct error
    category — a corrupt blob and a forged one are indistinguishable to the
    caller), exactly mirroring `kortex.api.token_codec.decode_token`.
    """
    try:
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
        data = json.loads(raw)
        if data.get("signature") is not None:
            data["signature"] = base64.b64decode(data["signature"])
    except Exception as exc:  # base64/json/ascii errors — not enumerable in advance
        raise ValueError("Malformed refresh token") from exc
    return RefreshTokenPayload.model_validate(data)
