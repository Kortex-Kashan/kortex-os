"""Adversarial tests for the Security Engine M3 `AuthenticationManager`.

Covers: uniform Argon2id credential verification for USER/SERVICE_PRINCIPAL/
AGENT, enumeration resistance, Ed25519 token issuance/verification (tamper,
foreign key, expiry, future-dated), the M3 principal-revalidation re-check,
signing-key decoding/bootstrap, `derive_ed25519_public_key`, storage-failure
normalization, and credential/key non-leakage prohibitions — proving the
ratified M3 architecture decisions, not merely exercising code paths.

Tenant identifiers are derived from `tmp_path.name` (unique per test, per
pytest's own fixture guarantee) rather than fixed literals — `Kernel()`
defaults to a single shared, non-test-scoped SQLite file
(`sqlite+aiosqlite:///./kortex_local.db`, gitignored) rather than an
isolated per-test database, so rows persist across test functions and even
across separate pytest invocations within the same working tree. Reusing a
fixed tenant/principal pair across multiple test functions would collide
with `PrincipalRecord`'s `(tenant_id, principal_id, principal_type)` unique
constraint the moment the suite is run more than once.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, NoReturn

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.kernel import Kernel
from kortex.engines.security.auth import AuthenticationManager
from kortex.engines.security.exceptions import (
    AuthenticationError,
    CryptoProviderError,
    InvalidSignatureError,
    InvalidTokenError,
    SecurityEngineError,
    SigningKeyError,
    TokenExpiredError,
)
from kortex.engines.security.models import PrincipalRecord, PrincipalType, SecurityPrincipal, TokenPayload
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.storage.engine import StorageEngine

_TEST_SIGNING_KEY = b"\x33" * 32
_OTHER_SIGNING_KEY = b"\x44" * 32

_PRINCIPAL_TYPES_AND_FIELDS = [
    (PrincipalType.USER.value, "password"),
    (PrincipalType.SERVICE_PRINCIPAL.value, "credential"),
    (PrincipalType.AGENT.value, "credential"),
]


def _tenant_a(tmp_path: Path) -> str:
    return f"tenant-a-{tmp_path.name}"


def _tenant_b(tmp_path: Path) -> str:
    return f"tenant-b-{tmp_path.name}"


async def _make_manager(
    tmp_path: Path, signing_key: bytes = _TEST_SIGNING_KEY
) -> tuple[Kernel, StorageEngine, AuthenticationManager]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "auth_manager_test"))
    kernel.register_engine(storage_engine)
    await storage_engine.initialize(kernel)
    await storage_engine.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()
    manager = AuthenticationManager(
        data_store=storage_engine.data, crypto_provider=LocalCrypto(), signing_private_key=signing_key
    )
    return kernel, storage_engine, manager


async def _seed_principal(
    data_store: Any,
    tenant_id: str,
    principal_id: str,
    principal_type: str,
    credential: str | None,
    enabled: bool = True,
    roles: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Insert a `PrincipalRecord` directly via `IDataStore` — M3 has no
    provisioning capability, so tests seed exactly as `test_secret_store.py`
    seeds `SecretRecord` rows directly."""
    credential_hash = PasswordHasher().hash(credential) if credential is not None else None

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type=principal_type,
                enabled=enabled,
                credential_hash=credential_hash,
                roles=roles or [],
                attributes=attributes or {},
            )
        )
        await session.flush()

    await data_store.execute_in_transaction(_action)


