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

import hashlib
import os
import re
import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple, cast

from argon2 import PasswordHasher
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.security.crypto import VerificationService
from kortex.engines.security.exceptions import (
    AuthenticationError,
    BootstrapClosedError,
    BootstrapValidationError,
    InvalidTokenError,
    OAuthLinkConflictError,
    OAuthStateError,
    PasswordPolicyError,
    PasswordResetError,
    PrincipalAlreadyExistsError,
    PrincipalRegistrationValidationError,
    SecurityEngineError,
    SigningKeyError,
    TokenExpiredError,
)
from kortex.engines.security.interfaces import IAuthenticationManager, ICryptoProvider
from kortex.engines.security.models import (
    CryptographicSignature,
    OAuthIdentityLinkRecord,
    OAuthStatePayload,
    PasswordResetTokenRecord,
    PrincipalRecord,
    PrincipalType,
    RolePermissionRecord,
    SecurityPrincipal,
    TokenPayload,
)
from kortex.engines.storage.interfaces import IDataStore

_SIGNING_KEY_LENGTH_BYTES = 32
_HEX_KEY_LENGTH_CHARS = 64
_TOKEN_TTL = timedelta(minutes=15)
_MIN_PASSWORD_LENGTH = 8
_RESET_TOKEN_TTL = timedelta(minutes=30)
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_OAUTH_STATE_TTL = timedelta(minutes=10)
# Domain separation: an OAuth `state` payload and a session `TokenPayload`
# are both signed with the same platform Ed25519 keypair, so a distinct
# byte prefix ensures one can never be replayed as the other even if their
# other fields happened to coincide.
_OAUTH_STATE_DOMAIN_PREFIX = b"oauth-state:"

