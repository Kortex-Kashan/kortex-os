"""
KORTEX Agent Enrollment Service (Phase 5).

Implements the one-time agent enrollment transaction defined by
`docs/architecture/phase5_locked_architecture_spec.md` S6-S10: token custody,
the four-state enrollment state machine, the transactional issuance sequence,
deterministic idempotency, and crash recovery.

Why this is transactional rather than a sequence of steps
--------------------------------------------------------
Enrollment mints a durable identity. Every observable effect — consuming the
token, creating the `PrincipalRecord`, and recording the issued certificate —
is committed as one unit. Certificate signing happens in-process and its
output is written into the same transaction *before* commit, so there is no
window in which a certificate exists that the database does not know about.

That matters because the certificate is the only artifact the agent keeps. If
a crash could leave a signed certificate with no committed record of it, the
agent would hold a credential the backend would refuse forever; and if a crash
could leave a committed record with no certificate, a retry would mint a
second identity for one machine.

What "single use" actually means
--------------------------------
A token completes enrollment exactly once. It does not follow that a second
request carrying it is always refused: a client that never received the first
response must be able to retry. The distinction is made on content, not on
count — an identical `(token, CSR, MachineInstallationId)` triple replays the
stored certificate byte-for-byte, while any deviation in the CSR or the
machine id is a different request wearing a used token and is rejected.

Token expiry is therefore checked only on the *new enrollment* path. Applying
it to the recovery path would mean a crash 24 hours before a retry silently
destroyed an already-issued identity.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.security.exceptions import SecurityEngineError
from kortex.engines.security.models import (
    AgentEnrollmentTokenRecord,
    PrincipalRecord,
    PrincipalType,
)
from kortex.engines.security.pki import CaMaterial, KortexPki, certificate_fingerprint
from kortex.engines.storage.interfaces import IDataStore

_logger = logging.getLogger(__name__)

TOKEN_BYTES: Final[int] = 32
TOKEN_VALIDITY_HOURS: Final[int] = 24

# Dialects whose `SELECT ... FOR UPDATE` genuinely takes a row lock. SQLite has
# no row-level locking at all and silently ignores the clause, so on SQLite the
# race is resolved by the `uq_security_principals_machine_id` unique constraint
# and the recovery path below, not by the lock. Both mechanisms are always
# active — the lock is an optimization on engines that have one, never the sole
# line of defence.
_ROW_LOCKING_DIALECTS: Final[frozenset[str]] = frozenset({"postgresql", "mysql", "mariadb", "oracle"})


class EnrollmentState(str, Enum):
    """The four mutually exclusive enrollment states (spec S7)."""

    # The two `TOKEN_*` values below are state-machine labels, not credentials;
    # the `noqa` suppresses ruff's hardcoded-password heuristic matching on the
    # name, not a real secret.
    TOKEN_AVAILABLE = "TOKEN_AVAILABLE"  # noqa: S105
    TOKEN_EXPIRED = "TOKEN_EXPIRED"  # noqa: S105
    ENROLLMENT_COMPLETED = "ENROLLMENT_COMPLETED"
    ENROLLMENT_PARTIAL = "ENROLLMENT_PARTIAL"


class AgentEnrollmentError(SecurityEngineError):
    """Base error for agent enrollment failures."""


class EnrollmentRejectedError(AgentEnrollmentError):
    """Enrollment is refused (HTTP 403).

    Deliberately carries one undifferentiated meaning for "unknown token",
    "expired token", "replayed with a different CSR", and "replayed with a
    different machine id". Distinguishing them in the response would let an
    attacker holding a captured token learn whether it exists and whether it
    has already been used.
    """


class EnrollmentConflictError(AgentEnrollmentError):
    """The MachineInstallationId is already enrolled (HTTP 409)."""


class EnrollmentInvariantError(AgentEnrollmentError):
    """A partially-populated enrollment record was found (fail closed).

    Never downgraded to "treat as incomplete and re-issue": a half-written
    completion means the database's own invariant is broken, and re-issuing
    against it could mint a second certificate for a machine that already
    holds one.
    """


def hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest of a raw enrollment token.

    Issued tokens are always 64 hex characters. A non-ASCII value therefore
    cannot be a real token, and is refused as one rather than being allowed to
    surface as an encoding error: a distinguishable failure mode would tell a
    prober which inputs are even considered, and a 500 would misreport a
    rejected credential as a server fault.

    Raises:
        EnrollmentRejectedError: If the value is not ASCII.
    """
    try:
        encoded = raw_token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise EnrollmentRejectedError("Enrollment was refused.") from exc
    return hashlib.sha256(encoded).hexdigest()