def _sign_custom_token(
    manager: AuthenticationManager,
    principal: SecurityPrincipal,
    issued_at_utc: datetime,
    expires_at_utc: datetime,
    token_id: str = "custom-token-id",
) -> TokenPayload:
    """Build a validly-signed `TokenPayload` with caller-chosen timestamps —
    used to test expiry/future-dating without waiting on a real clock."""
    payload_bytes = manager._build_signing_payload(
        token_id, principal.principal_id, principal.principal_type.value, principal.tenant_id,
        issued_at_utc, expires_at_utc,
    )
    signature = manager._verification_service.sign(
        payload_bytes, manager._signing_private_key, manager._signing_public_key
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


class _FailingDataStore:
    """Fake IDataStore whose transactions always fail — proves storage
    failures normalize to `SecurityEngineError` rather than a silent denial
    or a silent success."""

    async def get_session(self) -> Any:  # pragma: no cover - not exercised
        raise NotImplementedError

    async def execute_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> NoReturn:
        raise RuntimeError("simulated storage outage")


# -- Credential flows: USER / SERVICE_PRINCIPAL / AGENT, uniform mechanism -------


@pytest.mark.asyncio
@pytest.mark.parametrize(("principal_type", "field"), _PRINCIPAL_TYPES_AND_FIELDS)
async def test_authenticate_succeeds_for_valid_credential(
    tmp_path: Path, principal_type: str, field: str
) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(
        storage.data, tenant_a, "principal-1", principal_type, "correct-secret",
        roles=["role-a"], attributes={"env": "prod"},
    )

    credentials = {
        "principal_type": principal_type, "tenant_id": tenant_a, "principal_id": "principal-1", field: "correct-secret"
    }
    principal = await manager.authenticate(credentials)

    assert isinstance(principal, SecurityPrincipal)
    assert principal.principal_id == "principal-1"
    assert principal.principal_type.value == principal_type
    assert principal.tenant_id == tenant_a
    assert principal.roles == ["role-a"]
    assert principal.attributes == {"env": "prod"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("principal_type", "field"), _PRINCIPAL_TYPES_AND_FIELDS)
async def test_authenticate_wrong_credential_denied(tmp_path: Path, principal_type: str, field: str) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", principal_type, "correct-secret")

    with pytest.raises(AuthenticationError):
        await manager.authenticate(
            {
                "principal_type": principal_type,
                "tenant_id": tenant_a,
                "principal_id": "principal-1",
                field: "wrong-secret",
            }
        )


@pytest.mark.asyncio
async def test_authenticate_unknown_principal_denied_with_identical_message_to_wrong_credential(
    tmp_path: Path,
) -> None:
    """Enumeration resistance: unknown principal and wrong credential must be
    indistinguishable to the caller."""
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "correct-secret")

    with pytest.raises(AuthenticationError) as unknown_exc:
        await manager.authenticate(
            {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "does-not-exist", "password": "anything"}
        )
    with pytest.raises(AuthenticationError) as wrong_exc:
        await manager.authenticate(
            {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "wrong"}
        )

    assert type(unknown_exc.value) is type(wrong_exc.value)
    assert str(unknown_exc.value) == str(wrong_exc.value)


@pytest.mark.asyncio
async def test_authenticate_disabled_principal_denied_with_identical_message(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "disabled-user", "USER", "correct-secret", enabled=False)

    with pytest.raises(AuthenticationError) as disabled_exc:
        await manager.authenticate(
            {
                "principal_type": "USER",
                "tenant_id": tenant_a,
                "principal_id": "disabled-user",
                "password": "correct-secret",
            }
        )
    with pytest.raises(AuthenticationError) as wrong_exc:
        await manager.authenticate(
            {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "disabled-user", "password": "wrong"}
        )

    assert str(disabled_exc.value) == str(wrong_exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credentials_factory",
    [
        lambda t: {},
        lambda t: {"principal_type": "USER"},
        lambda t: {"principal_type": "USER", "tenant_id": t},
        lambda t: {"principal_type": "USER", "tenant_id": t, "principal_id": "x"},
        lambda t: {"principal_type": "USER", "tenant_id": "", "principal_id": "x", "password": "y"},
        lambda t: {"principal_type": "USER", "tenant_id": t, "principal_id": "", "password": "y"},
        lambda t: {"principal_type": "USER", "tenant_id": t, "principal_id": "x", "password": ""},
        lambda t: {"principal_type": "NOT_A_REAL_TYPE", "tenant_id": t, "principal_id": "x", "password": "y"},
        lambda t: {"principal_type": None, "tenant_id": t, "principal_id": "x", "password": "y"},
        lambda t: "not-a-dict",
        lambda t: None,
    ],
)
async def test_authenticate_malformed_input_denied_for_every_shape(
    tmp_path: Path, credentials_factory: Callable[[str], Any]
) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)

    with pytest.raises(AuthenticationError):
        await manager.authenticate(credentials_factory(_tenant_a(tmp_path)))


