"""
KORTEX Agent Gateway (Phase 5).

A `grpc.aio` server that terminates mutual TLS with Desktop Agents and
maintains authenticated sessions, per
`docs/architecture/phase5_locked_architecture_spec.md` S17-S19.

Two independent layers must both agree before a session exists:

1. **Cryptographic** — handled by OpenSSL inside gRPC. The client must present
   a certificate that chains to the KORTEX Root CA, is unexpired, and carries
   the `clientAuth` EKU. `require_client_auth=True` means a connection without
   one never reaches application code at all.

2. **Authorization** — handled here. A cryptographically valid certificate is
   only a claim about *which* principal is connecting; whether that principal
   may connect is a live database question. The certificate's subject CN is
   resolved to a `PrincipalRecord`, which must exist, be of type `AGENT`, and
   be enabled.

This split is what gives Phase 5 revocation without a CRL. Certificates stay
valid until they expire, but authority is re-read from the database on every
connection and re-checked every 5 seconds for the life of the session, so
disabling a principal severs its established streams rather than merely
preventing new ones.

Tenancy is never negotiated. `tenant_id` is read from the `PrincipalRecord`
and nothing a client sends — metadata, status payload, or certificate field —
can influence it.

What this module deliberately cannot do
---------------------------------------
It does not import, reference, or reach `CapabilityDispatcher`, and the
protocol it speaks has no message capable of expressing an instruction. An
authenticated session is the entire product of Phase 5; executing anything
over that session is Phase 6 and requires its own authorization design.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Final

import grpc
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID

from kortex.engines.agent_gateway.protos import agent_pb2, agent_pb2_grpc
from kortex.engines.security.agent_session import (
    AgentAuthorizationDeniedError,
    AgentAuthorizationUnavailableError,
    AgentSessionAuthorizer,
)
from kortex.engines.security.pki import KortexPki
from kortex.engines.storage.interfaces import IDataStore

_logger = logging.getLogger(__name__)

LIFECYCLE_MONITOR_INTERVAL_SECONDS: Final[float] = 5.0

_UNAUTHENTICATED_MESSAGE: Final[str] = "Agent identity is disabled or revoked."


@dataclass
class AgentSession:
    """One live, authenticated agent stream."""

    session_id: str
    principal_id: str
    tenant_id: str
    machine_installation_id: str | None
    revoked: asyncio.Event = field(default_factory=asyncio.Event)
    revocation_reason: str | None = None


class DesktopAgentGatewayServicer(agent_pb2_grpc.DesktopAgentGatewayServicer):
    """Implements the Phase 5 `Connect` session protocol.

    Deliberately holds no database access of its own. Identity resolution and
    the authority decision belong to the Security Engine
    (`AgentSessionAuthorizer`), which is the only sanctioned place in the
    codebase permitted to resolve a principal — see that module's docstring.
    """

    def __init__(self, authorizer: AgentSessionAuthorizer, sessions: dict[str, AgentSession]) -> None:
        self._authorizer = authorizer
        self._sessions = sessions

    # -- Identity resolution -------------------------------------------------

    @staticmethod
    def extract_principal_id(context: grpc.aio.ServicerContext) -> str | None:
        """Read the client certificate's Subject CN from the TLS auth context.

        Prefers parsing the peer's DER certificate directly over gRPC's
        pre-extracted `x509_common_name`, because the parsed certificate is
        the same object the chain validation accepted, leaving no room for the
        two to disagree.
        """
        auth_context = context.auth_context() or {}

        pem_entries = auth_context.get("x509_pem_cert")
        if pem_entries:
            try:
                certificate = x509.load_pem_x509_certificate(pem_entries[0])
                attributes = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                if attributes:
                    value = attributes[0].value
                    return value if isinstance(value, str) else value.decode("utf-8")
            except Exception:  # pragma: no cover - defensive
                _logger.warning("Peer certificate present but unparseable; rejecting.")
                return None

        common_names = auth_context.get("x509_common_name")
        if common_names:
            raw = common_names[0]
            return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return None

    # -- Session protocol ----------------------------------------------------

    async def Connect(  # noqa: N802 - gRPC generated method name
        self,
        request_iterator: AsyncIterator[agent_pb2.AgentMessage],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[agent_pb2.GatewayMessage]:
        """Authenticate an agent and maintain its session stream."""
        principal_id = self.extract_principal_id(context)
        if not principal_id:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, _UNAUTHENTICATED_MESSAGE)
            return

        try:
            identity = await self._authorizer.authorize(principal_id)
        except AgentAuthorizationUnavailableError:
            # Indeterminate, not denied. Reported as UNAVAILABLE so an outage
            # is visible as an outage instead of masquerading as a rejection.
            _logger.error("Agent authorization failed closed: principal state is indeterminate.")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Agent authorization is unavailable.")
            return
        except AgentAuthorizationDeniedError:
            _logger.warning("Agent session refused.", extra={"principal_id": principal_id})
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, _UNAUTHENTICATED_MESSAGE)
            return

        session = AgentSession(
            session_id=str(uuid.uuid4()),
            principal_id=identity.principal_id,
            # Authoritative, from the database. Never from the client.
            tenant_id=identity.tenant_id,
            machine_installation_id=identity.machine_installation_id,
        )

        expected_machine_id = identity.machine_installation_id
        self._sessions[session.session_id] = session
        _logger.info(
            "Agent session established.",
            extra={"session_id": session.session_id, "principal_id": principal_id},
        )

        try:
            async for message in self._merge_with_revocation(request_iterator, session, context):
                if not message.HasField("status"):
                    # The contract has exactly one agent payload. Anything else
                    # is a client that does not speak this protocol.
                    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Unsupported agent message.")
                    return

                presented = message.status.machine_installation_id
                if not presented or presented != expected_machine_id:
                    # Cryptographic identity and installation binding must
                    # agree. A valid certificate presented from a different
                    # installation than the one it was enrolled for is
                    # rejected, not merely logged.
                    _logger.warning(
                        "Agent session rejected: MachineInstallationId does not match the enrolled principal.",
                        extra={"session_id": session.session_id, "principal_id": principal_id},
                    )
                    await context.abort(grpc.StatusCode.UNAUTHENTICATED, _UNAUTHENTICATED_MESSAGE)
                    return

                yield agent_pb2.GatewayMessage(session_ack=agent_pb2.SessionAck(session_id=session.session_id))
        finally:
            self._sessions.pop(session.session_id, None)
            _logger.info("Agent session closed.", extra={"session_id": session.session_id})

    async def _merge_with_revocation(
        self,
        request_iterator: AsyncIterator[agent_pb2.AgentMessage],
        session: AgentSession,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[agent_pb2.AgentMessage]:
        """Yield client messages, aborting promptly if the session is revoked.

        The lifecycle monitor cannot reach into an in-flight RPC to cancel it,
        so it sets the session's `revoked` event and this loop — which is
        inside the RPC — turns that into an actual abort. Without racing the
        event against the read, a silent agent that never sends another
        message would keep its stream open indefinitely after being disabled.
        """
        revoked_task = asyncio.ensure_future(session.revoked.wait())
        iterator = request_iterator.__aiter__()
        try:
            while True:
                next_task = asyncio.ensure_future(iterator.__anext__())
                done, _pending = await asyncio.wait({next_task, revoked_task}, return_when=asyncio.FIRST_COMPLETED)

                if revoked_task in done:
                    next_task.cancel()
                    _logger.warning(
                        "Agent session revoked by lifecycle monitor.",
                        extra={
                            "session_id": session.session_id,
                            "principal_id": session.principal_id,
                            "reason": session.revocation_reason,
                        },
                    )
                    await context.abort(grpc.StatusCode.UNAUTHENTICATED, _UNAUTHENTICATED_MESSAGE)
                    return

                try:
                    yield next_task.result()
                except StopAsyncIteration:
                    return
        finally:
            revoked_task.cancel()


class AgentGatewayEngine:
    """Hosts the mTLS gRPC endpoint and the session lifecycle monitor."""

    def __init__(
        self,
        data_store: IDataStore,
        pki: KortexPki,
        host: str = "127.0.0.1",
        port: int = 50051,
        monitor_interval_seconds: float = LIFECYCLE_MONITOR_INTERVAL_SECONDS,
    ) -> None:
        # The Gateway never queries principals itself; every identity question
        # goes through the Security Engine's authorizer.
        self._authorizer = AgentSessionAuthorizer(data_store)
        self._pki = pki
        self._host = host
        self._port = port
        self._monitor_interval = monitor_interval_seconds
        self._server: grpc.aio.Server | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._sessions: dict[str, AgentSession] = {}
        self._bound_port: int | None = None

    @property
    def sessions(self) -> dict[str, AgentSession]:
        """The currently active authenticated sessions."""
        return self._sessions

    @property
    def bound_port(self) -> int | None:
        """The port actually bound, resolved after `start()`."""
        return self._bound_port

    async def start(self) -> int:
        """Start the mTLS gRPC server and the lifecycle monitor.

        Raises:
            CaUnavailableError: If the Root CA is missing or unusable.
            ServerCertificateUnavailableError: If the server certificate or
                key is missing or unusable.

        Both are fatal by design: a Gateway that cannot prove its own identity
        or validate clients' must not accept connections at all. There is no
        plaintext fallback and no self-signed substitute.
        """
        # Loaded before the server exists, so a failure aborts boot rather than
        # leaving a listener up in a degraded state.
        certificate_chain, private_key = await self._pki.load_server_material()
        ca_certificate = await self._pki.load_ca_certificate()
        root_certificates = ca_certificate.public_bytes(serialization.Encoding.PEM)

        credentials = grpc.ssl_server_credentials(
            [(private_key, certificate_chain)],
            root_certificates=root_certificates,
            require_client_auth=True,
        )

        self._server = grpc.aio.server()
        # The registration helper is generated code and carries no annotations;
        # the project's `disallow_untyped_calls` cannot be satisfied without
        # hand-editing a file that `protoc` regenerates.
        agent_pb2_grpc.add_DesktopAgentGatewayServicer_to_server(  # type: ignore[no-untyped-call]
            DesktopAgentGatewayServicer(self._authorizer, self._sessions), self._server
        )
        self._bound_port = self._server.add_secure_port(f"{self._host}:{self._port}", credentials)
        if self._bound_port == 0:
            raise RuntimeError(f"Agent Gateway could not bind {self._host}:{self._port}.")

        await self._server.start()
        self._monitor_task = asyncio.ensure_future(self._lifecycle_monitor())
        _logger.info("Agent Gateway listening with mTLS on %s:%s", self._host, self._bound_port)
        return self._bound_port

    async def stop(self, grace: float | None = None) -> None:
        """Stop the monitor and the server."""
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            # Shutdown is best-effort: the monitor task is being torn down on
            # purpose, and a failure from the task we just cancelled must not
            # stop the server itself from shutting down.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._monitor_task
            self._monitor_task = None
        if self._server is not None:
            await self._server.stop(grace)
            self._server = None

    # -- Lifecycle monitor ---------------------------------------------------

    async def _lifecycle_monitor(self) -> None:
        """Re-check every connected principal's authority every 5 seconds."""
        while True:
            await asyncio.sleep(self._monitor_interval)
            try:
                await self.revoke_disabled_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed sweep must not kill the monitor: that would silently
                # disable revocation for every session for the process's life.
                _logger.exception("Agent Gateway lifecycle monitor sweep failed; continuing.")

    async def revoke_disabled_sessions(self) -> list[str]:
        """Revoke sessions whose principal is now disabled or deleted.

        Returns the session ids revoked by this sweep.
        """
        if not self._sessions:
            return []

        principal_ids = {session.principal_id for session in self._sessions.values()}

        # An authorization outage propagates rather than being read as "none of
        # these principals are authorized" — mass-revoking every live session
        # because the database blinked would turn a transient fault into an
        # outage of its own. The caller logs and retries on the next sweep.
        still_authorized = await self._authorizer.still_authorized(principal_ids)

        revoked: list[str] = []
        for session in list(self._sessions.values()):
            if session.principal_id in still_authorized:
                continue
            session.revocation_reason = "principal_disabled_or_deleted"

            _logger.warning(
                "Revoking active agent session.",
                extra={
                    "session_id": session.session_id,
                    "principal_id": session.principal_id,
                    "tenant_id": session.tenant_id,
                    "reason": session.revocation_reason,
                },
            )
            session.revoked.set()
            revoked.append(session.session_id)

        return revoked
