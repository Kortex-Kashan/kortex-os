"""Capability Execution Grant — minting, verification, and the input-side
sensitive-value gate for `browser.type` (Browser-B5.3).

Reuses the *existing*, already-proven Ed25519 signing/verification path
(`SecurityEngine.verification_service` -> `ICryptoProvider.sign_ed25519`/
`verify_ed25519`, the same primitive `DurableApprovalManager` already uses
for human approval decisions) — nothing here introduces a new cryptographic
algorithm or a new signing/verification code path. See
`docs/architecture/browser_b5_architecture_gate.md` §6 for the full design
rationale, and §29 Q1 for the explicit, disclosed limitation this module
does *not* attempt to solve (backend-to-a-specific-desktop-instance
addressing outside an already-open request).

Grant lifetime is deliberately short (`DEFAULT_GRANT_TTL_SECONDS`, seconds —
never the 24-hour default approval-request timeout `DurableAIApprovalPolicy`
uses) — a grant that outlives the AI tool call's own timeout would let a
slow-to-redeem grant sit around uselessly; expiring it fast makes
`GrantExpired` the failure the caller actually sees, rather than a hang.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from kortex.engines.browser.models import (
    BrowserCapabilityExecutionGrant,
    BrowserCapabilityTarget,
    BrowserElementSelector,
)
from kortex.engines.security.crypto import VerificationService
from kortex.engines.security.models import CryptographicSignature

if TYPE_CHECKING:
    from kortex.engines.security.interfaces import ICryptoProvider

DEFAULT_GRANT_TTL_SECONDS: int = 20

# -- Canonicalization / parameter hashing ------------------------------------


def canonicalize_and_hash(capability_name: str, parameters: dict[str, Any]) -> str:
    """SHA-256 of a deterministic, sorted-key JSON encoding of
    `{capability, parameters}` — the exact same pattern
    `DurableAIApprovalPolicy.requires_approval`'s own `action_fingerprint`
    uses (`ai/governance.py`), applied here to bind a Grant to the precise
    parameters a capability handler was actually asked to execute, not
    merely to the fact that *some* call to that capability was approved.

    `parameters` is passed through `json.dumps` directly — a value like
    `ui_input_text`'s secret-shaped text is hashed, never stored in the
    clear anywhere the hash itself is logged/audited (a SHA-256 digest does
    not expose its own preimage).
    """
    payload = {"capability": capability_name, "parameters": parameters}
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_grant_payload(grant: BrowserCapabilityExecutionGrant) -> bytes:
    """The exact bytes a Grant's signature covers. Colon-joined, mirroring
    `DurableApprovalManager.submit_decision`'s own canonical-payload
    convention (`workflow/approval.py`) — every field that participates in
    a security decision is included, in a fixed order, so any single-field
    tamper (change the capability, the target, the parameter hash, the
    tenant, the principal, or either timestamp) changes the signed bytes
    and therefore fails verification."""
    parts = [
        grant.grant_id,
        grant.tenant_id,
        grant.principal_id,
        grant.capability_name,
        grant.browser_profile_id,
        grant.surface_id,
        "" if grant.navigation_generation is None else str(grant.navigation_generation),
        grant.canonicalized_parameters_hash,
        # Already-formatted strings (see `models.py`'s own doc on why
        # `issued_at`/`expires_at` are `str`, not `datetime`) — used
        # verbatim, never reformatted.
        grant.issued_at,
        grant.expires_at,
    ]
    return ":".join(parts).encode("utf-8")


def mint_grant(
    *,
    crypto_provider: ICryptoProvider,
    signing_private_key: bytes,
    signing_public_key: bytes,
    tenant_id: str,
    principal_id: str,
    capability_name: str,
    target: BrowserCapabilityTarget,
    parameters: dict[str, Any],
    ttl_seconds: int = DEFAULT_GRANT_TTL_SECONDS,
) -> BrowserCapabilityExecutionGrant:
    """Mint a signed, short-lived Capability Execution Grant.

    Called only from inside a Browser capability handler, itself only ever
    reached after `CapabilityDispatcher` has already verified the caller's
    identity and RBAC/ABAC authorization — this function does not
    independently re-check either; it trusts its own caller's context
    exactly as every other capability handler does.
    """
    now = datetime.now(UTC)
    grant_id = str(uuid.uuid4())
    unsigned = BrowserCapabilityExecutionGrant(
        grant_id=grant_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        capability_name=capability_name,
        browser_profile_id=target.browser_profile_id,
        surface_id=target.surface_id,
        navigation_generation=target.navigation_generation,
        canonicalized_parameters_hash=canonicalize_and_hash(capability_name, parameters),
        # `.isoformat()` explicitly, at construction time — never a
        # `datetime` field left for Pydantic to reformat differently on
        # JSON output later. See `models.py`'s own doc comment on why.
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
        # Placeholder — replaced below once the real payload is signed.
        # A frozen model can't be mutated in place, so the grant is built
        # twice: once to compute the exact canonical payload, once with the
        # real signature. Never partially constructed or returned mid-way.
        signature="0" * 64,
    )
    payload = _canonical_grant_payload(unsigned)
    signature = VerificationService(crypto_provider).sign(payload, signing_private_key, signing_public_key)
    return unsigned.model_copy(update={"signature": signature.signature.hex()})


class GrantVerificationResult:
    """Outcome of `verify_grant`. `is_valid=False` never distinguishes *why*
    to a caller past this module's own boundary — the specific reason is
    for logging/testing only, matching `BrowserGrantInvalidError`'s own
    "never tell a probing caller which check failed" posture."""

    __slots__ = ("is_valid", "reason")

    def __init__(self, is_valid: bool, reason: str = "") -> None:
        self.is_valid = is_valid
        self.reason = reason


def verify_grant(
    grant: BrowserCapabilityExecutionGrant,
    *,
    crypto_provider: ICryptoProvider,
    verification_public_key: bytes,
    now: datetime | None = None,
) -> GrantVerificationResult:
    """Independently re-verify a Grant's signature, shape, and expiry.

    Does **not** check single-use redemption — that is inherently a
    stateful property of *where the grant is redeemed* (the desktop
    process, Browser-B5.4), not something a stateless verification
    function can determine. Does **not** check live surface/tenant/profile
    binding either, for the same reason — see
    `browser_b5_architecture_gate.md` §8/§17 and `apps/desktop-tauri`'s
    `browser_grant.rs` for where those live, stateful checks run.

    This function exists so the *cryptographic and shape* half of grant
    verification is independently testable backend-side (proving tamper-
    detection, expiry, and canonicalization are all correct) without
    needing a live WebView2/Tauri process — the same "prove what's provable
    without a live runtime" split Browser-B1..B4 already established.
    """
    now = now or datetime.now(UTC)
    try:
        expires_at = datetime.fromisoformat(grant.expires_at)
        issued_at = datetime.fromisoformat(grant.issued_at)
    except ValueError:
        return GrantVerificationResult(False, "issued_at/expires_at is not a valid ISO 8601 timestamp")
    if now >= expires_at:
        return GrantVerificationResult(False, "expired")
    if now < issued_at:
        # A grant timestamped in the future is exactly as suspicious as one
        # already expired — never treated as "not yet valid but fine".
        return GrantVerificationResult(False, "issued_at in the future")
    try:
        signature_bytes = bytes.fromhex(grant.signature)
    except ValueError:
        return GrantVerificationResult(False, "signature is not valid hex")
    payload = _canonical_grant_payload(grant)
    signature = CryptographicSignature(
        algorithm="ed25519", signature=signature_bytes, public_key=verification_public_key
    )
    if not VerificationService(crypto_provider).verify_signature(payload, signature):
        return GrantVerificationResult(False, "signature verification failed")
    return GrantVerificationResult(True)


# -- Sensitive-input gate for `browser.type` ---------------------------------

_SENSITIVE_TARGET_NAME_PATTERN = re.compile(
    r"(?i)\b(password|passphrase|pin\b|otp|one[-_ ]?time[-_ ]?(code|password)|"
    r"security[-_ ]?code|security[-_ ]?answer|cvv|cvc|card[-_ ]?number|"
    r"recovery[-_ ]?code|backup[-_ ]?code|totp)\b"
)
# Deliberately duplicated (not imported) from `kortex.engines.ai.tools`'s
# private `_SECRET_VALUE_PATTERN` — the two are intentionally kept
# identical in intent (bearer tokens, `sk-`/`pk-`/`key-`/`secret-`-prefixed
# API keys) but independently maintained, rather than reaching across a
# module boundary to import another module's private, underscore-prefixed
# constant.
_SECRET_VALUE_SHAPED_PATTERN = re.compile(
    r"(?i)\b(bearer\s+[a-zA-Z0-9_\-\.]{8,}|(?:sk|pk|key|secret)[_-][a-zA-Z0-9_\-]{12,})\b"
)
_JWT_SHAPED_PATTERN = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_CARD_NUMBER_SHAPED_PATTERN = re.compile(r"^(?:\d[ -]?){13,19}$")
_OTP_SHAPED_PATTERN = re.compile(r"^\d{6,8}$")


def is_sensitive_type_target(selector: BrowserElementSelector) -> bool:
    """True if the *target field itself* is named/labeled like a
    password/PIN/OTP/security-code/card-number/recovery-code input,
    regardless of what value would be typed into it. This is the stronger
    of the two signals `browser.type` checks (see `looks_like_secret_value`
    for the value-shape half) — a field labeled "Password" is refused
    outright, since even an apparently-ordinary-looking value typed into it
    is still a credential by construction."""
    for candidate in (selector.accessible_name, selector.role, selector.node_ref):
        if candidate and _SENSITIVE_TARGET_NAME_PATTERN.search(candidate):
            return True
    return False


def looks_like_secret_value(value: str) -> bool:
    """True if `value` itself is shaped like a secret: a bearer token, an
    `sk-`/`pk-`/`key-`/`secret-`-prefixed API key (`_SECRET_VALUE_PATTERN`,
    reused verbatim from `kortex.engines.ai.tools` rather than
    re-implemented — the same detection philosophy already proven for
    output redaction, applied here at the input boundary instead), a
    JWT-shaped three-segment token, a card-number-shaped digit sequence, or
    a 6-8 digit OTP-shaped value.

    Deliberately does **not** attempt to detect an arbitrary human password
    by shape alone — an ordinary-looking password has no reliable
    structural signature to pattern-match, and this limitation is disclosed
    explicitly (`browser_b5_architecture_gate.md` §28) rather than
    papered over with a false sense of coverage. `is_sensitive_type_target`
    is the stronger, complementary check for exactly this gap: a password
    field is refused because of what it's *labeled*, not because of what
    looks like a password.
    """
    stripped = value.strip()
    if not stripped:
        return False
    if _SECRET_VALUE_SHAPED_PATTERN.search(stripped):
        return True
    if _JWT_SHAPED_PATTERN.match(stripped):
        return True
    if _CARD_NUMBER_SHAPED_PATTERN.match(stripped):
        return True
    return bool(_OTP_SHAPED_PATTERN.match(stripped))


def generate_grant_signing_keypair(crypto_provider: ICryptoProvider) -> tuple[bytes, bytes]:
    """Generate the process-lifetime Ed25519 keypair `BrowserCapabilityEngine`
    signs every Grant with, via the *existing* `ICryptoProvider` (never a
    new cryptography implementation). Returns `(private_key_bytes,
    public_key_bytes)` — the exact same order `ICryptoProvider.generate_ed25519_keypair`
    itself documents and returns; this function does not reorder it.

    **Disclosed limitation, stated plainly** (not silently assumed away):
    this key is generated fresh in-memory each process start — it does not
    survive a backend restart. This is the exact same posture
    `AgentOrchestrator`'s own `_DEFAULT_SIGNING_SECRET` module-level
    fallback already has in production (`ai/engine.py`'s `agent.py`,
    `secrets.token_bytes(32)` at import time) for HMAC resume-token
    signing — an already-accepted pattern in this codebase for a
    short-lived, non-durable signing key, not a new risk this module
    introduces. A restart invalidates every outstanding grant, which fails
    closed (the desktop's cached verification key stops matching, every
    subsequent redemption attempt fails `GrantInvalid`) rather than
    insecurely — see `browser_b5_architecture_gate.md` §28.
    """
    return crypto_provider.generate_ed25519_keypair()


def new_grant_nonce() -> str:
    """A cryptographically random value distinct from `grant_id`, reserved
    for a future desktop-side single-use-tracking scheme that wants an
    unguessable token separate from the (SecurityEngine-visible) grant
    identifier. Not currently consumed by `verify_grant` — single-use
    enforcement is the desktop redeem command's own responsibility
    (`browser_b5_architecture_gate.md` §17), tracked there by `grant_id`
    directly, since `grant_id` is already unguessable (UUIDv4) and
    single-purpose. Kept as a small, independent primitive rather than
    baked into `mint_grant` so a future design change doesn't need to touch
    the Grant's own frozen shape.
    """
    return secrets.token_hex(16)


__all__ = [
    "DEFAULT_GRANT_TTL_SECONDS",
    "GrantVerificationResult",
    "canonicalize_and_hash",
    "generate_grant_signing_keypair",
    "is_sensitive_type_target",
    "looks_like_secret_value",
    "mint_grant",
    "new_grant_nonce",
    "verify_grant",
]