@pytest.mark.asyncio
async def test_authenticate_cross_tenant_lookup_denied(tmp_path: Path) -> None:
    """Right principal_id, wrong tenant_id — must be denied, not found under a different tenant."""
    tenant_a, tenant_b = _tenant_a(tmp_path), _tenant_b(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "correct-secret")

    with pytest.raises(AuthenticationError):
        await manager.authenticate(
            {
                "principal_type": "USER",
                "tenant_id": tenant_b,
                "principal_id": "principal-1",
                "password": "correct-secret",
            }
        )


@pytest.mark.asyncio
async def test_authenticate_principal_with_no_credential_hash_denied(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "no-cred", "USER", credential=None)

    with pytest.raises(AuthenticationError):
        await manager.authenticate(
            {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "no-cred", "password": "anything"}
        )


@pytest.mark.asyncio
async def test_authenticate_does_not_evaluate_roles_or_authorization(tmp_path: Path) -> None:
    """Authentication establishes identity only — `roles` are carried through
    verbatim, never interpreted, filtered, or used to grant/deny anything."""
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "no-roles", "USER", "secret", roles=[], attributes={})

    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "no-roles", "password": "secret"}
    )
    assert principal.roles == []  # authenticate succeeds regardless of empty roles


# -- Token issuance -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_token_produces_well_formed_signed_token(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "secret")
    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "secret"}
    )

    token = await manager.issue_token(principal)

    assert isinstance(token, TokenPayload)
    assert token.signature is not None
    assert token.principal_id == "principal-1"
    assert token.tenant_id == tenant_a
    assert token.expires_at_utc > token.issued_at_utc


@pytest.mark.asyncio
async def test_issue_token_produces_distinct_ids_and_signatures_each_call(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    principal = SecurityPrincipal(principal_id="p1", principal_type=PrincipalType.USER, tenant_id=_tenant_a(tmp_path))

    token_1 = await manager.issue_token(principal)
    token_2 = await manager.issue_token(principal)

    assert token_1.token_id != token_2.token_id
    assert token_1.signature != token_2.signature


# -- Token verification --------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_token_round_trip_returns_correct_principal(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "secret", roles=["admin"])
    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "secret"}
    )
    token = await manager.issue_token(principal)

    verified = await manager.verify_token(token)

    assert verified.principal_id == "principal-1"
    assert verified.roles == ["admin"]


@pytest.mark.asyncio
async def test_verify_token_with_no_signature_denied(tmp_path: Path) -> None:
    _kernel, _storage, manager = await _make_manager(tmp_path)
    now = datetime.now(timezone.utc)
    token = TokenPayload(
        token_id="t1", principal_id="p1", principal_type=PrincipalType.USER, tenant_id=_tenant_a(tmp_path),
        issued_at_utc=now, expires_at_utc=now + timedelta(minutes=15), signature=None,
    )

    with pytest.raises(InvalidTokenError):
        await manager.verify_token(token)


@pytest.mark.asyncio
async def test_verify_token_tampered_claim_fails_signature_check(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "secret")
    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "secret"}
    )
    token = await manager.issue_token(principal)
    tampered = token.model_copy(update={"principal_id": "principal-1-attacker"})

    with pytest.raises(InvalidSignatureError):
        await manager.verify_token(tampered)


