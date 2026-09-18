"""KORTEX Phase 6 — desktop-command transport over the Phase 5 mTLS session.

Reuses the Phase 5 `GatewayHarness`/`_enroll_agent` fixtures from
`test_agent_gateway_mtls.py` unchanged: the identity, mTLS, and revocation
layer under test there is exactly what this transport now rides on top of,
and duplicating that setup here would be a second place it could drift.

These tests drive a fake .NET agent — a plain gRPC client that reads a
pushed command and writes back a `desktop_result` — because the actual
FlaUI/UIA execution is the Desktop Agent's job (`apps/desktop-agent`), not
the Gateway's. What is under test here is purely: command correlation,
agent selection (fail-closed on zero/many matches), timeout, and
session-disconnect handling — never what a command does on the Windows side.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.agent_gateway.exceptions import (
    DesktopAgentAmbiguousError,
    DesktopAgentUnavailableError,
    DesktopCommandTimeoutError,
    DesktopSessionDisconnectedError,
)
from kortex.engines.agent_gateway.protos import agent_pb2, agent_pb2_grpc
from kortex.engines.security.models import PrincipalRecord
from tests.integration.test_agent_gateway_mtls import (
    EnrolledAgent,
    GatewayHarness,
    _enroll_agent,
    harness,  # noqa: F401 - re-exported fixture, referenced by name as a parameter
)


def _launch_command(command_id: str) -> agent_pb2.GatewayMessage:
    return agent_pb2.GatewayMessage(
        desktop_launch=agent_pb2.DesktopLaunchCommand(
            command_id=command_id, application_id="notepad", timeout_seconds=10
        )
    )


class _FakeAgentSession:
    """A minimal fake Desktop Agent: connects, acks, then answers exactly one
    pushed command (or none, for the timeout/disconnect tests)."""

    def __init__(self, harness: GatewayHarness, agent: EnrolledAgent) -> None:
        self._harness = harness
        self._agent = agent
        self._channel = None
        self._call = None
        self._to_agent: asyncio.Queue[agent_pb2.AgentMessage] = asyncio.Queue()

    async def __aenter__(self) -> _FakeAgentSession:
        self._channel = self._harness.channel(self._agent)
        await self._channel.__aenter__()
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(self._channel)

        async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
            yield agent_pb2.AgentMessage(
                status=agent_pb2.AgentStatus(machine_installation_id=self._agent.machine_id, status="ONLINE")
            )
            while True:
                yield await self._to_agent.get()

        self._call = stub.Connect(_outbound())
        ack = await asyncio.wait_for(self._call.read(), timeout=15)
        assert ack.HasField("session_ack")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self._call.cancel()
        await self._channel.__aexit__(*exc_info)

    async def answer_next_command(self, *, success: bool = True) -> agent_pb2.GatewayMessage:
        """Read the next pushed command and immediately echo a matching result."""
        pushed = await asyncio.wait_for(self._call.read(), timeout=15)
        command_id = getattr(pushed, pushed.WhichOneof("payload")).command_id
        await self._to_agent.put(
            agent_pb2.AgentMessage(
                desktop_result=agent_pb2.DesktopCommandResult(
                    command_id=command_id,
                    success=success,
                    window_handle="win-1" if success else "",
                    process_id=4242 if success else 0,
                    error_code="" if success else "ELEMENT_NOT_FOUND",
                )
            )
        )
        return pushed


async def test_desktop_command_round_trip(harness: GatewayHarness) -> None:
    """A command pushed to the one live session is answered and correlated."""
    agent = await _enroll_agent(harness.stack)
    async with _FakeAgentSession(harness, agent) as fake_agent:
        send = asyncio.ensure_future(
            harness.gateway.send_desktop_command(
                tenant_id="tenant-alpha", build_command=_launch_command, timeout_seconds=10
            )
        )
        pushed = await fake_agent.answer_next_command(success=True)
        assert pushed.desktop_launch.application_id == "notepad"

        result = await send
        assert result.success is True
        assert result.window_handle == "win-1"
        assert result.process_id == 4242


async def test_desktop_command_no_agent_connected_fails_closed(harness: GatewayHarness) -> None:
    """No live session for the tenant: unavailable, not a hang or a crash."""
    with pytest.raises(DesktopAgentUnavailableError):
        await harness.gateway.send_desktop_command(
            tenant_id="tenant-alpha", build_command=_launch_command, timeout_seconds=2
        )


async def test_desktop_command_unconfirmed_session_is_not_a_valid_target(harness: GatewayHarness) -> None:
    """A session that has connected with a valid client certificate but has
    never sent its first `status` message must not be usable as a desktop
    command target, even though the Gateway already registered it in
    `sessions` -- otherwise identity/machine-installation binding, which the
    module's own docstring claims is enforced, would only ever be checked
    for the one message type (`status`) a compliant client happens to send,
    never for the privileged commands/results Phase 6 adds to the same
    stream."""
    agent = await _enroll_agent(harness.stack)

    async def _outbound() -> AsyncIterator[agent_pb2.AgentMessage]:
        await asyncio.sleep(30)
        yield  # pragma: no cover - never reached; only makes this an async generator

    async with harness.channel(agent) as channel:
        stub = agent_pb2_grpc.DesktopAgentGatewayStub(channel)
        call = stub.Connect(_outbound())
        try:
            for _ in range(50):
                if harness.gateway.sessions:
                    break
                await asyncio.sleep(0.1)
            assert len(harness.gateway.sessions) == 1
            session = next(iter(harness.gateway.sessions.values()))
            assert session.identity_confirmed is False

            with pytest.raises(DesktopAgentUnavailableError):
                await harness.gateway.send_desktop_command(
                    tenant_id="tenant-alpha", build_command=_launch_command, timeout_seconds=2
                )
        finally:
            call.cancel()


async def test_desktop_command_ambiguous_agents_fail_closed(harness: GatewayHarness) -> None:
    """Two live sessions for the same tenant with no `agent_id`: ambiguous, not
    an arbitrary pick of one of them."""
    first_agent = await _enroll_agent(harness.stack)
    second_agent = await _enroll_agent(harness.stack)
    async with _FakeAgentSession(harness, first_agent), _FakeAgentSession(harness, second_agent):
        with pytest.raises(DesktopAgentAmbiguousError):
            await harness.gateway.send_desktop_command(
                tenant_id="tenant-alpha", build_command=_launch_command, timeout_seconds=2
            )


async def test_desktop_command_disambiguated_by_agent_id(harness: GatewayHarness) -> None:
    """The same two-session tenant succeeds once `agent_principal_id` picks one."""
    first_agent = await _enroll_agent(harness.stack)
    second_agent = await _enroll_agent(harness.stack)
    async with _FakeAgentSession(harness, first_agent) as fake_first, _FakeAgentSession(harness, second_agent):
        send = asyncio.ensure_future(
            harness.gateway.send_desktop_command(
                tenant_id="tenant-alpha",
                agent_principal_id=first_agent.principal_id,
                build_command=_launch_command,
                timeout_seconds=10,
            )
        )
        await fake_first.answer_next_command(success=True)
        result = await send
        assert result.success is True


async def test_desktop_command_timeout(harness: GatewayHarness) -> None:
    """A connected agent that never answers times out, rather than hanging
    the caller forever."""
    agent = await _enroll_agent(harness.stack)
    async with _FakeAgentSession(harness, agent):
        with pytest.raises(DesktopCommandTimeoutError):
            await harness.gateway.send_desktop_command(
                tenant_id="tenant-alpha", build_command=_launch_command, timeout_seconds=1
            )


async def test_desktop_command_fails_when_agent_disabled_mid_command(harness: GatewayHarness) -> None:
    """Disabling the agent's principal while a command is in flight severs
    the real mTLS session (Phase 5's existing 5-second revocation sweep,
    unmodified) and fails the pending command as a disconnect -- not a
    timeout, and not a silently-lost command."""
    agent = await _enroll_agent(harness.stack)
    async with _FakeAgentSession(harness, agent):
        send = asyncio.ensure_future(
            harness.gateway.send_desktop_command(
                tenant_id="tenant-alpha", build_command=_launch_command, timeout_seconds=10
            )
        )
        await asyncio.sleep(0.05)  # let the command actually reach pending_commands

        async def _disable(session: AsyncSession) -> None:
            await session.execute(
                update(PrincipalRecord).where(PrincipalRecord.principal_id == agent.principal_id).values(enabled=False)
            )

        await harness.stack.storage_engine.data.execute_in_transaction(_disable)

        revoked = await harness.gateway.revoke_disabled_sessions()
        assert len(revoked) == 1

        with pytest.raises(DesktopSessionDisconnectedError):
            await send


async def test_desktop_command_session_disconnect_fails_pending() -> None:
    """A session that closes while a command is in flight fails that command
    with a disconnect error, not a bare timeout or a silent hang."""
    from pathlib import Path
    from tempfile import TemporaryDirectory

    import pytest as _pytest

    from tests.security.conftest import build_phase5_stack

    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        mp = _pytest.MonkeyPatch()
        try:
            stack = await build_phase5_stack(tmp_path, mp, name="disconnect")
            await stack.pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")
            await stack.pki.issue_server_certificate("gateway.kortex.local", ip_address="127.0.0.1")

            from kortex.engines.agent_gateway.engine import AgentGatewayEngine

            gateway = AgentGatewayEngine(data_store=stack.storage_engine.data, pki=stack.pki, host="127.0.0.1", port=0)
            await gateway.start()
            try:
                agent = await _enroll_agent(stack)
                # Register a session directly (bypassing the network) so we can
                # sever it deterministically without racing a real socket close.
                from kortex.engines.agent_gateway.engine import AgentSession, DesktopAgentGatewayServicer

                session = AgentSession(
                    session_id="s1",
                    principal_id=agent.principal_id,
                    tenant_id="tenant-alpha",
                    machine_installation_id=agent.machine_id,
                    # This test targets session-disconnect handling, not the
                    # identity-confirmation handshake itself.
                    identity_confirmed=True,
                )
                gateway.sessions["s1"] = session

                send = asyncio.ensure_future(
                    gateway.send_desktop_command(
                        tenant_id="tenant-alpha", build_command=_launch_command, timeout_seconds=10
                    )
                )
                await asyncio.sleep(0.05)  # let the command reach `pending_commands`
                DesktopAgentGatewayServicer._fail_pending_commands(session, DesktopSessionDisconnectedError("closed"))

                with pytest.raises(DesktopSessionDisconnectedError):
                    await send
            finally:
                await gateway.stop(grace=0)
                await stack.kernel.db.disconnect()
        finally:
            mp.undo()