def hash_csr(csr_bytes: bytes) -> str:
    """Return the SHA-256 hex digest of a submitted CSR."""
    return hashlib.sha256(csr_bytes).hexdigest()


def enrollment_state(record: AgentEnrollmentTokenRecord, now: datetime | None = None) -> EnrollmentState:
    """Classify a token record into exactly one enrollment state.

    `ENROLLMENT_COMPLETED` requires all six completion fields. Any strict,
    non-empty subset of them is `ENROLLMENT_PARTIAL` — never "completed", and
    never "available".
    """
    now = now or datetime.now(UTC)

    completion_fields = (
        record.consumed_at,
        record.enrollment_principal_id,
        record.enrollment_csr_hash,
        record.enrollment_machine_installation_id,
        record.issued_certificate,
        record.certificate_fingerprint,
    )
    populated = sum(1 for field in completion_fields if field is not None)

    if populated == len(completion_fields):
        return EnrollmentState.ENROLLMENT_COMPLETED
    if populated > 0:
        return EnrollmentState.ENROLLMENT_PARTIAL

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        # SQLite returns naive datetimes for TIMESTAMP columns. Values are
        # always written as UTC, so attach UTC rather than assuming local time.
        expires_at = expires_at.replace(tzinfo=UTC)
    if now >= expires_at:
        return EnrollmentState.TOKEN_EXPIRED
    return EnrollmentState.TOKEN_AVAILABLE