@pytest.mark.asyncio
async def test_verify_token_tampered_signature_fails(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "secret")
    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "secret"}
    )
    token = await manager.issue_token(principal)
    assert token.signature is not None
    tampered_signature = bytes([token.signature[0] ^ 0xFF]) + token.signature[1:]
    tampered = token.model_copy(update={"signature": tampered_signature})

    with pytest.raises(InvalidSignatureError):
        await manager.verify_token(tampered)


@pytest.mark.asyncio
async def test_verify_token_signed_by_foreign_key_fails(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager_a = await _make_manager(tmp_path, signing_key=_TEST_SIGNING_KEY)
    _kernel2, _storage2, manager_b = await _make_manager(tmp_path, signing_key=_OTHER_SIGNING_KEY)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "secret")
    principal = await manager_a.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "secret"}
    )
    token_signed_by_a = await manager_a.issue_token(principal)

    with pytest.raises(InvalidSignatureError):
        await manager_b.verify_token(token_signed_by_a)


@pytest.mark.asyncio
async def test_verify_token_expired_denied(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "secret")
    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "secret"}
    )
    now = datetime.now(timezone.utc)
    expired_token = _sign_custom_token(
        manager, principal, issued_at_utc=now - timedelta(hours=1), expires_at_utc=now - timedelta(minutes=1)
    )

    with pytest.raises(TokenExpiredError):
        await manager.verify_token(expired_token)


@pytest.mark.asyncio
async def test_verify_token_future_dated_denied(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "secret")
    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "secret"}
    )
    now = datetime.now(timezone.utc)
    future_token = _sign_custom_token(
        manager, principal, issued_at_utc=now + timedelta(hours=1), expires_at_utc=now + timedelta(hours=2)
    )

    with pytest.raises(TokenExpiredError):
        await manager.verify_token(future_token)


@pytest.mark.asyncio
async def test_verify_token_disabled_principal_between_issuance_and_verification_denied(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "secret")
    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "secret"}
    )
    token = await manager.issue_token(principal)

    async def _disable(session: AsyncSession) -> None:
        from sqlalchemy import select

        res = await session.execute(
            select(PrincipalRecord).where(
                PrincipalRecord.tenant_id == tenant_a, PrincipalRecord.principal_id == "principal-1"
            )
        )
        record = res.scalar_one()
        record.enabled = False

    await storage.data.execute_in_transaction(_disable)

    with pytest.raises(InvalidTokenError):
        await manager.verify_token(token)


@pytest.mark.asyncio
async def test_verify_token_deleted_principal_between_issuance_and_verification_denied(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "secret")
    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "secret"}
    )
    token = await manager.issue_token(principal)

    async def _delete(session: AsyncSession) -> None:
        from sqlalchemy import select

        res = await session.execute(
            select(PrincipalRecord).where(
                PrincipalRecord.tenant_id == tenant_a, PrincipalRecord.principal_id == "principal-1"
            )
        )
        await session.delete(res.scalar_one())

    await storage.data.execute_in_transaction(_delete)

    with pytest.raises(InvalidTokenError):
        await manager.verify_token(token)


# -- Signing-key bootstrap (KORTEX_AUTH_SIGNING_PRIVATE_KEY contract) -------------


def test_decode_signing_key_accepts_valid_hex() -> None:
    hex_key = _TEST_SIGNING_KEY.hex()
    assert AuthenticationManager.decode_signing_key(hex_key) == _TEST_SIGNING_KEY


def test_decode_signing_key_accepts_valid_base64() -> None:
    b64_key = base64.b64encode(_TEST_SIGNING_KEY).decode("ascii")
    assert AuthenticationManager.decode_signing_key(b64_key) == _TEST_SIGNING_KEY


def test_decode_signing_key_rejects_missing() -> None:
    for bad in (None, "", "   "):
        with pytest.raises(SigningKeyError):
            AuthenticationManager.decode_signing_key(bad)  # type: ignore[arg-type]


def test_decode_signing_key_rejects_wrong_length_hex() -> None:
    with pytest.raises(SigningKeyError):
        AuthenticationManager.decode_signing_key("aa" * 16)


