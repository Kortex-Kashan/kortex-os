"""Adversarial mTLS and session-lifecycle tests for the Phase 5 Agent Gateway.

Covers spec S17-S19, S21 and adversarial matrix tests 6, 8-12, 25-35.

Every TLS test drives a real `grpc.aio` server over a real TCP socket with real
certificates issued by the real internal CA. Rejection is asserted by observing
that the handshake or the RPC actually fails — never by asserting that a mock
was called. A mocked TLS boundary would prove nothing about whether OpenSSL is
configured to enforce client authentication, which is the property under test.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import delete, update

from kortex.engines.agent_gateway.engine import AgentGatewayEngine
from kortex.engines.agent_gateway.protos import agent_pb2, agent_pb2_grpc
from kortex.engines.security.agent_enrollment import AgentEnrollmentService
from kortex.engines.security.agent_session import AgentSessionAuthorizer
from kortex.engines.security.exceptions import (
    CaUnavailableError,
    ServerCertificateUnavailableError,
)
from kortex.engines.security.models import PrincipalRecord, PrincipalType
from kortex.engines.security.pki import (
    CA_PRIVATE_KEY_HANDLE,
    GATEWAY_SERVER_CERTIFICATE_HANDLE,
    SYSTEM_TENANT_ID,
)
from tests.security.conftest import Phase5Stack, build_phase5_stack

pytestmark = pytest.mark.integration

_GATEWAY_HOSTNAME = "gateway.kortex.local"
_TENANT = "tenant-alpha"


# -- Fixtures and helpers ------------------------------------------------------


class EnrolledAgent:
    """An enrolled agent's client credentials."""

    def __init__(self, principal_id: str, machine_id: str, certificate_pem: bytes, key_pem: bytes) -> None:
        self.principal_id = principal_id
        self.machine_id = machine_id
        self.certificate_pem = certificate_pem
        self.key_pem = key_pem


class GatewayHarness:
    """A running Gateway plus everything needed to talk to it."""

    def __init__(self, stack: Phase5Stack, gateway: AgentGatewayEngine, ca_pem: bytes, port: int) -> None:
        self.stack = stack
        self.gateway = gateway
        self.ca_pem = ca_pem
        self.port = port

    @property
    def target(self) -> str:
        return f"{_GATEWAY_HOSTNAME}:{self.port}"

    def channel(self, agent: EnrolledAgent | None = None, root_certificates: bytes | None = None):
        """Build a client channel, optionally without client credentials."""
        credentials = grpc.ssl_channel_credentials(
            root_certificates=root_certificates if root_certificates is not None else self.ca_pem,
            private_key=agent.key_pem if agent else None,
            certificate_chain=agent.certificate_pem if agent else None,
        )
        # The certificate's SAN is the gateway hostname, but the socket is on
        # localhost. This tells gRPC which name to verify against, so hostname
        # verification stays genuinely enabled rather than being bypassed.
        options = (("grpc.ssl_target_name_override", _GATEWAY_HOSTNAME),)
        return grpc.aio.secure_channel(f"127.0.0.1:{self.port}", credentials, options=options)


async def _enroll_agent(stack: Phase5Stack, tenant_id: str = _TENANT) -> EnrolledAgent:
    """Enroll one agent end-to-end and return its real client credentials."""
    service = AgentEnrollmentService(data_store=stack.storage_engine.data, pki=stack.pki)
    raw_token = await service.create_enrollment_token(tenant_id)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent")]))
        .sign(key, hashes.SHA256())
    )
    machine_id = str(uuid.uuid4())
    certificate_pem = await service.enroll(raw_token, csr.public_bytes(serialization.Encoding.PEM), machine_id)

    certificate = x509.load_pem_x509_certificate(certificate_pem)
    principal_id = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value

    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return EnrolledAgent(str(principal_id), machine_id, certificate_pem, key_pem)


