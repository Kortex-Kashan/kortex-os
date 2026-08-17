"""
KORTEX Security Engine — Authentication Manager (Milestone M3).

Implements `IAuthenticationManager` over `IDataStore` exclusively — Security
Engine never opens a database connection, executes raw SQL, or touches the
filesystem directly, following the same boundary `SecretStore` (M2) already
establishes.

    AuthenticationManager
        |
        v
    IDataStore  (Storage Engine — plain persistence only)

Credential model (ratified M3 architecture decision):
    - USER:              username/principal_id + password, verified via
                          Argon2id (`argon2-cffi`).
    - SERVICE_PRINCIPAL:  principal_id + a pre-shared credential, verified via
                          the *same* Argon2id one-way hash-and-verify path.
    - AGENT:              identical mechanism to SERVICE_PRINCIPAL.

All three principal types are verified uniformly via a single Argon2id
`credential_hash` column on `PrincipalRecord` — there is no plaintext
credential field, no reversible/encrypted credential storage, and (contrary
to an earlier draft of this milestone's design) no dependency on
`SecretStore`: nothing here ever needs a credential back after verifying it,
so one-way hashing is sufficient and simpler than round-tripping through the
secret vault. This is a deliberate scope decision, not an oversight — see the
M3 implementation plan for the full reasoning.

Token model (ratified): Ed25519-signed `TokenPayload`, reusing
`VerificationService.sign`/`verify_signature_strict` exactly as already
implemented in M1 — no JWT, no new signature algorithm. The signing keypair
is bootstrapped from `KORTEX_AUTH_SIGNING_PRIVATE_KEY`, decoded by
`AuthenticationManager.decode_signing_key` (mirroring
`SecretStore.decode_master_key`'s exact hex/Base64 contract), and is
cryptographically and operationally separate from `SecretStore`'s
`KORTEX_MASTER_KEY` — different env var, different algorithm, different
decode path, and (since M3 has no `SecretStore` dependency at all) no shared
object reference of any kind.

Explicit M3 non-goals (see the ratified architecture / implementation plan):
    - No token revocation, blacklist, or `revoke_token()` method.
    - No JWT / PyJWT / python-jose / caller-selectable token algorithm.
    - No `ICacheStore` usage — token verification is self-validating
      (signature + expiry) plus one authoritative `IDataStore` re-check.
    - No RBAC/ABAC/authorization evaluation of any kind. `roles`/`attributes`
      on `SecurityPrincipal` are carried through as opaque identity metadata
      only — this module never interprets or acts on them. Authentication
      establishes identity; it does not imply authorization.
    - No principal provisioning/registration capability — `PrincipalRecord`
      rows are assumed to already exist; this module only verifies.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, NamedTuple, Optional, cast

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.security.crypto import VerificationService
from kortex.engines.security.exceptions import (
    AuthenticationError,
    InvalidTokenError,
    SecurityEngineError,
    SigningKeyError,
    TokenExpiredError,
)
from kortex.engines.security.interfaces import IAuthenticationManager, ICryptoProvider
from kortex.engines.security.models import (
    CryptographicSignature,
    PrincipalRecord,
    PrincipalType,
    SecurityPrincipal,
    TokenPayload,
)
from kortex.engines.storage.interfaces import IDataStore

_SIGNING_KEY_LENGTH_BYTES = 32
_HEX_KEY_LENGTH_CHARS = 64
_TOKEN_TTL = timedelta(minutes=15)

# Generic, identical failure message for every authentication denial reason
# (unknown principal, disabled principal, wrong credential, malformed
# credential field) — enumeration resistance: a caller must never be able to
# distinguish "unknown user" from "wrong password" from the response shape.
_GENERIC_AUTH_FAILURE_MESSAGE = "Authentication failed: invalid credentials."

# Maps a principal_type string to the credential field name expected in the
# `credentials` dict passed to `authenticate()`. Unknown/missing
# `principal_type` values resolve to `None` here, which fails closed.
_CREDENTIAL_FIELD_BY_TYPE = {
    PrincipalType.USER.value: "password",
    PrincipalType.SERVICE_PRINCIPAL.value: "credential",
    PrincipalType.AGENT.value: "credential",
}


class _PrincipalSnapshot(NamedTuple):
    """Plain data extracted from a `PrincipalRecord` inside a transaction.

    Never the ORM object itself — mirrors `SecretStore.get_secret`'s existing
    pattern of extracting plain values before the session/transaction closes,
    avoiding any detached-instance-access hazard.
    """

    principal_id: str
    principal_type: str
    tenant_id: str
    enabled: bool
    credential_hash: Optional[str]
    roles: list[str]
    attributes: Dict[str, Any]


class AuthenticationManager(IAuthenticationManager):
    """M3 authentication manager. Implements `IAuthenticationManager` over `IDataStore`."""

    def __init__(self, data_store: IDataStore, crypto_provider: ICryptoProvider, signing_private_key: bytes) -> None:
        """Initialize AuthenticationManager with an already-decoded 32-byte Ed25519 signing key.

        Args:
            data_store: Storage Engine's `IDataStore` — the exclusive persistence path.
            crypto_provider: `ICryptoProvider` implementation (e.g. `LocalCrypto`).
            signing_private_key: Already-decoded 32-byte raw Ed25519 private
                key. `AuthenticationManager` never resolves this itself — the
                caller (normally `SecurityEngine`) sources and decodes it from
                `KORTEX_AUTH_SIGNING_PRIVATE_KEY`, keeping this class fully
                deterministic and testable, and never falls back to a
                generated/default key.

        Raises:
            SigningKeyError: If `signing_private_key` is not exactly 32 bytes.
                Never includes the key's value in the error message.
        """
        is_bytes = isinstance(signing_private_key, bytes)
        if not is_bytes or len(signing_private_key) != _SIGNING_KEY_LENGTH_BYTES:
            actual = len(signing_private_key) if is_bytes else type(signing_private_key).__name__
            raise SigningKeyError(
                f"Authentication signing key must be exactly {_SIGNING_KEY_LENGTH_BYTES} bytes, got {actual}."
            )
        self._data_store = data_store
        self._crypto = crypto_provider
        self._verification_service = VerificationService(crypto_provider)
        self._signing_private_key = signing_private_key
        self._signing_public_key = crypto_provider.derive_ed25519_public_key(signing_private_key)
        self._password_hasher = PasswordHasher()

    # -- Signing key decoding (KORTEX_AUTH_SIGNING_PRIVATE_KEY contract) ---------

    @staticmethod
    def decode_signing_key(raw: str) -> bytes:
        """Decode a `KORTEX_AUTH_SIGNING_PRIVATE_KEY` configuration value into 32 raw bytes.

        Accepted representations, mirroring `SecretStore.decode_master_key`'s
        contract exactly:
            - a 64-character hexadecimal string
            - a Base64 string decoding to exactly 32 bytes

        Fails closed (`SigningKeyError`) for anything else — missing, wrong
        length, or undecodable input. Never logs or includes the raw or
        decoded key value in any exception message. There is no fallback to
        a generated/default signing key under any circumstance.

        Args:
            raw: The configuration string value (already resolved from
                `KORTEX_AUTH_SIGNING_PRIVATE_KEY` by the caller).

        Raises:
            SigningKeyError: If `raw` is empty, not a string, or does not
                decode to exactly 32 bytes via hex or Base64.
        """
        if not isinstance(raw, str) or not raw.strip():
            raise SigningKeyError("KORTEX_AUTH_SIGNING_PRIVATE_KEY is missing or empty.")
        stripped = raw.strip()

        if len(stripped) == _HEX_KEY_LENGTH_CHARS:
            try:
                decoded = bytes.fromhex(stripped)
            except ValueError as exc:
                raise SigningKeyError(
                    f"KORTEX_AUTH_SIGNING_PRIVATE_KEY is {_HEX_KEY_LENGTH_CHARS} characters "
                    "but is not valid hexadecimal."
                ) from exc
        else:
            import base64

            try:
                decoded = base64.b64decode(stripped, validate=True)
            except Exception as exc:
                raise SigningKeyError(
                    "KORTEX_AUTH_SIGNING_PRIVATE_KEY is not a valid 64-character hex string or Base64 string."
                ) from exc

        if len(decoded) != _SIGNING_KEY_LENGTH_BYTES:
            raise SigningKeyError(
                f"KORTEX_AUTH_SIGNING_PRIVATE_KEY must decode to exactly "
                f"{_SIGNING_KEY_LENGTH_BYTES} bytes, got {len(decoded)}."
            )
        return decoded

    # -- Canonical token signing payload -----------------------------------------

    @staticmethod
    def _build_signing_payload(
        token_id: str,
        principal_id: str,
        principal_type: str,
        tenant_id: str,
        issued_at_utc: datetime,
        expires_at_utc: datetime,
    ) -> bytes:
        """Build the canonical, length-prefixed byte encoding signed/verified for a token.

        Length-prefixing (not delimiter-joining) makes the encoding injective,
        mirroring `SecretStore._build_aad`'s exact technique — no two distinct
        claim sets can ever collide to the same byte string.
        """
        parts = (
            token_id,
            principal_id,
            principal_type,
            tenant_id,
            issued_at_utc.isoformat(),
            expires_at_utc.isoformat(),
        )
        encoded = b""
        for part in parts:
            part_bytes = part.encode("utf-8")
            encoded += len(part_bytes).to_bytes(4, "big") + part_bytes
        return encoded

    # -- Storage-failure normalization -------------------------------------------

    async def _run_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        """Run `action` via `IDataStore.execute_in_transaction`, normalizing any
        failure that is not already a `SecurityEngineError` — an underlying
        storage failure must never be silently converted into a successful
        authentication or a misleading "unknown principal" outcome.
        """
        try:
            return await self._data_store.execute_in_transaction(action)
        except SecurityEngineError:
            raise
        except Exception as exc:
            raise SecurityEngineError("Authentication storage operation failed.") from exc

    async def _load_principal(
        self, tenant_id: str, principal_id: str, principal_type: str
    ) -> Optional[_PrincipalSnapshot]:
        """Look up a `PrincipalRecord` by `(tenant_id, principal_id, principal_type)`.

        Returns `None` if no matching record exists — callers must treat this
        identically to "found but disabled" or "wrong credential" for
        enumeration-resistance purposes.
        """

        async def _action(session: AsyncSession) -> Optional[_PrincipalSnapshot]:
            stmt = select(PrincipalRecord).where(
                PrincipalRecord.tenant_id == tenant_id,
                PrincipalRecord.principal_id == principal_id,
                PrincipalRecord.principal_type == principal_type,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                return None
            return _PrincipalSnapshot(
                principal_id=record.principal_id,
                principal_type=record.principal_type,
                tenant_id=record.tenant_id,
                enabled=record.enabled,
                credential_hash=record.credential_hash,
                roles=list(record.roles),
                attributes=dict(record.attributes),
            )

        return cast(Optional[_PrincipalSnapshot], await self._run_in_transaction(_action))

    def _verify_credential(self, presented: str, stored_hash: str) -> bool:
        """Verify `presented` against `stored_hash` via Argon2id.

        Fails closed for any exception (wrong credential, malformed hash, or
        any other Argon2id-layer failure) — `PasswordHasher.verify` raises on
        every non-match outcome and returns `True` only on a genuine match, so
        catching broadly here can never turn a failure into a success.
        """
        try:
            return bool(self._password_hasher.verify(stored_hash, presented))
        except Exception:
            return False

    # -- IAuthenticationManager ---------------------------------------------------

    async def authenticate(self, credentials: Dict[str, Any]) -> SecurityPrincipal:
        """Verify credentials and return the resulting `SecurityPrincipal`.

        Uniform across `USER`/`SERVICE_PRINCIPAL`/`AGENT` — all three verify
        via the same one-way Argon2id hash-and-verify path. Every failure
        reason (missing field, unknown principal, disabled principal, wrong
        credential, malformed stored hash) raises the identical
        `AuthenticationError` with the identical message — never a distinct
        shape that would let a caller distinguish "unknown user" from "wrong
        password" (enumeration resistance).

        Raises:
            AuthenticationError: For any credential failure.
            SecurityEngineError: If the underlying storage operation fails.
        """
        principal_type = credentials.get("principal_type") if isinstance(credentials, dict) else None
        tenant_id = credentials.get("tenant_id") if isinstance(credentials, dict) else None
        principal_id = credentials.get("principal_id") if isinstance(credentials, dict) else None
        credential_field = _CREDENTIAL_FIELD_BY_TYPE.get(principal_type) if isinstance(principal_type, str) else None
        presented = credentials.get(credential_field) if (isinstance(credentials, dict) and credential_field) else None

        if not (
            isinstance(tenant_id, str)
            and tenant_id
            and isinstance(principal_id, str)
            and principal_id
            and isinstance(principal_type, str)
            and credential_field is not None
            and isinstance(presented, str)
            and presented
        ):
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        snapshot = await self._load_principal(tenant_id, principal_id, principal_type)
        if snapshot is None or not snapshot.enabled or not snapshot.credential_hash:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        if not self._verify_credential(presented, snapshot.credential_hash):
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        return SecurityPrincipal(
            principal_id=snapshot.principal_id,
            principal_type=PrincipalType(snapshot.principal_type),
            tenant_id=snapshot.tenant_id,
            roles=list(snapshot.roles),
            attributes=dict(snapshot.attributes),
        )

    async def issue_token(self, principal: SecurityPrincipal) -> TokenPayload:
        """Issue a short-lived (15-minute), Ed25519-signed session token for `principal`.

        Never caches, revokes, or persists the issued token anywhere — the
        token is fully self-contained and self-validating.
        """
        token_id = os.urandom(16).hex()
        issued_at_utc = datetime.now(timezone.utc)
        expires_at_utc = issued_at_utc + _TOKEN_TTL

        payload_bytes = self._build_signing_payload(
            token_id, principal.principal_id, principal.principal_type.value, principal.tenant_id,
            issued_at_utc, expires_at_utc,
        )
        signature = self._verification_service.sign(
            payload_bytes, self._signing_private_key, self._signing_public_key
        )

        return TokenPayload(
            token_id=token_id,
            principal_id=principal.principal_id,
            principal_type=principal.principal_type,
            tenant_id=principal.tenant_id,
            issued_at_utc=issued_at_utc,
            expires_at_utc=expires_at_utc,
            signature=signature.signature,
        )

    async def verify_token(self, token: TokenPayload) -> SecurityPrincipal:
        """Verify a session token and return the resolved `SecurityPrincipal`.

        Mandatory order (never reversed):
            1. Reject immediately if `token.signature` is absent.
            2. Verify the Ed25519 signature over the token's own claims —
               occurs before any claim is trusted for any decision.
            3. Re-validate the principal's current enabled/existence state
               via a fresh `IDataStore` lookup (not a revocation list —
               reuses the exact same lookup path `authenticate()` uses).
            4. Validate `issued_at_utc`/`expires_at_utc` against a freshly
               read current time — never a caller-supplied or cached time.

        Raises:
            InvalidTokenError: If the signature is missing/invalid, or the
                principal no longer exists/is disabled.
            InvalidSignatureError: If Ed25519 verification fails (propagated
                directly from `VerificationService.verify_signature_strict`).
            TokenExpiredError: If the token is expired or not yet valid.
        """
        if token.signature is None:
            raise InvalidTokenError("Token has no signature.")

        payload_bytes = self._build_signing_payload(
            token.token_id, token.principal_id, token.principal_type.value, token.tenant_id,
            token.issued_at_utc, token.expires_at_utc,
        )
        signature_model = CryptographicSignature(
            algorithm="ed25519", signature=token.signature, public_key=self._signing_public_key
        )
        # Raises `InvalidSignatureError` on any mismatch — never caught/wrapped
        # here, per the mandated fail-closed order (signature before claims).
        self._verification_service.verify_signature_strict(payload_bytes, signature_model)

        snapshot = await self._load_principal(token.tenant_id, token.principal_id, token.principal_type.value)
        if snapshot is None or not snapshot.enabled:
            raise InvalidTokenError("Token principal is no longer valid.")

        now = datetime.now(timezone.utc)
        try:
            is_temporally_invalid = now > token.expires_at_utc or now < token.issued_at_utc
        except TypeError:
            # Naive/aware datetime comparison mismatch — treat as invalid,
            # never as valid, on any comparison failure.
            is_temporally_invalid = True
        if is_temporally_invalid:
            raise TokenExpiredError("Token is expired or not yet valid.")

        return SecurityPrincipal(
            principal_id=snapshot.principal_id,
            principal_type=PrincipalType(snapshot.principal_type),
            tenant_id=snapshot.tenant_id,
            roles=list(snapshot.roles),
            attributes=dict(snapshot.attributes),
        )