def test_decode_signing_key_rejects_64_char_string_that_is_not_valid_hex() -> None:
    """Exactly 64 characters (the hex-length branch) but containing a
    non-hexadecimal character — distinct from the wrong-length case above."""
    not_hex_but_right_length = "g" * 64
    with pytest.raises(SigningKeyError):
        AuthenticationManager.decode_signing_key(not_hex_but_right_length)


def test_decode_signing_key_rejects_garbage_string() -> None:
    with pytest.raises(SigningKeyError):
        AuthenticationManager.decode_signing_key("not-hex-and-not-base64-!!!")


def test_decode_signing_key_error_never_exposes_key_material() -> None:
    with pytest.raises(SigningKeyError) as exc_info:
        AuthenticationManager.decode_signing_key("not-a-valid-key-encoding")
    assert "not-a-valid-key-encoding" not in str(exc_info.value)


def test_authentication_manager_constructor_rejects_wrong_length_signing_key() -> None:
    with pytest.raises(SigningKeyError):
        AuthenticationManager(
            data_store=_FailingDataStore(), crypto_provider=LocalCrypto(), signing_private_key=b"\x00" * 16
        )


def test_authentication_manager_constructor_error_never_exposes_key_material() -> None:
    marker_key = b"\xde\xad\xbe\xef"
    with pytest.raises(SigningKeyError) as exc_info:
        AuthenticationManager(
            data_store=_FailingDataStore(), crypto_provider=LocalCrypto(), signing_private_key=marker_key
        )
    assert marker_key.hex() not in str(exc_info.value)


# -- derive_ed25519_public_key (ICryptoProvider extension, authorized for M3) -----


def test_derive_ed25519_public_key_matches_generated_keypair() -> None:
    crypto = LocalCrypto()
    private_key, public_key = crypto.generate_ed25519_keypair()

    assert crypto.derive_ed25519_public_key(private_key) == public_key


def test_derive_ed25519_public_key_rejects_malformed_private_key() -> None:
    crypto = LocalCrypto()
    with pytest.raises(CryptoProviderError):
        crypto.derive_ed25519_public_key(b"\x00" * 16)  # wrong length


def test_derive_ed25519_public_key_rejects_non_bytes_input() -> None:
    crypto = LocalCrypto()
    with pytest.raises(CryptoProviderError):
        crypto.derive_ed25519_public_key("not-bytes")  # type: ignore[arg-type]


# -- Storage failure normalization -------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_storage_failure_raises_security_engine_error_not_generic_auth_denial(
    tmp_path: Path,
) -> None:
    manager = AuthenticationManager(
        data_store=_FailingDataStore(), crypto_provider=LocalCrypto(), signing_private_key=_TEST_SIGNING_KEY
    )

    with pytest.raises(SecurityEngineError) as exc_info:
        await manager.authenticate(
            {"principal_type": "USER", "tenant_id": _tenant_a(tmp_path), "principal_id": "x", "password": "y"}
        )
    assert not isinstance(exc_info.value, AuthenticationError)


@pytest.mark.asyncio
async def test_authenticate_unknown_principal_type_never_reaches_storage(tmp_path: Path) -> None:
    """An unrecognized `principal_type` is rejected before any `IDataStore`
    lookup is attempted — proven by using a data store that would raise if
    ever actually invoked."""
    manager = AuthenticationManager(
        data_store=_FailingDataStore(), crypto_provider=LocalCrypto(), signing_private_key=_TEST_SIGNING_KEY
    )

    with pytest.raises(AuthenticationError):
        await manager.authenticate(
            {
                "principal_type": "NOT_A_REAL_TYPE",
                "tenant_id": _tenant_a(tmp_path),
                "principal_id": "x",
                "password": "y",
            }
        )


