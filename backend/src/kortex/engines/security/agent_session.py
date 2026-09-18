"""
Agent session authorization (Phase 5).

Owns the question "may this agent hold a session right now?" on behalf of the
Agent Gateway, per `docs/architecture/phase5_locked_architecture_spec.md` S18.

Why this lives in the Security Engine rather than in the Gateway
----------------------------------------------------------------
Resolving a principal from an identifier and deciding whether it may act is
the Security Engine's job, and the platform enforces that structurally: only
`core/dispatch.py` and `engines/security/**` may resolve a principal, a rule
asserted repo-wide by
`tests/unit/test_capability_identity_propagation_architecture.py`. That rule
exists because the confirmed Capability Identity Propagation vulnerability was
exactly a component outside this boundary deciding for itself who was calling.

The Gateway's case is a legitimate top-level authentication boundary — the
identifier comes from a certificate that OpenSSL has already chain-validated,
not from caller-supplied request data — but the right way to express that is
to delegate to this module, not to carve the Gateway out of the invariant. A
second place in the codebase that resolves principals is a second place that
can drift.

Fail-closed contract
--------------------
Two failure modes are deliberately distinct. A principal that is absent,
disabled, or of the wrong type is a *decision*: the answer is known and it is
"no". A database that cannot be consulted is *not* a decision, and reporting
it as one would make an outage indistinguishable from a denial and hide it
from operators. Callers must map the two to different statuses.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.security.exceptions import SecurityEngineError
from kortex.engines.security.models import PrincipalRecord, PrincipalType
from kortex.engines.storage.interfaces import IDataStore


@dataclass(frozen=True)
class AgentIdentity:
    """The authoritative, database-resolved identity of a connected agent.

    Every field here comes from the `PrincipalRecord`. Nothing a client sends
    contributes to it — in particular `tenant_id`, which the client has no way
    to influence and no way to override.
    """

    principal_id: str
    tenant_id: str
    machine_installation_id: str | None


class AgentAuthorizationDeniedError(SecurityEngineError):
    """The agent may not hold a session: unknown, disabled, or not an AGENT.

    Carries no detail about which of those it was. A caller that reported the
    difference would let anyone holding a certificate probe whether a given
    principal exists and whether it is currently enabled.
    """


class AgentAuthorizationUnavailableError(SecurityEngineError):
    """Authorization could not be determined (e.g. the database is down).

    Never collapsed into `AgentAuthorizationDeniedError`: "we do not know"
    must not be recorded or surfaced as "we checked and the answer is no".
    """


class AgentSessionAuthorizer:
    """Resolves and re-checks agent authority against the database."""

    def __init__(self, data_store: IDataStore) -> None:
        self._data_store = data_store

    async def authorize(self, principal_id: str) -> AgentIdentity:
        """Resolve a principal id to an authorized agent identity.

        Args:
            principal_id: The Subject CN of an already chain-validated client
                certificate.

        Returns:
            The database-resolved identity, including its authoritative tenant.

        Raises:
            AgentAuthorizationDeniedError: Unknown, disabled, or not an AGENT.
            AgentAuthorizationUnavailableError: The database could not be read.
        """

        async def _action(session: AsyncSession) -> PrincipalRecord | None:
            result = await session.execute(select(PrincipalRecord).where(PrincipalRecord.principal_id == principal_id))
            return result.scalars().first()

        try:
            record = await self._data_store.execute_in_transaction(_action)
        except Exception as exc:
            raise AgentAuthorizationUnavailableError("Agent authorization could not be determined.") from exc

        if record is None or record.principal_type != PrincipalType.AGENT.value or not record.enabled:
            raise AgentAuthorizationDeniedError("Agent identity is disabled or revoked.")

        return AgentIdentity(
            principal_id=record.principal_id,
            tenant_id=record.tenant_id,
            machine_installation_id=record.machine_installation_id,
        )

    async def still_authorized(self, principal_ids: set[str]) -> set[str]:
        """Return the subset of `principal_ids` that may still hold a session.

        Used by the Gateway's lifecycle monitor. Answering for the whole
        connected set in one query keeps the 5-second sweep a single round
        trip regardless of how many agents are connected.

        A principal missing from the result is either deleted or no longer
        authorized; the two are equivalent for revocation purposes.

        Raises:
            AgentAuthorizationUnavailableError: The database could not be read.
        """
        if not principal_ids:
            return set()

        async def _action(session: AsyncSession) -> set[str]:
            result = await session.execute(
                select(PrincipalRecord).where(PrincipalRecord.principal_id.in_(principal_ids))
            )
            return {
                record.principal_id
                for record in result.scalars().all()
                if record.principal_type == PrincipalType.AGENT.value and record.enabled
            }

        try:
            return await self._data_store.execute_in_transaction(_action)
        except Exception as exc:
            raise AgentAuthorizationUnavailableError("Agent authorization could not be determined.") from exc