# M7.1 first-run bootstrap. A fixed, well-known sentinel identity used
# exclusively as a concurrency mutex (see `bootstrap_first_admin` below) —
# never a real, authenticatable principal (`enabled=False`,
# `credential_hash=None`, so even a guessed match always fails closed in
# `authenticate()`).
_BOOTSTRAP_LOCK_TENANT_ID = "__system__"
_BOOTSTRAP_LOCK_PRINCIPAL_ID = "__bootstrap_lock__"
_BOOTSTRAP_MIN_PASSWORD_LENGTH = 8

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
    credential_hash: str | None
    roles: list[str]
    attributes: dict[str, Any]


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
    ) -> _PrincipalSnapshot | None:
        """Look up a `PrincipalRecord` by `(tenant_id, principal_id, principal_type)`.

        Returns `None` if no matching record exists — callers must treat this
        identically to "found but disabled" or "wrong credential" for
        enumeration-resistance purposes.
        """

        async def _action(session: AsyncSession) -> _PrincipalSnapshot | None:
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

        return cast(_PrincipalSnapshot | None, await self._run_in_transaction(_action))

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

    async def authenticate(self, credentials: dict[str, Any]) -> SecurityPrincipal:
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

    async def provision_principal(
        self,
        tenant_id: str,
        principal_id: str,
        principal_type: PrincipalType,
        credential: str,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> bool:
        """Idempotently ensure a `PrincipalRecord` exists for a system/service identity (M6.2-1).

        This is bootstrap-time infrastructure setup for the platform's own
        AI system principal, not a general-purpose principal registration
        capability — it does not contradict this module's own "no
        principal provisioning" precedent (see module docstring), which
        concerns provisioning as something a *caller* can invoke; this
        method is only ever called from `kortex.api.kernel_bootstrap`.

        Never overwrites an existing row: if a `PrincipalRecord` already
        exists for `(tenant_id, principal_id, principal_type)`, this is a
        no-op that returns `False` — `credential`/`roles`/`attributes` are
        applied only at first creation, so a restart never silently
        rotates the stored credential hash or role grants underneath an
        already-provisioned principal.

        Returns:
            True if a new `PrincipalRecord` was created, False if one
            already existed.
        """
        credential_hash = self._password_hasher.hash(credential)

        async def _action(session: AsyncSession) -> bool:
            stmt = select(PrincipalRecord).where(
                PrincipalRecord.tenant_id == tenant_id,
                PrincipalRecord.principal_id == principal_id,
                PrincipalRecord.principal_type == principal_type.value,
            )
            res = await session.execute(stmt)
            existing = res.scalar_one_or_none()
            if existing is not None:
                return False
            record = PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type=principal_type.value,
                enabled=True,
                credential_hash=credential_hash,
                roles=list(roles or []),
                attributes=dict(attributes or {}),
            )
            session.add(record)
            return True

        return cast(bool, await self._run_in_transaction(_action))

    # -- First-run bootstrap (Milestone M7.1) -------------------------------

    async def is_bootstrap_required(self) -> bool:
        """True if no `PrincipalRecord` exists anywhere in the system yet.

        Read-only, cheap (`SELECT COUNT(*)`), and deliberately global — not
        scoped to any one `tenant_id` — since a fresh KORTEX install has no
        tenant at all yet; "is there a single principal anywhere" is the
        only meaningful definition of "has this install ever been set up."
        Consulted by `Kernel.health_check()` (via `SecurityEngine`), not
        exposed as its own Kernel capability — `/health` is already the
        established unauthenticated diagnostic surface, so no new
        bootstrap-exempt read capability is needed alongside
        `bootstrap_first_admin`'s unavoidable write one.
        """

        async def _action(session: AsyncSession) -> bool:
            count_stmt = select(func.count()).select_from(PrincipalRecord)
            total = (await session.execute(count_stmt)).scalar_one()
            return bool(total == 0)

        return cast(bool, await self._run_in_transaction(_action))

    async def bootstrap_first_admin(
        self,
        tenant_id: str,
        principal_id: str,
        password: str,
        roles: list[str],
        permissions: list[str],
    ) -> None:
        """Create the very first tenant/administrator identity on a fresh
        install, and close bootstrap permanently in the same transaction.

        Fail-closed by construction, not by convention:
          1. A `SELECT COUNT(*)` re-check inside the transaction rejects
             immediately (`BootstrapClosedError`) if any principal already
             exists — the common, non-racing case.
          2. A fixed sentinel `PrincipalRecord`
             (`_BOOTSTRAP_LOCK_TENANT_ID`/`_BOOTSTRAP_LOCK_PRINCIPAL_ID`) is
             inserted in the *same* transaction as the real admin principal
             and its RBAC grants. Two genuinely concurrent callers can both
             pass step 1 before either commits, but both attempt to insert
             the identical sentinel row — `PrincipalRecord`'s existing
             `UniqueConstraint("tenant_id", "principal_id", "principal_type")`
             (Milestone M3, unmodified here) lets the database itself reject
             the loser's entire transaction, admin principal and RBAC grants
             included, not just the sentinel. There is no window in which a
             second admin can be created, and no window in which a caller
             observes a sentinel-only, partially-bootstrapped system: either
             everything in this method commits together, or none of it does
             and bootstrap remains open for a retry.

        Raises:
            BootstrapValidationError: For empty tenant_id/principal_id, or a
                password shorter than `_BOOTSTRAP_MIN_PASSWORD_LENGTH`. Never
                includes the submitted password.
            BootstrapClosedError: If a principal already exists.
            SecurityEngineError: If the underlying storage operation fails,
                including the true-concurrency race described above (the
                loser observes a generic storage failure, not a clean
                `BootstrapClosedError` — both outcomes equally prevent a
                second admin from being created).
        """
        if not (isinstance(tenant_id, str) and tenant_id.strip()):
            raise BootstrapValidationError("A tenant ID is required.")
        if not (isinstance(principal_id, str) and principal_id.strip()):
            raise BootstrapValidationError("A username is required.")
        if not isinstance(password, str) or len(password) < _BOOTSTRAP_MIN_PASSWORD_LENGTH:
            raise BootstrapValidationError(f"Password must be at least {_BOOTSTRAP_MIN_PASSWORD_LENGTH} characters.")
        if not roles:
            raise BootstrapValidationError("At least one role is required for the bootstrap administrator.")

        credential_hash = self._password_hasher.hash(password)
        primary_role = roles[0]

        async def _action(session: AsyncSession) -> None:
            count_stmt = select(func.count()).select_from(PrincipalRecord)
            existing = (await session.execute(count_stmt)).scalar_one()
            if existing > 0:
                raise BootstrapClosedError("Bootstrap is no longer available: an administrator already exists.")

            session.add(
                PrincipalRecord(
                    id=str(uuid.uuid4()),
                    tenant_id=_BOOTSTRAP_LOCK_TENANT_ID,
                    principal_id=_BOOTSTRAP_LOCK_PRINCIPAL_ID,
                    principal_type=PrincipalType.SERVICE_PRINCIPAL.value,
                    enabled=False,
                    credential_hash=None,
                    roles=[],
                    attributes={},
                )
            )
            session.add(
                PrincipalRecord(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    principal_type=PrincipalType.USER.value,
                    enabled=True,
                    credential_hash=credential_hash,
                    roles=list(roles),
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )
            for permission in sorted(set(permissions)):
                session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=primary_role, permission=permission))

        await self._run_in_transaction(_action)

    async def issue_token(self, principal: SecurityPrincipal) -> TokenPayload:
        """Issue a short-lived (15-minute), Ed25519-signed session token for `principal`.

        Never caches, revokes, or persists the issued token anywhere — the
        token is fully self-contained and self-validating.
        """
        token_id = os.urandom(16).hex()
        issued_at_utc = datetime.now(UTC)
        expires_at_utc = issued_at_utc + _TOKEN_TTL

        payload_bytes = self._build_signing_payload(
            token_id,
            principal.principal_id,
            principal.principal_type.value,
            principal.tenant_id,
            issued_at_utc,
            expires_at_utc,
        )
        signature = self._verification_service.sign(payload_bytes, self._signing_private_key, self._signing_public_key)

        return TokenPayload(
            token_id=token_id,
            principal_id=principal.principal_id,
            principal_type=principal.principal_type,
            tenant_id=principal.tenant_id,
            issued_at_utc=issued_at_utc,
            expires_at_utc=expires_at_utc,
            signature=signature.signature,
        )

    # -- Principal Registration (Phase A: admin-provisioned "Register") ----

    async def register_principal(
        self,
        tenant_id: str,
        principal_id: str,
        password: str,
        roles: list[str],
        email: str | None = None,
    ) -> None:
        """Create a new `USER` principal. Admin-only at the capability layer
        (`SecurityEngine` gates this behind `security:principal:write`).

        Deliberately distinct from `provision_principal`: this is a real
        "create a user" operation and raises `PrincipalAlreadyExistsError`
        on a duplicate `(tenant_id, principal_id)` rather than silently
        no-op'ing — an admin submitting a duplicate username needs to see
        that conflict, not have it silently swallowed the way
        `provision_principal`'s own, unrelated system-principal-bootstrap
        use case requires.

        Raises:
            PrincipalRegistrationValidationError: Empty tenant/username,
                empty roles, or a malformed email. Never includes the
                submitted password.
            PasswordPolicyError: `password` is shorter than
                `_MIN_PASSWORD_LENGTH`.
            PrincipalAlreadyExistsError: A principal already exists at
                `(tenant_id, principal_id, USER)`.
        """
        if not (isinstance(tenant_id, str) and tenant_id.strip()):
            raise PrincipalRegistrationValidationError("A tenant ID is required.")
        if not (isinstance(principal_id, str) and principal_id.strip()):
            raise PrincipalRegistrationValidationError("A username is required.")
        if not roles:
            raise PrincipalRegistrationValidationError("At least one role is required.")
        if email is not None and not _EMAIL_PATTERN.match(email):
            raise PrincipalRegistrationValidationError("The email address is not valid.")
        if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LENGTH:
            raise PasswordPolicyError(f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.")

        credential_hash = self._password_hasher.hash(password)

        async def _action(session: AsyncSession) -> None:
            stmt = select(PrincipalRecord).where(
                PrincipalRecord.tenant_id == tenant_id,
                PrincipalRecord.principal_id == principal_id,
                PrincipalRecord.principal_type == PrincipalType.USER.value,
            )
            res = await session.execute(stmt)
            if res.scalar_one_or_none() is not None:
                raise PrincipalAlreadyExistsError(
                    f"A principal already exists for tenant '{tenant_id}' with username '{principal_id}'."
                )
            session.add(
                PrincipalRecord(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    principal_type=PrincipalType.USER.value,
                    enabled=True,
                    credential_hash=credential_hash,
                    roles=list(roles),
                    attributes={},
                    email=email,
                )
            )

        await self._run_in_transaction(_action)

    async def set_email(self, tenant_id: str, principal_id: str, principal_type: str, email: str) -> None:
        """Set or replace the caller's own contact email (self-service,
        used by Account settings), consulted only by the password-reset
        look-up. Raises `PrincipalRegistrationValidationError` for a
        malformed address, `AuthenticationError` (generic, matching
        `authenticate()`'s own precedent) if the principal cannot be found.
        """
        if not _EMAIL_PATTERN.match(email):
            raise PrincipalRegistrationValidationError("The email address is not valid.")

        async def _action(session: AsyncSession) -> None:
            stmt = select(PrincipalRecord).where(
                PrincipalRecord.tenant_id == tenant_id,
                PrincipalRecord.principal_id == principal_id,
                PrincipalRecord.principal_type == principal_type,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)
            record.email = email

        await self._run_in_transaction(_action)

    # -- Password Change / Reset (Phase A) ----------------------------------

    async def change_password(
        self,
        tenant_id: str,
        principal_id: str,
        principal_type: str,
        current_password: str,
        new_password: str,
    ) -> None:
        """Change a principal's own password after verifying the current one.

        Raises:
            AuthenticationError: `current_password` does not verify —
                identical, generic failure message to `authenticate()`
                (never reveals whether the principal exists vs. the
                password is simply wrong).
            PasswordPolicyError: `new_password` is shorter than
                `_MIN_PASSWORD_LENGTH`.
        """
        if not isinstance(new_password, str) or len(new_password) < _MIN_PASSWORD_LENGTH:
            raise PasswordPolicyError(f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.")

        snapshot = await self._load_principal(tenant_id, principal_id, principal_type)
        if snapshot is None or not snapshot.enabled or not snapshot.credential_hash:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)
        if not self._verify_credential(current_password, snapshot.credential_hash):
            raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)

        new_hash = self._password_hasher.hash(new_password)

        async def _action(session: AsyncSession) -> None:
            stmt = select(PrincipalRecord).where(
                PrincipalRecord.tenant_id == tenant_id,
                PrincipalRecord.principal_id == principal_id,
                PrincipalRecord.principal_type == principal_type,
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record is None:
                raise AuthenticationError(_GENERIC_AUTH_FAILURE_MESSAGE)
            record.credential_hash = new_hash

        await self._run_in_transaction(_action)

    async def request_password_reset(self, email: str) -> str | None:
        """Look up a principal by `email` and, if found, create a single-use
        reset token. Returns the **raw** token to the caller (`SecurityEngine`
        hands it to the configured `IEmailProvider`; it is never persisted
        or returned any further than that) — or `None` if no principal has
        that email on record.

        Deliberately does not raise or otherwise distinguish "no match" from
        "match found" in any externally observable way: the caller
        (`SecurityEngine.request_password_reset_capability`) must present
        the identical generic response regardless of this method's return
        value, mirroring `authenticate()`'s own enumeration-resistance
        discipline.
        """

        async def _find(session: AsyncSession) -> _PrincipalSnapshot | None:
            stmt = select(PrincipalRecord).where(PrincipalRecord.email == email, PrincipalRecord.enabled.is_(True))
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

        snapshot = cast(_PrincipalSnapshot | None, await self._run_in_transaction(_find))
        if snapshot is None:
            return None

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        issued_at = datetime.now(UTC)
        expires_at = issued_at + _RESET_TOKEN_TTL

        async def _store(session: AsyncSession) -> None:
            session.add(
                PasswordResetTokenRecord(
                    id=str(uuid.uuid4()),
                    tenant_id=snapshot.tenant_id,
                    principal_id=snapshot.principal_id,
                    principal_type=snapshot.principal_type,
                    token_hash=token_hash,
                    expires_at_utc=expires_at,
                    used_at_utc=None,
                )
            )

        await self._run_in_transaction(_store)
        return raw_token

    async def reset_password(self, token: str, new_password: str) -> None:
        """Redeem a single-use password-reset token, setting `new_password`.

        Raises:
            PasswordResetError: The token is missing/malformed/unknown,
                expired, or already used. Never distinguishes which.
            PasswordPolicyError: `new_password` is shorter than
                `_MIN_PASSWORD_LENGTH`.
        """
        if not isinstance(token, str) or not token:
            raise PasswordResetError("This password reset link is invalid or has expired.")
        if not isinstance(new_password, str) or len(new_password) < _MIN_PASSWORD_LENGTH:
            raise PasswordPolicyError(f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.")

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        new_hash = self._password_hasher.hash(new_password)

        async def _action(session: AsyncSession) -> None:
            stmt = select(PasswordResetTokenRecord).where(PasswordResetTokenRecord.token_hash == token_hash)
            res = await session.execute(stmt)
            reset_record = res.scalar_one_or_none()
            if reset_record is None or reset_record.used_at_utc is not None:
                raise PasswordResetError("This password reset link is invalid or has expired.")

            # SQLite round-trips a plain `Mapped[datetime]` as timezone-naive
            # regardless of what was written — normalize to aware-UTC before
            # comparing against `datetime.now(UTC)` (naive-vs-aware raises
            # `TypeError`, not a clean bool, mirroring the exact hazard
            # `verify_token` already guards against for `TokenPayload`).
            expires_at = reset_record.expires_at_utc
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at < datetime.now(UTC):
                raise PasswordResetError("This password reset link is invalid or has expired.")

            principal_stmt = select(PrincipalRecord).where(
                PrincipalRecord.tenant_id == reset_record.tenant_id,
                PrincipalRecord.principal_id == reset_record.principal_id,
                PrincipalRecord.principal_type == reset_record.principal_type,
            )
            principal_res = await session.execute(principal_stmt)
            principal_record = principal_res.scalar_one_or_none()
            if principal_record is None or not principal_record.enabled:
                raise PasswordResetError("This password reset link is invalid or has expired.")

            principal_record.credential_hash = new_hash
            reset_record.used_at_utc = datetime.now(UTC)

        await self._run_in_transaction(_action)

    # -- OAuth (Phase A: Google/Microsoft sign-in for an existing account) --

    @staticmethod
    def _build_oauth_state_signing_payload(state: OAuthStatePayload) -> bytes:
        parts = (
            state.nonce,
            state.provider,
            state.intent,
            state.tenant_id or "",
            state.principal_id or "",
            state.principal_type or "",
            state.issued_at_utc.isoformat(),
            state.expires_at_utc.isoformat(),
        )
        encoded = _OAUTH_STATE_DOMAIN_PREFIX
        for part in parts:
            part_bytes = part.encode("utf-8")
            encoded += len(part_bytes).to_bytes(4, "big") + part_bytes
        return encoded

    async def issue_oauth_state(
        self,
        provider: str,
        intent: str,
        tenant_id: str | None = None,
        principal_id: str | None = None,
        principal_type: str | None = None,
    ) -> OAuthStatePayload:
        """Issue a short-lived (10-minute), signed OAuth `state` payload.

        Self-contained and self-validating, exactly like `issue_token` —
        never persisted, never looked up server-side on the callback. For
        `intent="link"`, `tenant_id`/`principal_id`/`principal_type` capture
        the already-authenticated caller who initiated the flow, signed
        alongside the rest of the payload so the callback cannot be
        redirected to link a different principal than the one who started it.
        """
        issued_at_utc = datetime.now(UTC)
        state = OAuthStatePayload(
            nonce=secrets.token_urlsafe(16),
            provider=provider,
            intent=intent,
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_type=principal_type,
            issued_at_utc=issued_at_utc,
            expires_at_utc=issued_at_utc + _OAUTH_STATE_TTL,
        )
        payload_bytes = self._build_oauth_state_signing_payload(state)
        signature = self._verification_service.sign(payload_bytes, self._signing_private_key, self._signing_public_key)
        return state.model_copy(update={"signature": signature.signature})

    async def verify_oauth_state(self, state: OAuthStatePayload) -> OAuthStatePayload:
        """Verify a signed OAuth `state` payload's signature and expiry.

        Raises `OAuthStateError` (never a more specific exception) for any
        failure reason — missing signature, tampered claims, or expiry —
        matching `PasswordResetError`'s "never distinguish which" precedent.
        """
        if state.signature is None:
            raise OAuthStateError("This sign-in attempt is invalid or has expired.")

        payload_bytes = self._build_oauth_state_signing_payload(state)
        signature_model = CryptographicSignature(
            algorithm="ed25519", signature=state.signature, public_key=self._signing_public_key
        )
        try:
            self._verification_service.verify_signature_strict(payload_bytes, signature_model)
        except Exception as exc:
            raise OAuthStateError("This sign-in attempt is invalid or has expired.") from exc

        now = datetime.now(UTC)
        try:
            is_temporally_invalid = now > state.expires_at_utc or now < state.issued_at_utc
        except TypeError:
            is_temporally_invalid = True
        if is_temporally_invalid:
            raise OAuthStateError("This sign-in attempt is invalid or has expired.")

        return state

    async def resolve_oauth_link(self, provider: str, external_subject: str) -> SecurityPrincipal | None:
        """Look up the KORTEX principal linked to `(provider, external_subject)`.

        Returns `None` if no link exists or the linked principal is
        disabled/deleted — the caller (`SecurityEngine`) treats this as an
        honest "no linked account" outcome, never an error, and never
        auto-creates a principal (OAuth is a second sign-in method for an
        existing account, not a registration path).
        """

        async def _action(session: AsyncSession) -> _PrincipalSnapshot | None:
            link_stmt = select(OAuthIdentityLinkRecord).where(
                OAuthIdentityLinkRecord.provider == provider,
                OAuthIdentityLinkRecord.external_subject == external_subject,
            )
            link = (await session.execute(link_stmt)).scalar_one_or_none()
            if link is None:
                return None
            principal_stmt = select(PrincipalRecord).where(
                PrincipalRecord.tenant_id == link.tenant_id,
                PrincipalRecord.principal_id == link.principal_id,
                PrincipalRecord.principal_type == link.principal_type,
            )
            record = (await session.execute(principal_stmt)).scalar_one_or_none()
            if record is None or not record.enabled:
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

        snapshot = cast(_PrincipalSnapshot | None, await self._run_in_transaction(_action))
        if snapshot is None:
            return None
        return SecurityPrincipal(
            principal_id=snapshot.principal_id,
            principal_type=PrincipalType(snapshot.principal_type),
            tenant_id=snapshot.tenant_id,
            roles=list(snapshot.roles),
            attributes=dict(snapshot.attributes),
        )

    async def link_oauth_identity(
        self,
        tenant_id: str,
        principal_id: str,
        principal_type: str,
        provider: str,
        external_subject: str,
    ) -> None:
        """Link an external OAuth identity to an already-authenticated
        principal (self-service, from Account settings).

        Raises `OAuthLinkConflictError` if `external_subject` is already
        linked to a *different* principal — never silently reassigned.
        Replaces (rather than duplicates) an existing link this same
        principal already has for `provider`, so re-linking the identical
        provider is idempotent.
        """

        async def _action(session: AsyncSession) -> None:
            conflict_stmt = select(OAuthIdentityLinkRecord).where(
                OAuthIdentityLinkRecord.provider == provider,
                OAuthIdentityLinkRecord.external_subject == external_subject,
            )
            conflict = (await session.execute(conflict_stmt)).scalar_one_or_none()
            if conflict is not None and (
                conflict.tenant_id != tenant_id
                or conflict.principal_id != principal_id
                or conflict.principal_type != principal_type
            ):
                raise OAuthLinkConflictError(f"This {provider} account is already linked to a different KORTEX user.")
            if conflict is not None:
                return  # Already linked identically — idempotent no-op.

            own_stmt = select(OAuthIdentityLinkRecord).where(
                OAuthIdentityLinkRecord.tenant_id == tenant_id,
                OAuthIdentityLinkRecord.principal_id == principal_id,
                OAuthIdentityLinkRecord.principal_type == principal_type,
                OAuthIdentityLinkRecord.provider == provider,
            )
            own = (await session.execute(own_stmt)).scalar_one_or_none()
            if own is not None:
                await session.delete(own)
                await session.flush()

            session.add(
                OAuthIdentityLinkRecord(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    principal_type=principal_type,
                    provider=provider,
                    external_subject=external_subject,
                    linked_at=datetime.now(UTC),
                )
            )

        await self._run_in_transaction(_action)

    async def unlink_oauth_identity(
        self, tenant_id: str, principal_id: str, principal_type: str, provider: str
    ) -> bool:
        """Remove the caller's own link for `provider`, if any. Returns
        `True` if a link was removed, `False` if none existed."""

        async def _action(session: AsyncSession) -> bool:
            stmt = select(OAuthIdentityLinkRecord).where(
                OAuthIdentityLinkRecord.tenant_id == tenant_id,
                OAuthIdentityLinkRecord.principal_id == principal_id,
                OAuthIdentityLinkRecord.principal_type == principal_type,
                OAuthIdentityLinkRecord.provider == provider,
            )
            record = (await session.execute(stmt)).scalar_one_or_none()
            if record is None:
                return False
            await session.delete(record)
            return True

        return cast(bool, await self._run_in_transaction(_action))

    async def list_oauth_links(self, tenant_id: str, principal_id: str, principal_type: str) -> list[str]:
        """Return the list of provider names the caller currently has linked."""

        async def _action(session: AsyncSession) -> list[str]:
            stmt = select(OAuthIdentityLinkRecord.provider).where(
                OAuthIdentityLinkRecord.tenant_id == tenant_id,
                OAuthIdentityLinkRecord.principal_id == principal_id,
                OAuthIdentityLinkRecord.principal_type == principal_type,
            )
            rows = (await session.execute(stmt)).scalars().all()
            return list(rows)

        return cast(list[str], await self._run_in_transaction(_action))

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
            token.token_id,
            token.principal_id,
            token.principal_type.value,
            token.tenant_id,
            token.issued_at_utc,
            token.expires_at_utc,
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

        now = datetime.now(UTC)
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
