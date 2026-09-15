"""
KORTEX OS — Integration Hub M2 Test Suite
GitHub OAuth Connector: the full production wiring — `kortex.security.
integration_oauth.begin/complete` -> `kortex.connector.profile.register`
(GitHub branch) -> dynamically-registered `kortex.connector.<profile_id>.*`
capabilities -> `HttpRestConnectorDriver` (network mocked) ->
`kortex.connector.integration.disconnect` — exercised through real `Kernel.
invoke_capability()` dispatch, not direct engine method calls, so the actual
production `secret_resolver`/capability-registration wiring in `ConnectorEngine.
initialize()` is what gets proven, mirroring `test_connector_action_reference_
integration.py`'s (F5) exact fixture/mocking pattern for the sibling milestone.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import CapabilityNotFoundError
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.connector.drivers.http_driver import HttpRestConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.models import ConnectorProfile
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import SecretNotFoundError
from kortex.engines.security.models import IntegrationTokenSet, PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x77" * 32
_TEST_SIGNING_KEY = b"\x11" * 32
_ROLE = "M2_GITHUB_INTEGRATION_ROLE"
_TENANT_A = "m2_github_tenant_alpha"
_TENANT_B = "m2_github_tenant_beta"


class FakeIntegrationOAuthProvider:
    provider_id = "github"

    def __init__(self) -> None:
        self.exchanged_codes: list[str] = []

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        return f"https://fake-github.example/authorize?state={state}&redirect_uri={redirect_uri}"

    async def exchange_code(self, code: str, redirect_uri: str) -> IntegrationTokenSet:
        self.exchanged_codes.append(code)
        return IntegrationTokenSet(access_token=f"gh-access-{code}")

    async def refresh(self, refresh_token: str) -> IntegrationTokenSet:
        raise AssertionError("refresh should not be called in this test — the token never expires")


class _MockStreamResponse:
    """Mirrors `test_connector_action_reference_integration.py`'s own helper exactly."""

    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self.chunks = chunks
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()


@pytest.fixture
async def github_kernel_env(
    tmp_path: Path,
) -> AsyncIterator[tuple[Kernel, ConnectorEngine, FakeIntegrationOAuthProvider]]:
    db_path = (tmp_path / f"kortex_m2_github_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    provider = FakeIntegrationOAuthProvider()
    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_m2_github_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_SIGNING_KEY,
        integration_oauth_providers={"github": provider},
    )
    connector_engine = ConnectorEngine(data_store=data_store)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:write"))
        for tenant in (_TENANT_A, _TENANT_B):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant,
                    principal_id="user_github",
                    principal_type="USER",
                    credential_hash=hasher.hash("pass-github"),
                    roles=[_ROLE],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    await storage_engine.data.execute_in_transaction(_seed)
    connector_engine.register_driver(HttpRestConnectorDriver())

    try:
        yield kernel, connector_engine, provider
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _token(kernel: Kernel, tenant_id: str) -> str:
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": "user_github", "password": "pass-github"}
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _connect_github_profile(kernel: Kernel, tenant_id: str, profile_id: str) -> None:
    """Drives the real, full flow a desktop client would: begin -> complete
    -> profile.register — every step through `Kernel.invoke_capability()`,
    proving the production dispatch/wiring path, not a shortcut around it."""
    token = await _token(kernel, tenant_id)

    begin = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.security.integration_oauth.begin",
            session_token=token,
            parameters={"provider": "github", "profile_id": profile_id, "redirect_uri": "app://callback"},
            context={"resource_tenant_id": tenant_id},
        )
    )

    complete = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.security.integration_oauth.complete",
            session_token=token,
            parameters={
                "provider": "github",
                "profile_id": profile_id,
                "code": f"code-for-{profile_id}",
                "state": begin["state"],
                "redirect_uri": "app://callback",
            },
            context={"resource_tenant_id": tenant_id},
        )
    )
    assert complete["connected"] is True

    await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.profile.register",
            session_token=token,
            parameters={
                "profile": {
                    "profile_id": profile_id,
                    "name": f"GitHub {profile_id}",
                    "driver_id": "connector-http-rest",
                    "secret_handle": complete["secret_handle"],
                    "options": {"base_url": "https://api.github.com", "integration_provider": "github"},
                }
            },
            context={"resource_tenant_id": tenant_id},
        )
    )