@pytest.fixture
async def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[GatewayHarness]:
    """A running mTLS Gateway with a fresh CA, on an ephemeral port."""
    stack = await build_phase5_stack(tmp_path, monkeypatch, name="gateway")
    ca_certificate = await stack.pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")
    await stack.pki.issue_server_certificate(_GATEWAY_HOSTNAME, ip_address="127.0.0.1")

    gateway = AgentGatewayEngine(
        data_store=stack.storage_engine.data,
        pki=stack.pki,
        host="127.0.0.1",
        port=0,
        monitor_interval_seconds=5.0,
    )
    port = await gateway.start()
    try:
        yield GatewayHarness(stack, gateway, ca_certificate.public_bytes(serialization.Encoding.PEM), port)
    finally:
        await gateway.stop(grace=0)
        await stack.kernel.db.disconnect()


async def _connect_once(
    harness: GatewayHarness, agent: EnrolledAgent, machine_id: str | None = None
) -> agent_pb2.GatewayMessage:
    """Open a stream, send one AgentStatus, and return the first reply."""

    async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
        yield agent_pb2.AgentMessage(
            status=agent_pb2.AgentStatus(
                machine_installation_id=machine_id if machine_id is not None else agent.machine_id,
                status="ONLINE",
            )
        )
        await asyncio.sleep(30)  # hold the stream open

    async with harness.channel(agent) as channel:
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
        call = stub.Connect(_outbound())
        return await asyncio.wait_for(call.read(), timeout=15)


# -- Test 8: valid mTLS connection ---------------------------------------------


async def test_mtls_valid_connection(harness: GatewayHarness) -> None:
    """TEST 8: a valid client and server certificate establish a session."""
    agent = await _enroll_agent(harness.stack)
    reply = await _connect_once(harness, agent)

    assert reply.HasField("session_ack")
    assert reply.session_ack.session_id


# -- Test 25: CN -> PrincipalRecord mapping ------------------------------------


async def test_gateway_principal_cn_mapping(harness: GatewayHarness) -> None:
    """TEST 25: the Gateway maps the certificate CN to the PrincipalRecord."""
    agent = await _enroll_agent(harness.stack)

    async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
        yield agent_pb2.AgentMessage(
            status=agent_pb2.AgentStatus(machine_installation_id=agent.machine_id, status="ONLINE")
        )
        await asyncio.sleep(30)

    async with harness.channel(agent) as channel:
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
        call = stub.Connect(_outbound())
        reply = await asyncio.wait_for(call.read(), timeout=15)

        sessions = list(harness.gateway.sessions.values())
        assert len(sessions) == 1
        session = sessions[0]
        assert session.session_id == reply.session_ack.session_id
        assert session.principal_id == agent.principal_id
        assert session.machine_installation_id == agent.machine_id
        call.cancel()


# -- Test 28: tenant authority -------------------------------------------------


async def test_gateway_tenant_derived_from_db(harness: GatewayHarness) -> None:
    """TEST 28: the session tenant comes from the database, not the client.

    The client is given no way to state a tenant — the contract has no such
    field — and additionally sends a spoofed `tenant_id` in gRPC metadata,
    which must be ignored.
    """
    agent = await _enroll_agent(harness.stack, tenant_id="tenant-alpha")

    async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
        yield agent_pb2.AgentMessage(
            status=agent_pb2.AgentStatus(machine_installation_id=agent.machine_id, status="ONLINE")
        )
        await asyncio.sleep(30)

    async with harness.channel(agent) as channel:
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
        call = stub.Connect(
            _outbound(),
            metadata=(("tenant_id", "tenant-attacker"), ("tenant-id", "tenant-attacker")),
        )
        await asyncio.wait_for(call.read(), timeout=15)

        session = next(iter(harness.gateway.sessions.values()))
        assert session.tenant_id == "tenant-alpha"
        assert session.tenant_id != "tenant-attacker"
        call.cancel()


# -- Test 9: untrusted client --------------------------------------------------


