"""Shared `OAuthStatePayload` <-> opaque string codec.

Extracted from `SecurityEngine._encode_oauth_state`/`_decode_oauth_state`
(Phase A) so `IntegrationOAuthManager` (Integration Hub M2, `intent=
"connector_link"`) can round-trip the same payload shape — including the
newer `profile_id` field neither Phase A intent ("login"/"link") ever sets —
without duplicating the encoding logic. `SecurityEngine`'s own two methods
now delegate here unchanged in behavior.

Not itself a secret — the string's integrity comes entirely from the
embedded Ed25519 signature, verified separately by
`AuthenticationManager.verify_oauth_state`. This module only encodes/decodes
the wire representation; it neither signs nor verifies.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime

from kortex.engines.security.models import OAuthStatePayload


class OAuthStateDecodeError(ValueError):
    """Raised when an opaque `state` string is not validly encoded. Callers
    map this to their own domain exception (e.g. `OAuthStateError`) — never
    surfaced to a caller directly."""


def encode_oauth_state(state: OAuthStatePayload) -> str:
    """Encode a signed `OAuthStatePayload` into the opaque string carried
    through a provider's `state` query parameter."""
    payload = {
        "nonce": state.nonce,
        "provider": state.provider,
        "intent": state.intent,
        "tenant_id": state.tenant_id,
        "principal_id": state.principal_id,
        "principal_type": state.principal_type,
        "profile_id": state.profile_id,
        "issued_at_utc": state.issued_at_utc.isoformat(),
        "expires_at_utc": state.expires_at_utc.isoformat(),
        "signature": state.signature.hex() if state.signature else None,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_oauth_state(encoded: str) -> OAuthStatePayload:
    """Inverse of `encode_oauth_state`. Raises `OAuthStateDecodeError` for
    any malformed input — a missing/invalid `state` is a normal, expected
    failure mode (a stale link, a tampered value), never a crash."""
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
        payload = json.loads(raw)
        return OAuthStatePayload(
            nonce=payload["nonce"],
            provider=payload["provider"],
            intent=payload["intent"],
            tenant_id=payload.get("tenant_id"),
            principal_id=payload.get("principal_id"),
            principal_type=payload.get("principal_type"),
            profile_id=payload.get("profile_id"),
            issued_at_utc=datetime.fromisoformat(payload["issued_at_utc"]),
            expires_at_utc=datetime.fromisoformat(payload["expires_at_utc"]),
            signature=bytes.fromhex(payload["signature"]) if payload.get("signature") else None,
        )
    except Exception as exc:
        raise OAuthStateDecodeError("The provided OAuth state value is malformed.") from exc