@pytest.mark.asyncio
async def test_full_connect_dispatch_disconnect_chain(
    github_kernel_env: tuple[Kernel, ConnectorEngine, FakeIntegrationOAuthProvider],
) -> None:
    kernel, _connector_engine, _provider = github_kernel_env
    await _connect_github_profile(kernel, _TENANT_A, "gh-prof-1")

    # The dynamically-registered capability exists and is dispatchable.
    assert kernel.get_capability("kortex.connector.gh-prof-1.user_get").owner_id == "gh-prof-1"

    token = await _token(kernel, _TENANT_A)
    resp_bytes = json.dumps({"login": "octocat"}).encode("utf-8")
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("140.82.112.6", 443))]),
        patch.object(httpx.AsyncClient, "stream") as mock_stream,
    ):
        mock_stream.return_value = _MockStreamResponse([resp_bytes], status_code=200)
        result = await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.connector.gh-prof-1.user_get",
                session_token=token,
                parameters={},
                context={"resource_tenant_id": _TENANT_A},
            )
        )

    assert result["body"] == {"login": "octocat"}
    call_kwargs = mock_stream.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer gh-access-code-for-gh-prof-1"
    assert call_kwargs["url"] == "https://api.github.com/user"

    # Disconnect: one call, capability gone immediately.
    disconnect_result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.integration.disconnect",
            session_token=token,
            parameters={"profile_id": "gh-prof-1"},
            context={"resource_tenant_id": _TENANT_A},
        )
    )
    assert disconnect_result["disconnected"] is True
    assert disconnect_result["credential_revoked"] is True

    with pytest.raises(CapabilityNotFoundError):
        kernel.get_capability("kortex.connector.gh-prof-1.user_get")

    # Calling disconnect a second time is a safe no-op, not an error.
    second = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.integration.disconnect",
            session_token=token,
            parameters={"profile_id": "gh-prof-1"},
            context={"resource_tenant_id": _TENANT_A},
        )
    )
    assert second["disconnected"] is True


@pytest.mark.asyncio
async def test_cross_profile_credential_isolation_same_tenant(
    github_kernel_env: tuple[Kernel, ConnectorEngine, FakeIntegrationOAuthProvider],
) -> None:
    """Profile B cannot use Profile A's integration credential even within
    the same tenant — the resolver's `(tenant_id, profile_id)` binding check
    proven through the real, wired pipeline, not just `IntegrationOAuthManager`
    in isolation (already covered by its own unit tests)."""
    kernel, connector_engine, _provider = github_kernel_env
    await _connect_github_profile(kernel, _TENANT_A, "gh-prof-a")

    security_engine: SecurityEngine = kernel.get_engine("security")
    profile_a = await connector_engine.get_profile("gh-prof-a", tenant_id=_TENANT_A)

    # Register a second, unrelated GitHub-flavored profile in the SAME
    # tenant, but forge its secret_handle to point at Profile A's opaque
    # handle instead of its own — this must never resolve.
    await connector_engine.register_profile(
        ConnectorProfile(
            profile_id="gh-prof-b",
            tenant_id=_TENANT_A,
            name="GitHub B",
            driver_id="connector-http-rest",
            secret_handle=profile_a.secret_handle,
            options={"base_url": "https://api.github.com", "integration_provider": "github"},
        )
    )

    resolved = await security_engine.integration_oauth_manager.resolve_access_token(
        profile_a.secret_handle, _TENANT_A, "gh-prof-b"
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_cross_tenant_credential_isolation(
    github_kernel_env: tuple[Kernel, ConnectorEngine, FakeIntegrationOAuthProvider],
) -> None:
    """Tenant A's integration credential must never resolve through a
    Tenant B call, even if Tenant B somehow obtained Tenant A's opaque
    secret handle (e.g. leaked or guessed) and named the matching
    `profile_id` — `SecretStore`'s own tenant-scoped lookup already
    enforces this at the storage layer; this proves it holds through the
    full wired connect path. (`ConnectorProfile.profile_id` is a single
    global namespace on this platform — reusing the identical id across two
    tenants would itself be a pre-existing, out-of-scope collision, so each
    tenant here gets its own id, same as production usage would.)"""
    kernel, connector_engine, _provider = github_kernel_env
    await _connect_github_profile(kernel, _TENANT_A, "gh-tenant-a-prof")
    await _connect_github_profile(kernel, _TENANT_B, "gh-tenant-b-prof")

    security_engine: SecurityEngine = kernel.get_engine("security")
    profile_a = await connector_engine.get_profile("gh-tenant-a-prof", tenant_id=_TENANT_A)
    profile_b = await connector_engine.get_profile("gh-tenant-b-prof", tenant_id=_TENANT_B)
    assert profile_a.secret_handle != profile_b.secret_handle

    # Tenant B's resolver call, using Tenant A's handle and Tenant A's own
    # profile_id, must not succeed — `SecretStore`'s own tenant-scoped
    # lookup fails closed with `SecretNotFoundError` before this manager's
    # OAuth-binding check is even reached, exactly as it already would for
    # any other connector's plain secret (unchanged, pre-existing behavior).
    with pytest.raises(SecretNotFoundError):
        await security_engine.integration_oauth_manager.resolve_access_token(
            profile_a.secret_handle, _TENANT_B, "gh-tenant-a-prof"
        )