async def test_mtls_untrusted_client_rejected(harness: GatewayHarness) -> None:
    """TEST 9: a self-signed client certificate is rejected by the Gateway."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "impostor")])
    now = datetime.datetime.now(datetime.UTC)
    self_signed = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True)
        .sign(key, hashes.SHA256())
    )

    impostor = EnrolledAgent(
        "impostor",
        str(uuid.uuid4()),
        self_signed.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )

    with pytest.raises(grpc.aio.AioRpcError) as excinfo:
        await _connect_once(harness, impostor)
    assert excinfo.value.code() in {
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.UNAUTHENTICATED,
        grpc.StatusCode.INTERNAL,
    }


async def test_mtls_client_without_certificate_rejected(harness: GatewayHarness) -> None:
    """A client presenting NO certificate cannot connect at all.

    Proves `require_client_auth=True` is genuinely in force, rather than the
    Gateway merely checking a certificate when one happens to be offered.
    """

    async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
        yield agent_pb2.AgentMessage(status=agent_pb2.AgentStatus(machine_installation_id="x", status="ONLINE"))
        await asyncio.sleep(30)

    async with harness.channel(agent=None) as channel:
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
        call = stub.Connect(_outbound())
        with pytest.raises(grpc.aio.AioRpcError):
            await asyncio.wait_for(call.read(), timeout=15)


# -- Test 10: expired client ---------------------------------------------------


async def test_mtls_expired_client_rejected(harness: GatewayHarness) -> None:
    """TEST 10: an expired client certificate is rejected at the handshake."""
    ca_material = await harness.stack.pki.load_ca_material()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.UTC)

    expired = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired-agent")]))
        .issuer_name(ca_material.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=30))
        .not_valid_after(now - datetime.timedelta(days=1))  # already expired
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True)
        .sign(ca_material.private_key, hashes.SHA256())
    )

    agent = EnrolledAgent(
        "expired-agent",
        str(uuid.uuid4()),
        expired.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )

    with pytest.raises(grpc.aio.AioRpcError):
        await _connect_once(harness, agent)


# -- Tests 11/12/6: the agent's side of server validation ----------------------


async def test_mtls_untrusted_server_rejected(harness: GatewayHarness) -> None:
    """TEST 11: the agent rejects a server certificate from an unknown CA.

    The client is configured to trust a DIFFERENT CA than the one that issued
    the Gateway's certificate, so the handshake must fail.
    """
    agent = await _enroll_agent(harness.stack)

    foreign_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Rogue CA")])
    now = datetime.datetime.now(datetime.UTC)
    foreign_ca = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(foreign_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(foreign_key, hashes.SHA256())
    )

    async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
        yield agent_pb2.AgentMessage(
            status=agent_pb2.AgentStatus(machine_installation_id=agent.machine_id, status="ONLINE")
        )
        await asyncio.sleep(30)

    async with harness.channel(agent, root_certificates=foreign_ca.public_bytes(serialization.Encoding.PEM)) as channel:
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
        call = stub.Connect(_outbound())
        with pytest.raises(grpc.aio.AioRpcError):
            await asyncio.wait_for(call.read(), timeout=15)


async def test_server_cert_hostname_verification(harness: GatewayHarness) -> None:
    """TEST 6: the client validates the SAN against the expected hostname.

    Connects with a target name override that the Gateway certificate's SAN
    does not cover; TLS hostname verification must fail.
    """
    agent = await _enroll_agent(harness.stack)

    credentials = grpc.ssl_channel_credentials(
        root_certificates=harness.ca_pem,
        private_key=agent.key_pem,
        certificate_chain=agent.certificate_pem,
    )
    options = (("grpc.ssl_target_name_override", "not-the-gateway.example.com"),)

    async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
        yield agent_pb2.AgentMessage(
            status=agent_pb2.AgentStatus(machine_installation_id=agent.machine_id, status="ONLINE")
        )
        await asyncio.sleep(30)

    async with grpc.aio.secure_channel(f"127.0.0.1:{harness.port}", credentials, options=options) as channel:
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
        call = stub.Connect(_outbound())
        with pytest.raises(grpc.aio.AioRpcError):
            await asyncio.wait_for(call.read(), timeout=15)


async def test_mtls_expired_server_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST 12: the agent rejects an expired server certificate.

    Stands up a Gateway whose server certificate is already expired (signed
    directly by the real CA with a past `not_valid_after`), then asserts a
    correctly-configured client refuses it.
    """
    stack = await build_phase5_stack(tmp_path, monkeypatch, name="expired_server")
    ca_certificate = await stack.pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")
    ca_material = await stack.pki.load_ca_material()

    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.UTC)
    expired_server_certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _GATEWAY_HOSTNAME)]))
        .issuer_name(ca_material.certificate.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=400))
        .not_valid_after(now - datetime.timedelta(days=1))  # already expired
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(_GATEWAY_HOSTNAME)]),
            critical=False,
        )
        .sign(ca_material.private_key, hashes.SHA256())
    )

    await stack.secret_store.put_secret(
        GATEWAY_SERVER_CERTIFICATE_HANDLE,
        SYSTEM_TENANT_ID,
        expired_server_certificate.public_bytes(serialization.Encoding.PEM).decode("ascii"),
    )
    await stack.secret_store.put_secret(
        "system/pki/gateway_server_private_key",
        SYSTEM_TENANT_ID,
        server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii"),
    )

    gateway = AgentGatewayEngine(data_store=stack.storage_engine.data, pki=stack.pki, host="127.0.0.1", port=0)
    port = await gateway.start()
    try:
        harness = GatewayHarness(stack, gateway, ca_certificate.public_bytes(serialization.Encoding.PEM), port)
        agent = await _enroll_agent(stack)

        async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
            yield agent_pb2.AgentMessage(
                status=agent_pb2.AgentStatus(machine_installation_id=agent.machine_id, status="ONLINE")
            )
            await asyncio.sleep(30)

        async with harness.channel(agent) as channel:
            stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
            call = stub.Connect(_outbound())
            with pytest.raises(grpc.aio.AioRpcError):
                await asyncio.wait_for(call.read(), timeout=15)
    finally:
        await gateway.stop(grace=0)
        await stack.kernel.db.disconnect()