class AgentEnrollmentService:
    """Issues agent identities against single-use enrollment tokens."""

    def __init__(self, data_store: IDataStore, pki: KortexPki) -> None:
        self._data_store = data_store
        self._pki = pki

    # -- Token issuance ------------------------------------------------------

    async def create_enrollment_token(self, tenant_id: str, validity_hours: int = TOKEN_VALIDITY_HOURS) -> str:
        """Generate a single-use enrollment token and persist only its digest.

        Returns the raw 64-character hex token. This is the only moment it
        exists: it is not stored, not logged, and cannot be recovered
        afterwards. A lost token is replaced by issuing a new one.
        """
        raw_token = secrets.token_hex(TOKEN_BYTES)
        now = datetime.now(UTC)

        async def _action(session: AsyncSession) -> None:
            session.add(
                AgentEnrollmentTokenRecord(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    token_hash=hash_token(raw_token),
                    expires_at=now + timedelta(hours=validity_hours),
                )
            )
            await session.flush()

        await self._data_store.execute_in_transaction(_action)
        return raw_token

    # -- Enrollment ----------------------------------------------------------

    async def enroll(self, raw_token: str, csr_bytes: bytes, machine_installation_id: str) -> bytes:
        """Enroll an agent, or idempotently replay a completed enrollment.

        Args:
            raw_token: The raw hex enrollment token presented by the agent.
            csr_bytes: The agent's PEM or DER CSR.
            machine_installation_id: The agent's Machine Installation ID.

        Returns:
            The issued client certificate as PEM bytes.

        Raises:
            EnrollmentRejectedError: Unknown/expired token, or a replay whose
                CSR or machine id differs from the completed enrollment.
            EnrollmentConflictError: The machine is already enrolled under a
                different token.
            EnrollmentInvariantError: A partially-populated record was found.
        """
        token_hash = hash_token(raw_token)
        csr_digest = hash_csr(csr_bytes)

        # Loaded BEFORE the transaction opens. Reading the CA from the
        # SecretStore is itself a database transaction, and on SQLite the
        # engine serializes session acquisition behind a single non-reentrant
        # write lock, so loading it inside the enrollment transaction would
        # deadlock. Signing itself is pure computation and stays inside the
        # transaction, so the certificate is still persisted before commit.
        #
        # Loading it unconditionally — including on the replay path — also
        # means a missing or corrupt CA fails enrollment closed rather than
        # silently serving certificates while the trust anchor is broken.
        ca_material = await self._pki.load_ca_material()

        try:
            return await self._data_store.execute_in_transaction(
                lambda session: self._enroll_in_transaction(
                    session, token_hash, csr_bytes, csr_digest, machine_installation_id, ca_material
                )
            )
        except IntegrityError:
            # A concurrent transaction committed the enrollment for this token
            # or this machine between our read and our insert, and the database
            # constraint stopped the duplicate. Re-evaluate against the now
            # committed state: an identical request replays that certificate,
            # anything else is refused.
            _logger.info(
                "Agent enrollment hit a uniqueness constraint; re-evaluating against committed state.",
                extra={"machine_installation_id": machine_installation_id},
            )
            return await self._data_store.execute_in_transaction(
                lambda session: self._replay_completed_enrollment(
                    session, token_hash, csr_digest, machine_installation_id
                )
            )

    async def _enroll_in_transaction(
        self,
        session: AsyncSession,
        token_hash: str,
        csr_bytes: bytes,
        csr_digest: str,
        machine_installation_id: str,
        ca_material: CaMaterial,
    ) -> bytes:
        now = datetime.now(UTC)
        record = await self._lock_token(session, token_hash)
        if record is None:
            raise EnrollmentRejectedError("Enrollment was refused.")

        state = enrollment_state(record, now)

        # Idempotency branch is evaluated BEFORE expiration, so a completed
        # enrollment stays recoverable after its token's 24 hours elapse.
        if state is EnrollmentState.ENROLLMENT_COMPLETED:
            return self._replay_or_refuse(record, csr_digest, machine_installation_id)

        if state is EnrollmentState.ENROLLMENT_PARTIAL:
            _logger.error(
                "Partially populated agent enrollment record detected; failing closed.",
                extra={"token_record_id": record.id},
            )
            raise EnrollmentInvariantError("The enrollment record is in an inconsistent state and cannot be completed.")

        if state is EnrollmentState.TOKEN_EXPIRED:
            raise EnrollmentRejectedError("Enrollment was refused.")

        # -- New enrollment --------------------------------------------------
        existing = await session.execute(
            select(PrincipalRecord.id).where(PrincipalRecord.machine_installation_id == machine_installation_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise EnrollmentConflictError("This machine installation is already enrolled.")

        principal_id = str(uuid.uuid4())

        # Reserve the token first (spec S8 step 6), so the row carries the
        # binding even though nothing is visible to other transactions until
        # this one commits.
        record.consumed_at = now
        record.enrollment_csr_hash = csr_digest
        record.enrollment_machine_installation_id = machine_installation_id

        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=record.tenant_id,
                principal_id=principal_id,
                principal_type=PrincipalType.AGENT.value,
                enabled=True,
                machine_installation_id=machine_installation_id,
                roles=["agent"],
                attributes={"enrolled_at": now.isoformat()},
            )
        )
        # Surfaces a duplicate machine id as an IntegrityError here, inside the
        # transaction, rather than at commit time where it could not be
        # recovered from.
        await session.flush()

        certificate, certificate_pem = self._pki.sign_client_csr_with_material(ca_material, csr_bytes, principal_id)

        record.enrollment_principal_id = principal_id
        record.issued_certificate = certificate_pem
        record.certificate_fingerprint = certificate_fingerprint(certificate)
        await session.flush()

        return certificate_pem

    async def _replay_completed_enrollment(
        self,
        session: AsyncSession,
        token_hash: str,
        csr_digest: str,
        machine_installation_id: str,
    ) -> bytes:
        """Resolve a request that lost a race, against committed state only."""
        record = await self._lock_token(session, token_hash)
        if record is None:
            raise EnrollmentRejectedError("Enrollment was refused.")

        state = enrollment_state(record)
        if state is EnrollmentState.ENROLLMENT_COMPLETED:
            return self._replay_or_refuse(record, csr_digest, machine_installation_id)

        if state is EnrollmentState.ENROLLMENT_PARTIAL:
            raise EnrollmentInvariantError("The enrollment record is in an inconsistent state and cannot be completed.")

        # The token itself is unused, so the constraint that fired was the
        # machine id's: this installation is already enrolled elsewhere.
        raise EnrollmentConflictError("This machine installation is already enrolled.")

    @staticmethod
    def _replay_or_refuse(record: AgentEnrollmentTokenRecord, csr_digest: str, machine_installation_id: str) -> bytes:
        """Return the stored certificate iff the replay matches exactly."""
        if record.enrollment_csr_hash != csr_digest:
            raise EnrollmentRejectedError("Enrollment was refused.")
        if record.enrollment_machine_installation_id != machine_installation_id:
            raise EnrollmentRejectedError("Enrollment was refused.")
        certificate = record.issued_certificate
        if certificate is None:  # pragma: no cover - excluded by the state check
            raise EnrollmentInvariantError("The completed enrollment has no stored certificate.")
        return certificate

    @staticmethod
    async def _lock_token(session: AsyncSession, token_hash: str) -> AgentEnrollmentTokenRecord | None:
        statement = select(AgentEnrollmentTokenRecord).where(AgentEnrollmentTokenRecord.token_hash == token_hash)
        bind = session.get_bind()
        if bind.dialect.name in _ROW_LOCKING_DIALECTS:
            statement = statement.with_for_update()
        result = await session.execute(statement)
        return result.scalar_one_or_none()
