"""Adversarial tests for Phase 5 agent enrollment.

Covers spec S6-S10 and adversarial matrix tests 13-22 and 24: token custody,
the four-state enrollment state machine, transactional issuance, deterministic
idempotency, the concurrency race, duplicate machine ids, and crash recovery
on both sides of the commit boundary.

Everything here runs against a real SQLite database and the real PKI. The
crash tests induce failure by making a real operation fail inside the real
transaction, then assert on what the database actually committed — not on a
mock's call log.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from kortex.engines.security.agent_enrollment import (
    AgentEnrollmentService,
    EnrollmentConflictError,
    EnrollmentInvariantError,
    EnrollmentRejectedError,
    EnrollmentState,
    enrollment_state,
    hash_token,
)
from kortex.engines.security.models import (
    AgentEnrollmentTokenRecord,
    PrincipalRecord,
    PrincipalType,
)
from tests.security.conftest import Phase5Stack

_TENANT = "tenant-alpha"


def _new_machine_id() -> str:
    return str(uuid.uuid4())


def _make_csr(common_name: str = "agent") -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM)


async def _service(stack: Phase5Stack) -> AgentEnrollmentService:
    await stack.pki.initialize_ca(certificate_path=stack.tmp_path / "certs" / "ca.crt")
    return AgentEnrollmentService(data_store=stack.storage_engine.data, pki=stack.pki)


async def _token_record(stack: Phase5Stack, raw_token: str) -> AgentEnrollmentTokenRecord:
    async def _action(session):  # type: ignore[no-untyped-def]
        result = await session.execute(
            select(AgentEnrollmentTokenRecord).where(AgentEnrollmentTokenRecord.token_hash == hash_token(raw_token))
        )
        return result.scalar_one()

    return await stack.storage_engine.data.execute_in_transaction(_action)


async def _principals(stack: Phase5Stack) -> list[PrincipalRecord]:
    async def _action(session):  # type: ignore[no-untyped-def]
        result = await session.execute(select(PrincipalRecord))
        return list(result.scalars().all())

    return await stack.storage_engine.data.execute_in_transaction(_action)


# -- Test 13: SHA-256 token storage ------------------------------------------


async def test_enrollment_token_sha256_storage(phase5_stack: Phase5Stack) -> None:
    """TEST 13: only the SHA-256 digest of the token is persisted."""
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)

    # The raw token is a 64-char lowercase hex string (32 random bytes).
    assert len(raw_token) == 64
    assert raw_token == raw_token.lower()
    bytes.fromhex(raw_token)

    record = await _token_record(phase5_stack, raw_token)
    assert record.token_hash == hash_token(raw_token)
    assert record.token_hash != raw_token

    # No column anywhere on the row holds the raw token.
    for column in AgentEnrollmentTokenRecord.__table__.columns:
        value = getattr(record, column.name)
        assert raw_token not in str(value), f"raw token found in column {column.name!r}"

    # 24-hour validity.
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    assert timedelta(hours=23) < (expires_at - datetime.now(UTC)) <= timedelta(hours=24)


async def test_raw_token_is_absent_from_the_entire_database_file(phase5_stack: Phase5Stack) -> None:
    """The raw token must not appear anywhere in the on-disk database."""
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    await service.enroll(raw_token, _make_csr(), _new_machine_id())

    await phase5_stack.kernel.db.disconnect()
    found = False
    for path in phase5_stack.tmp_path.rglob("*"):
        if path.is_file() and raw_token.encode("ascii") in path.read_bytes():
            found = True
    assert not found, "raw enrollment token found on disk"


# -- Test 14: raw token never logged -----------------------------------------


async def test_enrollment_token_raw_never_logged(phase5_stack: Phase5Stack, caplog: pytest.LogCaptureFixture) -> None:
    """TEST 14: no log record emitted during enrollment contains the token."""
    service = await _service(phase5_stack)

    with caplog.at_level(logging.DEBUG):
        raw_token = await service.create_enrollment_token(_TENANT)
        await service.enroll(raw_token, _make_csr(), _new_machine_id())

        # Also exercise every rejection path, since error paths are where
        # secrets most often leak into messages.
        with pytest.raises(EnrollmentRejectedError):
            await service.enroll("f" * 64, _make_csr(), _new_machine_id())
        with pytest.raises(EnrollmentRejectedError):
            await service.enroll(raw_token, _make_csr(), _new_machine_id())

    captured = "\n".join(
        [record.getMessage() for record in caplog.records]
        + [str(record.args) for record in caplog.records]
        + [caplog.text]
    )
    assert raw_token not in captured


async def test_non_ascii_token_is_refused_uniformly(phase5_stack: Phase5Stack) -> None:
    """A non-ASCII token is refused as a credential, not raised as an error.

    Guards the uniform-rejection property: every bad token must produce the
    same 403-shaped outcome, so a prober cannot distinguish "malformed" from
    "unknown" from "already used".
    """
    service = await _service(phase5_stack)

    for bad_token in ("tökén" * 10, "‮" * 64, "🔑" * 16):
        with pytest.raises(EnrollmentRejectedError):
            await service.enroll(bad_token, _make_csr(), _new_machine_id())

    assert await _principals(phase5_stack) == []


async def test_rejection_errors_never_contain_the_token(phase5_stack: Phase5Stack) -> None:
    """Exception messages must not carry the token either."""
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)

    with pytest.raises(EnrollmentRejectedError) as excinfo:
        await service.enroll("a" * 64, _make_csr(), _new_machine_id())
    assert "a" * 64 not in str(excinfo.value)
    assert raw_token not in str(excinfo.value)


# -- Test 15: expiration ------------------------------------------------------


async def test_enrollment_token_expiration(phase5_stack: Phase5Stack) -> None:
    """TEST 15: a token past its 24-hour window is refused for new enrollment."""
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)

    async def _expire(session):  # type: ignore[no-untyped-def]
        result = await session.execute(
            select(AgentEnrollmentTokenRecord).where(AgentEnrollmentTokenRecord.token_hash == hash_token(raw_token))
        )
        record = result.scalar_one()
        record.expires_at = datetime.now(UTC) - timedelta(minutes=1)

    await phase5_stack.storage_engine.data.execute_in_transaction(_expire)

    with pytest.raises(EnrollmentRejectedError):
        await service.enroll(raw_token, _make_csr(), _new_machine_id())

    # And nothing was created as a side effect.
    assert await _principals(phase5_stack) == []


async def test_unknown_token_is_refused(phase5_stack: Phase5Stack) -> None:
    """A token that was never issued is refused, creating nothing."""
    service = await _service(phase5_stack)
    with pytest.raises(EnrollmentRejectedError):
        await service.enroll("0" * 64, _make_csr(), _new_machine_id())
    assert await _principals(phase5_stack) == []


# -- Test 16: single use ------------------------------------------------------


async def test_enrollment_token_single_use(phase5_stack: Phase5Stack) -> None:
    """TEST 16: a second enrollment with a DIFFERENT CSR is refused."""
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    machine_id = _new_machine_id()

    first_certificate = await service.enroll(raw_token, _make_csr(), machine_id)
    assert b"BEGIN CERTIFICATE" in first_certificate

    with pytest.raises(EnrollmentRejectedError):
        await service.enroll(raw_token, _make_csr("different-key"), machine_id)

    # Exactly one principal, exactly one issued certificate.
    principals = await _principals(phase5_stack)
    assert len(principals) == 1
    record = await _token_record(phase5_stack, raw_token)
    assert record.issued_certificate == first_certificate


async def test_enrollment_creates_an_agent_principal_bound_to_the_token_tenant(
    phase5_stack: Phase5Stack,
) -> None:
    """The principal is AGENT, enabled, and takes its tenant from the token."""
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    machine_id = _new_machine_id()

    certificate_pem = await service.enroll(raw_token, _make_csr(), machine_id)

    principals = await _principals(phase5_stack)
    assert len(principals) == 1
    principal = principals[0]
    assert principal.principal_type == PrincipalType.AGENT.value
    assert principal.enabled is True
    assert principal.tenant_id == _TENANT
    assert principal.machine_installation_id == machine_id
    assert principal.roles == ["agent"]

    # The certificate's CN is the backend-generated principal_id.
    certificate = x509.load_pem_x509_certificate(certificate_pem)
    assert certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == principal.principal_id


# -- Test 17: idempotent retry ------------------------------------------------


async def test_enrollment_idempotent_retry(phase5_stack: Phase5Stack) -> None:
    """TEST 17: identical (token, CSR, machine id) replays the same certificate."""
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    csr = _make_csr()
    machine_id = _new_machine_id()

    first = await service.enroll(raw_token, csr, machine_id)
    second = await service.enroll(raw_token, csr, machine_id)
    third = await service.enroll(raw_token, csr, machine_id)

    # Byte-for-byte identical, not merely "also valid".
    assert first == second == third

    # And still exactly one principal.
    assert len(await _principals(phase5_stack)) == 1


async def test_completed_enrollment_replays_even_after_the_token_expires(
    phase5_stack: Phase5Stack,
) -> None:
    """Expiration must NOT apply to the completed-enrollment recovery path.

    A crash shortly before the token's 24 hours elapse must not destroy an
    identity that was already issued.
    """
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    csr = _make_csr()
    machine_id = _new_machine_id()

    first = await service.enroll(raw_token, csr, machine_id)

    async def _expire(session):  # type: ignore[no-untyped-def]
        result = await session.execute(
            select(AgentEnrollmentTokenRecord).where(AgentEnrollmentTokenRecord.token_hash == hash_token(raw_token))
        )
        result.scalar_one().expires_at = datetime.now(UTC) - timedelta(days=30)

    await phase5_stack.storage_engine.data.execute_in_transaction(_expire)

    assert await service.enroll(raw_token, csr, machine_id) == first


# -- Test 18: same token + same CSR + different machine id --------------------


async def test_enrollment_same_token_same_csr_different_machine_id_rejected(
    phase5_stack: Phase5Stack,
) -> None:
    """TEST 18: a replay from a different machine is refused (403)."""
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    csr = _make_csr()

    await service.enroll(raw_token, csr, _new_machine_id())

    with pytest.raises(EnrollmentRejectedError):
        await service.enroll(raw_token, csr, _new_machine_id())

    assert len(await _principals(phase5_stack)) == 1


# -- Test 19: concurrent race -------------------------------------------------


async def test_enrollment_concurrent_race(phase5_stack: Phase5Stack) -> None:
    """TEST 19: 10 concurrent identical requests yield exactly 1 principal.

    All ten present the same token, CSR and machine id, so every one of them
    is entitled to a certificate — but only one identity may exist. The
    surviving guarantee is: one `PrincipalRecord`, and one certificate value
    returned to all ten.
    """
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    csr = _make_csr()
    machine_id = _new_machine_id()

    results = await asyncio.gather(
        *[service.enroll(raw_token, csr, machine_id) for _ in range(10)],
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, bytes)]
    failures = [r for r in results if not isinstance(r, bytes)]

    assert not failures, f"no request should fail outright, got: {failures}"
    assert len(successes) == 10
    # Every caller received the same certificate.
    assert len(set(successes)) == 1

    principals = await _principals(phase5_stack)
    assert len(principals) == 1, f"expected exactly one principal, found {len(principals)}"


async def test_concurrent_enrollments_from_different_machines_on_one_token(
    phase5_stack: Phase5Stack,
) -> None:
    """Concurrent requests from DIFFERENT machines: exactly one wins.

    This is the token-theft race — one token, two machines. The loser must be
    refused rather than receiving a second identity.
    """
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    csr = _make_csr()

    results = await asyncio.gather(
        *[service.enroll(raw_token, csr, _new_machine_id()) for _ in range(5)],
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, bytes)]
    assert len(successes) == 1, "exactly one machine may win the token"
    assert len(await _principals(phase5_stack)) == 1


# -- Test 20: duplicate machine id --------------------------------------------


async def test_enrollment_duplicate_machine_id(phase5_stack: Phase5Stack) -> None:
    """TEST 20: the same machine with a NEW token is refused with a conflict."""
    service = await _service(phase5_stack)
    machine_id = _new_machine_id()

    first_token = await service.create_enrollment_token(_TENANT)
    await service.enroll(first_token, _make_csr(), machine_id)

    second_token = await service.create_enrollment_token(_TENANT)
    with pytest.raises(EnrollmentConflictError):
        await service.enroll(second_token, _make_csr(), machine_id)

    assert len(await _principals(phase5_stack)) == 1


async def test_duplicate_machine_id_is_refused_across_tenants(phase5_stack: Phase5Stack) -> None:
    """One installation cannot be enrolled into a second tenant.

    `machine_installation_id` is globally unique, not per-tenant, so this is
    refused by the database rather than by a tenant-scoped application check.
    """
    service = await _service(phase5_stack)
    machine_id = _new_machine_id()

    token_a = await service.create_enrollment_token("tenant-alpha")
    await service.enroll(token_a, _make_csr(), machine_id)

    token_b = await service.create_enrollment_token("tenant-beta")
    with pytest.raises(EnrollmentConflictError):
        await service.enroll(token_b, _make_csr(), machine_id)

    principals = await _principals(phase5_stack)
    assert len(principals) == 1
    assert principals[0].tenant_id == "tenant-alpha"


async def test_machine_id_uniqueness_is_enforced_by_the_database(phase5_stack: Phase5Stack) -> None:
    """The constraint is real DDL, not merely an application pre-check.

    Inserts a second principal with a duplicate machine id directly through
    the data store, bypassing the enrollment service entirely.
    """
    from sqlalchemy.exc import IntegrityError

    service = await _service(phase5_stack)
    machine_id = _new_machine_id()
    token = await service.create_enrollment_token(_TENANT)
    await service.enroll(token, _make_csr(), machine_id)

    async def _insert_duplicate(session):  # type: ignore[no-untyped-def]
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id="tenant-beta",
                principal_id=str(uuid.uuid4()),
                principal_type=PrincipalType.AGENT.value,
                enabled=True,
                machine_installation_id=machine_id,
                roles=["agent"],
                attributes={},
            )
        )
        await session.flush()

    with pytest.raises(IntegrityError):
        await phase5_stack.storage_engine.data.execute_in_transaction(_insert_duplicate)


# -- Test 21: crash before commit ---------------------------------------------


async def test_enrollment_crash_before_commit(phase5_stack: Phase5Stack) -> None:
    """TEST 21: a crash during signing rolls back and leaves the token usable.

    Failure is injected into the real CSR-signing call inside the real
    transaction — the same place a genuine PKI fault would occur.
    """
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    csr = _make_csr()
    machine_id = _new_machine_id()

    original_sign = phase5_stack.pki.sign_client_csr_with_material

    def _exploding_sign(material, csr_bytes: bytes, principal_id: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated crash during CSR signing")

    phase5_stack.pki.sign_client_csr_with_material = _exploding_sign  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await service.enroll(raw_token, csr, machine_id)
    phase5_stack.pki.sign_client_csr_with_material = original_sign  # type: ignore[method-assign]

    # Rolled back: no orphaned principal.
    assert await _principals(phase5_stack) == []

    # The token is untouched and still available.
    record = await _token_record(phase5_stack, raw_token)
    assert enrollment_state(record) is EnrollmentState.TOKEN_AVAILABLE
    assert record.consumed_at is None
    assert record.issued_certificate is None
    assert record.enrollment_principal_id is None

    # And the retry succeeds normally.
    certificate = await service.enroll(raw_token, csr, machine_id)
    assert b"BEGIN CERTIFICATE" in certificate
    assert len(await _principals(phase5_stack)) == 1


# -- Test 22: crash after commit ----------------------------------------------


async def test_enrollment_crash_after_commit(phase5_stack: Phase5Stack) -> None:
    """TEST 22: a response lost after commit is recovered by an identical retry.

    Models the crash-after-commit window by committing the enrollment and
    discarding the returned certificate, exactly as a client that never
    received the response would experience it.
    """
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    csr = _make_csr()
    machine_id = _new_machine_id()

    committed = await service.enroll(raw_token, csr, machine_id)  # response "lost"
    del committed

    record = await _token_record(phase5_stack, raw_token)
    assert enrollment_state(record) is EnrollmentState.ENROLLMENT_COMPLETED
    stored_certificate = record.issued_certificate

    recovered = await service.enroll(raw_token, csr, machine_id)
    assert recovered == stored_certificate

    # No second identity was minted during recovery.
    assert len(await _principals(phase5_stack)) == 1


# -- Partial state fails closed (spec S7/S8 step 5) ---------------------------


async def test_partial_enrollment_state_fails_closed(phase5_stack: Phase5Stack) -> None:
    """A half-written completion is never treated as completed."""
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    csr = _make_csr()
    machine_id = _new_machine_id()

    # Corrupt the row into a partial state: consumed, but no certificate.
    async def _corrupt(session):  # type: ignore[no-untyped-def]
        result = await session.execute(
            select(AgentEnrollmentTokenRecord).where(AgentEnrollmentTokenRecord.token_hash == hash_token(raw_token))
        )
        record = result.scalar_one()
        record.consumed_at = datetime.now(UTC)
        record.enrollment_csr_hash = "0" * 64
        record.enrollment_machine_installation_id = machine_id

    await phase5_stack.storage_engine.data.execute_in_transaction(_corrupt)

    record = await _token_record(phase5_stack, raw_token)
    assert enrollment_state(record) is EnrollmentState.ENROLLMENT_PARTIAL

    with pytest.raises(EnrollmentInvariantError):
        await service.enroll(raw_token, csr, machine_id)

    # Fail closed: nothing issued, nothing created.
    assert await _principals(phase5_stack) == []


async def test_enrollment_state_requires_all_six_completion_fields(phase5_stack: Phase5Stack) -> None:
    """Every strict subset of the six completion fields is PARTIAL."""
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)
    await service.enroll(raw_token, _make_csr(), _new_machine_id())

    record = await _token_record(phase5_stack, raw_token)
    assert enrollment_state(record) is EnrollmentState.ENROLLMENT_COMPLETED

    for field in (
        "consumed_at",
        "enrollment_principal_id",
        "enrollment_csr_hash",
        "enrollment_machine_installation_id",
        "issued_certificate",
        "certificate_fingerprint",
    ):
        original = getattr(record, field)
        setattr(record, field, None)
        assert enrollment_state(record) is EnrollmentState.ENROLLMENT_PARTIAL, (
            f"clearing {field!r} must not leave the record COMPLETED"
        )
        setattr(record, field, original)


# -- Test 24: private key never reaches the backend ---------------------------


async def test_private_key_never_reaches_backend(phase5_stack: Phase5Stack) -> None:
    """TEST 24: the submitted CSR carries no private key, and none is stored.

    The agent's key is generated in Windows CNG and marked non-exportable; the
    backend's half of that guarantee is that it never receives, stores, or
    could reconstruct one. This asserts the CSR payload contains no private
    key material and that nothing private lands in the database.
    """
    service = await _service(phase5_stack)
    raw_token = await service.create_enrollment_token(_TENANT)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent")]))
        .sign(key, hashes.SHA256())
    )
    csr_bytes = csr.public_bytes(serialization.Encoding.PEM)

    # The CSR payload itself contains no private key.
    assert b"PRIVATE KEY" not in csr_bytes

    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    private_numbers = key.private_numbers()

    await service.enroll(raw_token, csr_bytes, _new_machine_id())

    # Nothing resembling the private key is anywhere in the database file.
    await phase5_stack.kernel.db.disconnect()
    secret_marker = private_numbers.d.to_bytes((private_numbers.d.bit_length() + 7) // 8, "big")
    for path in phase5_stack.tmp_path.rglob("*"):
        if not path.is_file():
            continue
        blob = path.read_bytes()
        assert private_pem not in blob, f"agent private key PEM found at {path}"
        assert secret_marker not in blob, f"agent RSA private exponent found at {path}"