# -- Test 26: unknown principal ------------------------------------------------


async def test_gateway_unknown_principal_rejected(harness: GatewayHarness) -> None:
    """TEST 26: a cryptographically valid cert whose principal was deleted is rejected."""
    agent = await _enroll_agent(harness.stack)

    async def _delete(session):  # type: ignore[no-untyped-def]
        await session.execute(delete(PrincipalRecord).where(PrincipalRecord.principal_id == agent.principal_id))

    await harness.stack.storage_engine.data.execute_in_transaction(_delete)

    with pytest.raises(grpc.aio.AioRpcError) as excinfo:
        await _connect_once(harness, agent)
    assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED


# -- Test 27: disabled principal -----------------------------------------------


async def test_gateway_disabled_principal_rejected(harness: GatewayHarness) -> None:
    """TEST 27: a valid certificate for a disabled principal is rejected."""
    agent = await _enroll_agent(harness.stack)

    async def _disable(session):  # type: ignore[no-untyped-def]
        await session.execute(
            update(PrincipalRecord).where(PrincipalRecord.principal_id == agent.principal_id).values(enabled=False)
        )

    await harness.stack.storage_engine.data.execute_in_transaction(_disable)

    with pytest.raises(grpc.aio.AioRpcError) as excinfo:
        await _connect_once(harness, agent)
    assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_gateway_wrong_principal_type_rejected(harness: GatewayHarness) -> None:
    """A principal that is not an AGENT cannot open an agent session."""
    agent = await _enroll_agent(harness.stack)

    async def _retype(session):  # type: ignore[no-untyped-def]
        await session.execute(
            update(PrincipalRecord)
            .where(PrincipalRecord.principal_id == agent.principal_id)
            .values(principal_type=PrincipalType.USER.value)
        )

    await harness.stack.storage_engine.data.execute_in_transaction(_retype)

    with pytest.raises(grpc.aio.AioRpcError) as excinfo:
        await _connect_once(harness, agent)
    assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED


# -- Test 29: machine id mismatch ----------------------------------------------


async def test_gateway_machine_id_mismatch_rejected(harness: GatewayHarness) -> None:
    """TEST 29: an AgentStatus whose machine id differs from the record is rejected.

    The certificate is entirely valid and the principal is enabled — only the
    installation binding disagrees. Cryptographic identity alone must not be
    sufficient.
    """
    agent = await _enroll_agent(harness.stack)

    with pytest.raises(grpc.aio.AioRpcError) as excinfo:
        await _connect_once(harness, agent, machine_id=str(uuid.uuid4()))
    assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED

    # And an empty machine id is equally refused.
    with pytest.raises(grpc.aio.AioRpcError):
        await _connect_once(harness, agent, machine_id="")


# -- Test 30: database unavailable ---------------------------------------------