@pytest.mark.asyncio
async def test_authenticate_propagates_security_engine_error_raised_directly_by_storage_action(
    tmp_path: Path,
) -> None:
    """`_run_in_transaction`'s `except SecurityEngineError: raise` passthrough
    — a `SecurityEngineError` raised by the transaction body itself (as
    opposed to a generic storage-layer exception) must propagate unchanged,
    never re-wrapped."""

    class _RaisingSecurityErrorDataStore:
        async def get_session(self) -> Any:  # pragma: no cover - not exercised
            raise NotImplementedError

        async def execute_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> NoReturn:
            raise SigningKeyError("marker-error-from-transaction-body")

    manager = AuthenticationManager(
        data_store=_RaisingSecurityErrorDataStore(),
        crypto_provider=LocalCrypto(),
        signing_private_key=_TEST_SIGNING_KEY,
    )

    with pytest.raises(SigningKeyError, match="marker-error-from-transaction-body"):
        await manager.authenticate(
            {"principal_type": "USER", "tenant_id": _tenant_a(tmp_path), "principal_id": "x", "password": "y"}
        )


@pytest.mark.asyncio
async def test_verify_token_naive_datetime_comparison_fails_closed(tmp_path: Path) -> None:
    """Defensive fallback: if a token's timestamps are ever naive (no tzinfo)
    rather than the aware datetimes this module always produces, comparison
    against a fresh aware `now` raises `TypeError` internally — this must be
    treated as invalid, never as valid."""
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "secret")
    principal = await manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "secret"}
    )
    naive_now = datetime.now()  # noqa: DTZ005 -- deliberately naive, this is what's under test
    naive_token = _sign_custom_token(
        manager, principal, issued_at_utc=naive_now, expires_at_utc=naive_now + timedelta(minutes=15)
    )

    with pytest.raises(TokenExpiredError):
        await manager.verify_token(naive_token)


@pytest.mark.asyncio
async def test_verify_token_storage_failure_raises_security_engine_error(tmp_path: Path) -> None:
    manager = AuthenticationManager(
        data_store=_FailingDataStore(), crypto_provider=LocalCrypto(), signing_private_key=_TEST_SIGNING_KEY
    )
    principal = SecurityPrincipal(principal_id="p1", principal_type=PrincipalType.USER, tenant_id=_tenant_a(tmp_path))
    token = await manager.issue_token(principal)  # issuance never touches IDataStore

    with pytest.raises(SecurityEngineError) as exc_info:
        await manager.verify_token(token)
    assert not isinstance(exc_info.value, InvalidTokenError)


# -- Credential / key non-leakage prohibitions -------------------------------------


@pytest.mark.asyncio
async def test_authentication_error_never_contains_presented_credential(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "correct-secret")

    with pytest.raises(AuthenticationError) as exc_info:
        await manager.authenticate(
            {
                "principal_type": "USER",
                "tenant_id": tenant_a,
                "principal_id": "principal-1",
                "password": "extremely-sensitive-wrong-password",
            }
        )

    assert "extremely-sensitive-wrong-password" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_stored_credential_hash_never_appears_in_any_exception(tmp_path: Path) -> None:
    tenant_a = _tenant_a(tmp_path)
    _kernel, storage, manager = await _make_manager(tmp_path)
    await _seed_principal(storage.data, tenant_a, "principal-1", "USER", "correct-secret")

    with pytest.raises(AuthenticationError) as exc_info:
        await manager.authenticate(
            {"principal_type": "USER", "tenant_id": tenant_a, "principal_id": "principal-1", "password": "wrong"}
        )

    # Argon2id hashes are always prefixed "$argon2id$" — confirms no stored
    # hash value leaked into the exception message.
    assert "$argon2id$" not in str(exc_info.value)


def test_signing_private_key_never_appears_in_any_constructor_exception() -> None:
    for bad_length in (0, 16, 31, 33, 64):
        bad_key = bytes(range(bad_length % 256)) if bad_length else b""
        with pytest.raises(SigningKeyError) as exc_info:
            AuthenticationManager(
                data_store=_FailingDataStore(), crypto_provider=LocalCrypto(), signing_private_key=bad_key
            )
        assert bad_key.hex() not in str(exc_info.value) or bad_key == b""
