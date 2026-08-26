"""Opaque session-token encoding for the M3 transport boundary.

`TokenPayload` (`kortex.engines.security.models`) is a real, already
Ed25519-signed credential — `AuthenticationManager.verify_token` re-verifies
that signature on every call regardless of how the bytes arrived here, so
this codec does not need to provide its own tamper protection. Its only job
is to give Rust something it can store as an opaque string in the OS
keychain and replay verbatim, without Rust ever needing to understand
`TokenPayload`'s internal shape (the Tauri/Rust layer must never evaluate
business rules — see `phase3_desktop_architecture.md` §3 principle 2).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from kortex.engines.security.models import TokenPayload


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def encode_token(token: TokenPayload) -> str:
    """Serialize a verified `TokenPayload` into an opaque, URL-safe string.

    Deliberately does not use `model_dump_json()`: Pydantic v2's default
    JSON mode for a `bytes` field assumes UTF-8 text, but `signature` is a
    raw Ed25519 signature (arbitrary binary) and reliably fails that
    assumption — confirmed by an `invalid utf-8 sequence` crash while
    writing this module's e2e tests. `model_dump(mode="python")` plus a
    manual base64 pass for `bytes`/`datetime` avoids that entirely.
    """
    data = token.model_dump(mode="python")
    payload_json = json.dumps(data, default=_json_default)
    return base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("ascii")


def decode_token(blob: str) -> TokenPayload:
    """Reverse of `encode_token`.

    Raises `ValueError` on malformed input; callers must treat that
    identically to an invalid token (never a distinct error category —
    a corrupt blob and a forged one are indistinguishable to the caller).
    """
    try:
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
        data = json.loads(raw)
        if data.get("signature") is not None:
            data["signature"] = base64.b64decode(data["signature"])
    except Exception as exc:  # base64/json/ascii errors — not enumerable in advance
        raise ValueError("Malformed session token") from exc
    return TokenPayload.model_validate(data)