async def test_gateway_db_unavailable_fails_closed(harness: GatewayHarness) -> None:
    """TEST 30: a database error during authorization rejects the stream.

    Fails closed as UNAVAILABLE, not as a silent success and not as a
    "principal not found" denial that would mask the outage.
    """
    from kortex.engines.agent_gateway.engine import DesktopAgentGatewayServicer

    agent = await _enroll_agent(harness.stack)

    class _AbortedError(Exception):
        def __init__(self, code: grpc.StatusCode, details: str) -> None:
            self.code = code
            self.details = details

    class _FailingDataStore:
        """An IDataStore whose every transaction fails, as a real outage would."""

        async def execute_in_transaction(self, action):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated database outage")

    class _Context:
        """Presents the agent's genuine, valid certificate to the servicer."""

        def auth_context(self) -> dict[str, list[bytes]]:
            return {"x509_pem_cert": [agent.certificate_pem]}

        async def abort(self, code: grpc.StatusCode, details: str) -> None:
            raise _AbortedError(code, details)

    async def _no_messages() -> AsyncIterator[agent_pb2.AgentMessage]:
        return
        yield  # pragma: no cover - makes this an async generator

    servicer = DesktopAgentGatewayServicer(
        AgentSessionAuthorizer(_FailingDataStore()),  # type: ignore[arg-type]
        {},
    )

    with pytest.raises(_AbortedError) as excinfo:
        async for _ in servicer.Connect(_no_messages(), _Context()):  # type: ignore[arg-type]
            pass

    # UNAVAILABLE, not UNAUTHENTICATED: an indeterminate answer must not be
    # reported as a successful denial, which would hide the outage.
    assert excinfo.value.code == grpc.StatusCode.UNAVAILABLE
    assert harness.gateway.sessions == {}


# -- Test 31: SecretStore unavailable ------------------------------------------


async def test_gateway_secret_store_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST 31: a Gateway whose PKI material is unavailable refuses to boot."""
    stack = await build_phase5_stack(tmp_path, monkeypatch, name="no_secrets")
    try:
        # No CA and no server certificate exist at all.
        gateway = AgentGatewayEngine(data_store=stack.storage_engine.data, pki=stack.pki, host="127.0.0.1", port=0)
        with pytest.raises(ServerCertificateUnavailableError):
            await gateway.start()
        assert gateway.bound_port is None

        # With a CA but a corrupt server certificate, boot still fails closed.
        await stack.pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")
        await stack.pki.issue_server_certificate(_GATEWAY_HOSTNAME)
        await stack.secret_store.put_secret(GATEWAY_SERVER_CERTIFICATE_HANDLE, SYSTEM_TENANT_ID, "corrupt")
        with pytest.raises(ServerCertificateUnavailableError):
            await gateway.start()

        # And with a corrupt CA, boot fails closed too.
        await stack.pki.issue_server_certificate(_GATEWAY_HOSTNAME)
        await stack.secret_store.put_secret(CA_PRIVATE_KEY_HANDLE, SYSTEM_TENANT_ID, "corrupt")
        await stack.secret_store.put_secret("system/pki/ca_public_certificate", SYSTEM_TENANT_ID, "corrupt")
        with pytest.raises(CaUnavailableError):
            await gateway.start()
    finally:
        await stack.kernel.db.disconnect()


# -- Tests 32/33: active session revocation ------------------------------------


async def test_active_session_disabled_terminated(harness: GatewayHarness) -> None:
    """TEST 32: disabling a principal severs its established stream.

    Asserts the documented 5-second polling contract: the Gateway's configured
    monitor interval is 5 seconds, and a sweep at that cadence revokes the
    session and closes the stream with UNAUTHENTICATED.
    """
    assert harness.gateway._monitor_interval == 5.0

    agent = await _enroll_agent(harness.stack)

    async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
        yield agent_pb2.AgentMessage(
            status=agent_pb2.AgentStatus(machine_installation_id=agent.machine_id, status="ONLINE")
        )
        await asyncio.sleep(60)  # stays silent, as a real idle agent would

    async with harness.channel(agent) as channel:
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
        call = stub.Connect(_outbound())
        await asyncio.wait_for(call.read(), timeout=15)
        assert len(harness.gateway.sessions) == 1

        async def _disable(session):  # type: ignore[no-untyped-def]
            await session.execute(
                update(PrincipalRecord).where(PrincipalRecord.principal_id == agent.principal_id).values(enabled=False)
            )

        await harness.stack.storage_engine.data.execute_in_transaction(_disable)

        revoked = await harness.gateway.revoke_disabled_sessions()
        assert len(revoked) == 1

        # The stream is severed well within the 5-second polling window.
        with pytest.raises(grpc.aio.AioRpcError) as excinfo:
            await asyncio.wait_for(call.read(), timeout=5)
        assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED

    await asyncio.sleep(0.1)
    assert harness.gateway.sessions == {}


async def test_active_session_deleted_terminated(harness: GatewayHarness) -> None:
    """TEST 33: deleting a principal severs its established stream."""
    agent = await _enroll_agent(harness.stack)

    async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
        yield agent_pb2.AgentMessage(
            status=agent_pb2.AgentStatus(machine_installation_id=agent.machine_id, status="ONLINE")
        )
        await asyncio.sleep(60)

    async with harness.channel(agent) as channel:
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
        call = stub.Connect(_outbound())
        await asyncio.wait_for(call.read(), timeout=15)

        async def _delete(session):  # type: ignore[no-untyped-def]
            await session.execute(delete(PrincipalRecord).where(PrincipalRecord.principal_id == agent.principal_id))

        await harness.stack.storage_engine.data.execute_in_transaction(_delete)

        revoked = await harness.gateway.revoke_disabled_sessions()
        assert len(revoked) == 1

        with pytest.raises(grpc.aio.AioRpcError) as excinfo:
            await asyncio.wait_for(call.read(), timeout=5)
        assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_lifecycle_monitor_runs_automatically_every_five_seconds(harness: GatewayHarness) -> None:
    """The monitor is a live background task, not merely a callable method.

    Disables a principal and waits — without calling the sweep — for the
    Gateway's own timer to sever the session.
    """
    agent = await _enroll_agent(harness.stack)

    async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
        yield agent_pb2.AgentMessage(
            status=agent_pb2.AgentStatus(machine_installation_id=agent.machine_id, status="ONLINE")
        )
        await asyncio.sleep(60)

    async with harness.channel(agent) as channel:
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
        call = stub.Connect(_outbound())
        await asyncio.wait_for(call.read(), timeout=15)

        async def _disable(session):  # type: ignore[no-untyped-def]
            await session.execute(
                update(PrincipalRecord).where(PrincipalRecord.principal_id == agent.principal_id).values(enabled=False)
            )

        await harness.stack.storage_engine.data.execute_in_transaction(_disable)

        # No manual sweep. Allow one full 5s interval plus scheduling slack.
        with pytest.raises(grpc.aio.AioRpcError) as excinfo:
            await asyncio.wait_for(call.read(), timeout=12)
        assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED


# -- Test 34: no capability execution ------------------------------------------


def test_gateway_no_capability_execution() -> None:
    """TEST 34: the Gateway exposes and imports no execution path.

    Checks the module's own source and namespace rather than its behaviour,
    because the property is an absence: there must be no method, import, or
    reference through which a capability could be dispatched.
    """
    import ast
    import inspect

    from kortex.engines.agent_gateway import engine as gateway_engine

    tree = ast.parse(inspect.getsource(gateway_engine))

    forbidden_modules = {
        "subprocess",
        "os",
        "shutil",
        "pty",
        "ctypes",
        "winreg",
        "kortex.core.dispatch",
        "kortex.engines.python_exec",
    }
    forbidden_names = {
        "CapabilityDispatcher",
        "CapabilityRequest",
        "eval",
        "exec",
        "compile",
        "__import__",
        "system",
        "popen",
        "spawn",
        "Popen",
        "run",
    }

    imported_modules: set[str] = set()
    referenced_names: set[str] = set()
    called_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module)
                imported_modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Name):
            referenced_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced_names.add(node.attr)

        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                called_names.add(target.id)
            elif isinstance(target, ast.Attribute):
                called_names.add(target.attr)

    # Nothing that could execute anything is imported.
    for module in imported_modules:
        root = module.split(".")[0]
        assert root not in forbidden_modules, f"Gateway imports forbidden module {module!r}"
        assert not module.startswith("kortex.core.dispatch"), f"Gateway imports {module!r}"
        assert not module.startswith("kortex.engines.python_exec"), f"Gateway imports {module!r}"

    # No dispatcher or execution primitive is named or called anywhere in
    # executable code. Comments and docstrings are excluded by construction,
    # because the AST carries only real code.
    assert referenced_names & forbidden_names == set(), (
        f"Gateway references forbidden names: {sorted(referenced_names & forbidden_names)}"
    )
    assert called_names & forbidden_names == set(), (
        f"Gateway calls forbidden names: {sorted(called_names & forbidden_names)}"
    )
    assert not any("dispatch" in name.lower() for name in referenced_names | called_names)

    # Nothing in the module's runtime namespace is a dispatcher.
    for name in dir(gateway_engine):
        assert "dispatch" not in name.lower(), f"Gateway exposes {name!r}"

    # And no execution-capable method exists on the servicer.
    servicer_methods = {
        name for name, _ in inspect.getmembers(gateway_engine.DesktopAgentGatewayServicer, inspect.isfunction)
    }
    assert servicer_methods & {"Execute", "Invoke", "RunCommand", "Dispatch"} == set()
    assert "Connect" in servicer_methods


# -- Test 35: protobuf contract has no executable fields -----------------------


def test_grpc_contract_no_executable_fields() -> None:
    """TEST 35: the compiled protobuf descriptor contains no execution messages.

    Inspects the compiled `FileDescriptor` — the actual wire contract — rather
    than the .proto text, so a stale or divergent generated module cannot pass.
    """
    file_descriptor = agent_pb2.DESCRIPTOR

    message_names = set(file_descriptor.message_types_by_name)
    assert message_names == {"AgentMessage", "GatewayMessage", "AgentStatus", "SessionAck"}

    # Exactly one service, exactly one method, bidirectional streaming.
    assert set(file_descriptor.services_by_name) == {"DesktopAgentGateway"}
    service = file_descriptor.services_by_name["DesktopAgentGateway"]
    assert [method.name for method in service.methods] == ["Connect"]
    connect = service.methods_by_name["Connect"]
    assert connect.client_streaming is True
    assert connect.server_streaming is True

    # Every field across every message, checked against execution vocabulary.
    forbidden_fragments = (
        "capability",
        "execute",
        "command",
        "shell",
        "process",
        "script",
        "python",
        "powershell",
        "cmd",
        "payload_bytes",
        "automation",
        "keystroke",
        "click",
    )
    for message_name, message in file_descriptor.message_types_by_name.items():
        for field in message.fields:
            lowered = field.name.lower()
            for fragment in forbidden_fragments:
                assert fragment not in lowered, (
                    f"{message_name}.{field.name} matches forbidden execution vocabulary {fragment!r}"
                )

    # The agent->gateway and gateway->agent payload unions each have exactly
    # one permitted member.
    assert [f.name for f in file_descriptor.message_types_by_name["AgentMessage"].fields] == ["status"]
    assert [f.name for f in file_descriptor.message_types_by_name["GatewayMessage"].fields] == ["session_ack"]
    assert [f.name for f in file_descriptor.message_types_by_name["AgentStatus"].fields] == [
        "machine_installation_id",
        "status",
    ]
    assert [f.name for f in file_descriptor.message_types_by_name["SessionAck"].fields] == ["session_id"]


def test_proto_sources_are_identical() -> None:
    """The backend and Desktop Agent must compile the same contract.

    Two copies of a protocol definition that drift apart is a wire-format
    incompatibility waiting to happen, so they are asserted byte-identical.
    """
    backend_proto = (
        Path(__file__).resolve().parents[2] / "src" / "kortex" / "engines" / "agent_gateway" / "protos" / "agent.proto"
    )
    agent_proto = Path(__file__).resolve().parents[3] / "apps" / "desktop-agent" / "protos" / "agent.proto"
    assert backend_proto.exists()
    assert agent_proto.exists(), "the Desktop Agent's copy of agent.proto is missing"
    assert backend_proto.read_bytes() == agent_proto.read_bytes()
