"""Integration suite for the MCP Gateway / Catalog milestone.

Every test here enters through the *real* Kernel capability-dispatch boundary
— real `SecurityEngine` authentication, real RBAC/ABAC, real
`kernel.invoke_capability` — never through a raw-manager or raw-driver
shortcut, matching the methodology already established by
`test_mcp_integration_slice.py` (Integration Hub M1) and
`test_connector_tenant_isolation_dispatch.py` (M6.3-1).

The mandatory end-to-end scenario proves the whole chain:

    authorized tenant
      -> MCP ConnectorProfile
      -> kortex.mcp.catalog.profile.list      (discovery)
      -> kortex.mcp.catalog.capability.list   (discovery)
      -> kortex.mcp.gateway.invoke            (access boundary)
      -> tenant/profile/capability authorization
      -> kortex.mcp.<profile_id>.<tool_name>  (canonical capability)
      -> CapabilityDispatcher                 (existing execution boundary)
      -> McpConnectorDriver                   (existing MCP infrastructure)
      -> MCP server                           (deterministic local double)
      -> structured result -> gateway response

The only thing stubbed anywhere below is the remote MCP server itself (the
`streamable_http_client`/`ClientSession` transport seam, stubbed exactly as
M1's own vertical slice already does — no external SaaS dependency is
introduced for testing). Everything between the Gateway and that seam is the
real production code path, including the real `CapabilityDispatcher`, which a
pass-through spy records so the execution boundary can be asserted on rather
than assumed.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from mcp.types import CallToolResult, TextContent, Tool
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.capability_projection import register_projection_capabilities
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import CapabilityNotFoundError
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import (
    ConnectorProfileNotFoundError,
    ConnectorSecurityError,
    ConnectorValidationError,
)
from kortex.engines.connector.mcp_gateway import (
    CATALOG_CAPABILITY_GET_CAPABILITY,
    CATALOG_CAPABILITY_LIST_CAPABILITY,
    CATALOG_PROFILE_GET_CAPABILITY,
    CATALOG_PROFILE_LIST_CAPABILITY,
    GATEWAY_INVOKE_CAPABILITY,
    mcp_capability_name,
)
from kortex.engines.connector.models import ConnectorProfile
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32

_ROLE = "MCP_GATEWAY_TEST_ROLE"
_TENANT_A = "tenant_a_mcp_gw"
_TENANT_B = "tenant_b_mcp_gw"
_USER_A = "user_mcp_gw_a"
_USER_B = "user_mcp_gw_b"
_PASSWORD = "gateway-test-pass"  # test-only local credential

_PROFILE_A = "mcp-gw-profile-a"
_PROFILE_A2 = "mcp-gw-profile-a-second"
_PROFILE_B = "mcp-gw-profile-b"
_PROFILE_A_DISABLED = "mcp-gw-profile-a-disabled"
_HTTP_PROFILE_A = "http-gw-profile-a"

_SECRET_HANDLE = "vault:mcp-gw-secret"  # a handle, not a secret value
_SECRET_VALUE = "mcp-gw-access-token-SHOULD-NEVER-APPEAR"  # test-only leak sentinel

# Tools the deterministic MCP server double advertises for every profile.
_TOOL_QUERY = "query_data"
_TOOL_EXPORT = "export_csv"
# A second profile deliberately advertises a tool the first one does not, so
# "wrong profile/tool combination" is a genuinely distinct case from
# "nonexistent tool" rather than an artifact of identical tool sets.
_TOOL_ARCHIVE = "archive_dataset"


@dataclass
class _Env:
    """Everything a test needs, plus the dispatch spy's recording."""

    kernel: Kernel
    connector_engine: ConnectorEngine
    security_engine: SecurityEngine
    mcp_driver: Any
    dispatched: list[str] = field(default_factory=list)


def _tool(name: str, description: str, schema: dict[str, Any]) -> Tool:
    return Tool(name=name, description=description, inputSchema=schema)


def _mock_mcp_session(tool_names: list[str]) -> MagicMock:
    """Build a deterministic MCP server double exposing `tool_names`."""
    catalog = {
        _TOOL_QUERY: _tool(
            _TOOL_QUERY,
            "Run a query against the warehouse",
            {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
        ),
        _TOOL_EXPORT: _tool(
            _TOOL_EXPORT,
            "Export a dataset to CSV",
            {"type": "object", "properties": {"limit": {"type": "integer"}}},
        ),
        _TOOL_ARCHIVE: _tool(
            _TOOL_ARCHIVE,
            "Archive a dataset",
            {"type": "object", "properties": {"dataset": {"type": "string"}}},
        ),
    }
    session = MagicMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=MagicMock(tools=[catalog[n] for n in tool_names]))
    session.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text="42 rows returned successfully.")],
            isError=False,
        )
    )
    return session


async def _token(env: _Env, tenant_id: str, principal_id: str) -> Any:
    principal = await env.security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": _PASSWORD,
        }
    )
    return await env.security_engine.authentication_manager.issue_token(principal)


async def _invoke(env: _Env, capability: str, token: Any, tenant_id: str, /, **parameters: Any) -> Any:
    """Drive one capability through the real Kernel dispatch boundary.

    Positional-only up to `tenant_id` so a test can pass a *capability*
    parameter literally named `tenant_id` (proving the Gateway ignores a
    caller-supplied one) without colliding with this helper's own argument.
    """
    return await env.kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability,
            session_token=token,
            parameters=dict(parameters),
            context={"resource_tenant_id": tenant_id},
        )
    )


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[_Env]:
    """A fully booted Kernel with two tenants, four MCP profiles, and reconciled tools."""
    db_path = (tmp_path / f"kortex_mcp_gw_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_mcp_gw_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    connector_engine = ConnectorEngine(data_store=data_store)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)

    # F6 tenant capability projection — the Catalog authorizes through it.
    register_projection_capabilities(kernel)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:read"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:write"))
        for tenant_id, principal_id in ((_TENANT_A, _USER_A), (_TENANT_B, _USER_B)):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    principal_type="USER",
                    enabled=True,
                    credential_hash=hasher.hash(_PASSWORD),
                    roles=[_ROLE],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    mcp_driver = connector_engine.mcp_driver
    assert mcp_driver is not None
    connector_engine.register_driver(mcp_driver)
    # A real, tenant-scoped secret behind a handle: the Catalog must never
    # surface either the handle or the resolved value.
    await security_engine.put_secret(_SECRET_HANDLE, _TENANT_A, _SECRET_VALUE)
    await security_engine.put_secret(_SECRET_HANDLE, _TENANT_B, _SECRET_VALUE)
    mcp_driver.set_secret_resolver(AsyncMock(return_value=_SECRET_VALUE))

    def _profile(profile_id: str, tenant_id: str, *, driver_id: str = "connector-mcp", active: bool = True):
        return ConnectorProfile(
            profile_id=profile_id,
            tenant_id=tenant_id,
            name=f"Profile {profile_id}",
            driver_id=driver_id,
            options={"endpoint_url": f"https://mcp.example.com/{profile_id}"},
            secret_handle=_SECRET_HANDLE,
            is_active=active,
        )

    profiles = {
        _PROFILE_A: (_profile(_PROFILE_A, _TENANT_A), [_TOOL_QUERY, _TOOL_EXPORT]),
        _PROFILE_A2: (_profile(_PROFILE_A2, _TENANT_A), [_TOOL_ARCHIVE]),
        _PROFILE_B: (_profile(_PROFILE_B, _TENANT_B), [_TOOL_QUERY]),
    }

    ssrf = AsyncMock(return_value=("mcp.example.com", "93.184.216.34", 443))
    transport = MagicMock()
    transport.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
    transport.__aexit__ = AsyncMock(return_value=None)

    # Patches stay active for the whole test: the Gateway's real invocation
    # path re-enters this same transport seam when it opens a runtime session.
    with (
        patch.object(mcp_driver, "validate_ssrf_and_pin_ip", ssrf),
        patch("kortex.engines.connector.drivers.mcp_driver.streamable_http_client", return_value=transport),
        patch("kortex.engines.connector.drivers.mcp_driver.ClientSession") as mock_session_cls,
    ):
        sessions = {pid: _mock_mcp_session(tools) for pid, (_p, tools) in profiles.items()}
        # Every runtime session the driver opens belongs to the profile it was
        # opened for; the driver opens at most one at a time here, so returning
        # the profile-A session by default and overriding per reconcile call is
        # sufficient and stays deterministic.
        mock_session_cls.return_value = sessions[_PROFILE_A]

        for profile_id, (profile, _tools) in profiles.items():
            await connector_engine.profile_manager.register_profile(profile)
            await mcp_driver.reconcile_tools(profile, sessions[profile_id])

        # A deactivated MCP profile owned by tenant A: registered but never
        # reconciled, exactly as production leaves a deactivated integration.
        await connector_engine.profile_manager.register_profile(_profile(_PROFILE_A_DISABLED, _TENANT_A, active=False))
        # A non-MCP profile owned by tenant A: the Catalog must not show it.
        await connector_engine.profile_manager.register_profile(
            ConnectorProfile(
                profile_id=_HTTP_PROFILE_A,
                tenant_id=_TENANT_A,
                name="Tenant A HTTP profile",
                driver_id="connector-http-rest",
                options={"base_url": "https://api.example.com"},
            )
        )

        environment = _Env(
            kernel=kernel,
            connector_engine=connector_engine,
            security_engine=security_engine,
            mcp_driver=mcp_driver,
        )

        # Pass-through spy on the REAL dispatcher: records every capability
        # name that actually crosses the execution boundary, without replacing
        # or short-circuiting any of its behaviour.
        real_dispatch = kernel._dispatcher.dispatch

        async def _recording_dispatch(request: CapabilityRequest) -> Any:
            environment.dispatched.append(request.capability_name)
            return await real_dispatch(request)

        with patch.object(kernel._dispatcher, "dispatch", _recording_dispatch):
            try:
                yield environment
            finally:
                if kernel.state == KernelState.RUNNING:
                    with contextlib.suppress(Exception):
                        await kernel.shutdown()
                await db_manager.disconnect()


# ---------------------------------------------------------------------------
# 1-4. Catalog discovery and metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_lists_only_the_callers_mcp_profiles(env: _Env) -> None:
    """Catalog profile discovery: MCP profiles of the caller's tenant, and nothing else."""
    token_a = await _token(env, _TENANT_A, _USER_A)
    profiles = await _invoke(env, CATALOG_PROFILE_LIST_CAPABILITY, token_a, _TENANT_A)

    listed = {p["profile_id"] for p in profiles}
    assert listed == {_PROFILE_A, _PROFILE_A2, _PROFILE_A_DISABLED}
    # Tenant B's MCP profile is absent, and so is tenant A's own non-MCP profile.
    assert _PROFILE_B not in listed
    assert _HTTP_PROFILE_A not in listed
    assert all(p["tenant_id"] == _TENANT_A for p in profiles)


@pytest.mark.asyncio
async def test_catalog_profile_list_honours_active_only(env: _Env) -> None:
    """`active_only` mirrors the existing connector profile-list convention."""
    token_a = await _token(env, _TENANT_A, _USER_A)
    profiles = await _invoke(env, CATALOG_PROFILE_LIST_CAPABILITY, token_a, _TENANT_A, active_only=True)

    listed = {p["profile_id"] for p in profiles}
    assert listed == {_PROFILE_A, _PROFILE_A2}
    assert _PROFILE_A_DISABLED not in listed


@pytest.mark.asyncio
async def test_catalog_profile_metadata(env: _Env) -> None:
    """Profile metadata is the projected, non-sensitive view."""
    token_a = await _token(env, _TENANT_A, _USER_A)
    profile = await _invoke(env, CATALOG_PROFILE_GET_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A)

    assert profile["profile_id"] == _PROFILE_A
    assert profile["tenant_id"] == _TENANT_A
    assert profile["driver_id"] == "connector-mcp"
    assert profile["is_active"] is True
    assert profile["options"]["endpoint_url"] == f"https://mcp.example.com/{_PROFILE_A}"


@pytest.mark.asyncio
async def test_catalog_lists_capabilities_of_an_authorized_profile(env: _Env) -> None:
    """Catalog capability discovery reads back RegistryEngine, scoped to one profile."""
    token_a = await _token(env, _TENANT_A, _USER_A)
    capabilities = await _invoke(env, CATALOG_CAPABILITY_LIST_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A)

    tools = {c["tool_name"] for c in capabilities}
    assert tools == {_TOOL_QUERY, _TOOL_EXPORT}
    assert all(c["profile_id"] == _PROFILE_A for c in capabilities)
    assert all(c["capability_name"] == mcp_capability_name(_PROFILE_A, c["tool_name"]) for c in capabilities)
    # A capability owned by the caller's *other* profile never appears here.
    assert _TOOL_ARCHIVE not in tools


@pytest.mark.asyncio
async def test_catalog_capability_metadata(env: _Env) -> None:
    """Capability metadata comes from the RegistryEngine descriptor, verbatim."""
    token_a = await _token(env, _TENANT_A, _USER_A)
    capability = await _invoke(
        env, CATALOG_CAPABILITY_GET_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A, tool_name=_TOOL_QUERY
    )

    descriptor = env.kernel.get_capability(mcp_capability_name(_PROFILE_A, _TOOL_QUERY))
    assert capability["capability_name"] == descriptor.name
    assert capability["description"] == descriptor.description == "Run a query against the warehouse"
    assert capability["parameters_schema"] == descriptor.parameters_schema
    assert capability["parameters_schema"]["properties"]["sql"]["type"] == "string"
    assert capability["requires_authentication"] is True


@pytest.mark.asyncio
async def test_catalog_reads_do_not_reconcile_or_mutate(env: _Env) -> None:
    """A Catalog read is a pure projection: no reconciliation, no registry mutation."""
    token_a = await _token(env, _TENANT_A, _USER_A)
    before = {c.name for c in env.kernel.list_capabilities()}

    with patch.object(env.mcp_driver, "reconcile_tools", AsyncMock()) as reconcile:
        await _invoke(env, CATALOG_PROFILE_LIST_CAPABILITY, token_a, _TENANT_A)
        await _invoke(env, CATALOG_PROFILE_GET_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A)
        await _invoke(env, CATALOG_CAPABILITY_LIST_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A)
        await _invoke(
            env, CATALOG_CAPABILITY_GET_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A, tool_name=_TOOL_QUERY
        )
        reconcile.assert_not_awaited()

    assert {c.name for c in env.kernel.list_capabilities()} == before
    # No runtime MCP session was opened by a read, either.
    assert _PROFILE_A not in env.mcp_driver._active_sessions


# ---------------------------------------------------------------------------
# 5-7. Tenant / profile isolation and unauthorized capability visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_isolation_is_symmetric(env: _Env) -> None:
    """Tenant A's profile is visible to A and invisible to B, and vice versa."""
    token_a = await _token(env, _TENANT_A, _USER_A)
    token_b = await _token(env, _TENANT_B, _USER_B)

    a_listed = {p["profile_id"] for p in await _invoke(env, CATALOG_PROFILE_LIST_CAPABILITY, token_a, _TENANT_A)}
    b_listed = {p["profile_id"] for p in await _invoke(env, CATALOG_PROFILE_LIST_CAPABILITY, token_b, _TENANT_B)}

    assert _PROFILE_A in a_listed and _PROFILE_A not in b_listed
    assert _PROFILE_B in b_listed and _PROFILE_B not in a_listed

    # Direct cross-tenant metadata reads are masked as "not found".
    with pytest.raises(ConnectorProfileNotFoundError):
        await _invoke(env, CATALOG_PROFILE_GET_CAPABILITY, token_b, _TENANT_B, profile_id=_PROFILE_A)
    with pytest.raises(ConnectorProfileNotFoundError):
        await _invoke(env, CATALOG_PROFILE_GET_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_B)


@pytest.mark.asyncio
async def test_cross_tenant_capability_listing_is_masked_as_not_found(env: _Env) -> None:
    """Tenant B cannot enumerate tenant A's MCP capabilities, not even their count."""
    token_b = await _token(env, _TENANT_B, _USER_B)

    with pytest.raises(ConnectorProfileNotFoundError):
        await _invoke(env, CATALOG_CAPABILITY_LIST_CAPABILITY, token_b, _TENANT_B, profile_id=_PROFILE_A)
    with pytest.raises(ConnectorProfileNotFoundError):
        await _invoke(
            env, CATALOG_CAPABILITY_GET_CAPABILITY, token_b, _TENANT_B, profile_id=_PROFILE_A, tool_name=_TOOL_QUERY
        )


@pytest.mark.asyncio
async def test_profile_isolation_within_one_tenant(env: _Env) -> None:
    """A tool of one profile is invisible through a sibling profile of the same tenant."""
    token_a = await _token(env, _TENANT_A, _USER_A)

    # `archive_dataset` really does exist for this tenant — on _PROFILE_A2.
    owned = await _invoke(
        env, CATALOG_CAPABILITY_GET_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A2, tool_name=_TOOL_ARCHIVE
    )
    assert owned["capability_name"] == mcp_capability_name(_PROFILE_A2, _TOOL_ARCHIVE)

    # Asking for it under the wrong profile is a miss, not a redirect.
    with pytest.raises(CapabilityNotFoundError):
        await _invoke(
            env, CATALOG_CAPABILITY_GET_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A, tool_name=_TOOL_ARCHIVE
        )


@pytest.mark.asyncio
async def test_non_mcp_profile_is_not_exposed_through_the_catalog(env: _Env) -> None:
    """The MCP Catalog is not an oracle for a tenant's non-MCP connector profiles."""
    token_a = await _token(env, _TENANT_A, _USER_A)

    with pytest.raises(ConnectorProfileNotFoundError):
        await _invoke(env, CATALOG_PROFILE_GET_CAPABILITY, token_a, _TENANT_A, profile_id=_HTTP_PROFILE_A)


# ---------------------------------------------------------------------------
# 8-12. Gateway authentication, authorization, and rejection cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_requires_authenticated_caller_context(env: _Env) -> None:
    """An unauthenticated dispatch never reaches the Gateway's routing logic."""
    from kortex.engines.security.exceptions import AuthenticationError

    with pytest.raises((AuthenticationError, ConnectorSecurityError)):
        await env.kernel.invoke_capability(
            CapabilityRequest(
                capability_name=GATEWAY_INVOKE_CAPABILITY,
                session_token=None,
                parameters={"profile_id": _PROFILE_A, "tool_name": _TOOL_QUERY, "arguments": {"sql": "SELECT 1"}},
                context={"resource_tenant_id": _TENANT_A},
            )
        )

    # And the Gateway itself fails closed when called with no context at all.
    gateway = env.connector_engine.mcp_gateway
    assert gateway is not None
    with pytest.raises(ConnectorSecurityError):
        await gateway.invoke(profile_id=_PROFILE_A, tool_name=_TOOL_QUERY, arguments={})


@pytest.mark.asyncio
async def test_gateway_ignores_a_caller_supplied_tenant_id(env: _Env) -> None:
    """`tenant_id` is not a Gateway parameter; supplying one cannot widen authority."""
    token_b = await _token(env, _TENANT_B, _USER_B)

    with pytest.raises(ConnectorProfileNotFoundError):
        await _invoke(
            env,
            GATEWAY_INVOKE_CAPABILITY,
            token_b,
            _TENANT_B,
            profile_id=_PROFILE_A,
            tool_name=_TOOL_QUERY,
            arguments={"sql": "SELECT * FROM tenant_a_secrets;"},
            tenant_id=_TENANT_A,
        )


@pytest.mark.asyncio
async def test_gateway_rejects_cross_tenant_profile(env: _Env) -> None:
    """Tenant A invoking tenant B's profile is masked as "not found"."""
    token_a = await _token(env, _TENANT_A, _USER_A)

    with pytest.raises(ConnectorProfileNotFoundError):
        await _invoke(
            env,
            GATEWAY_INVOKE_CAPABILITY,
            token_a,
            _TENANT_A,
            profile_id=_PROFILE_B,
            tool_name=_TOOL_QUERY,
            arguments={"sql": "SELECT 1"},
        )


@pytest.mark.asyncio
async def test_gateway_rejects_wrong_profile_tool_combination(env: _Env) -> None:
    """A real tool of the caller's own tenant is still rejected under the wrong profile."""
    token_a = await _token(env, _TENANT_A, _USER_A)

    with pytest.raises(CapabilityNotFoundError):
        await _invoke(
            env,
            GATEWAY_INVOKE_CAPABILITY,
            token_a,
            _TENANT_A,
            profile_id=_PROFILE_A,
            tool_name=_TOOL_ARCHIVE,
            arguments={"dataset": "sales"},
        )


@pytest.mark.asyncio
async def test_gateway_rejects_disabled_profile(env: _Env) -> None:
    """A deactivated MCP profile is refused, distinctly from "not found"."""
    token_a = await _token(env, _TENANT_A, _USER_A)

    with pytest.raises(ConnectorSecurityError, match="not active"):
        await _invoke(
            env,
            GATEWAY_INVOKE_CAPABILITY,
            token_a,
            _TENANT_A,
            profile_id=_PROFILE_A_DISABLED,
            tool_name=_TOOL_QUERY,
            arguments={"sql": "SELECT 1"},
        )


@pytest.mark.asyncio
async def test_gateway_rejects_nonexistent_capability(env: _Env) -> None:
    """An unknown tool produces a structured rejection, not a transport attempt."""
    token_a = await _token(env, _TENANT_A, _USER_A)

    with pytest.raises(CapabilityNotFoundError):
        await _invoke(
            env,
            GATEWAY_INVOKE_CAPABILITY,
            token_a,
            _TENANT_A,
            profile_id=_PROFILE_A,
            tool_name="no_such_tool",
            arguments={},
        )


@pytest.mark.asyncio
async def test_gateway_rejects_invalid_parameters(env: _Env) -> None:
    """Parameter validation happens before any authorization state is consulted."""
    token_a = await _token(env, _TENANT_A, _USER_A)

    with pytest.raises(ConnectorValidationError, match="may not contain"):
        await _invoke(
            env,
            GATEWAY_INVOKE_CAPABILITY,
            token_a,
            _TENANT_A,
            profile_id=_PROFILE_A,
            tool_name="other_profile.query_data",
            arguments={},
        )

    with pytest.raises(ConnectorValidationError, match="'arguments' must be a JSON object"):
        await _invoke(
            env,
            GATEWAY_INVOKE_CAPABILITY,
            token_a,
            _TENANT_A,
            profile_id=_PROFILE_A,
            tool_name=_TOOL_QUERY,
            arguments="not-an-object",
        )

    with pytest.raises(ConnectorValidationError, match="dispatcher-reserved"):
        await _invoke(
            env,
            GATEWAY_INVOKE_CAPABILITY,
            token_a,
            _TENANT_A,
            profile_id=_PROFILE_A,
            tool_name=_TOOL_QUERY,
            arguments={"sql": "SELECT 1", "principal": {"tenant_id": _TENANT_B}},
        )


@pytest.mark.asyncio
async def test_gateway_surfaces_underlying_mcp_error(env: _Env) -> None:
    """A driver-level MCP failure propagates, it is not swallowed."""
    from kortex.engines.connector.exceptions import DriverExecutionError

    token_a = await _token(env, _TENANT_A, _USER_A)

    with (
        patch.object(
            env.mcp_driver,
            "invoke_tool_with_retry",
            AsyncMock(side_effect=DriverExecutionError("remote MCP tool exploded")),
        ),
        pytest.raises(DriverExecutionError, match="remote MCP tool exploded"),
    ):
        await _invoke(
            env,
            GATEWAY_INVOKE_CAPABILITY,
            token_a,
            _TENANT_A,
            profile_id=_PROFILE_A,
            tool_name=_TOOL_QUERY,
            arguments={"sql": "SELECT 1"},
        )


# ---------------------------------------------------------------------------
# 14. Secret / credential protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_never_leaks_credential_material(env: _Env) -> None:
    """No access token, refresh token, secret handle, or secret value is reachable."""
    token_a = await _token(env, _TENANT_A, _USER_A)

    payloads = [
        await _invoke(env, CATALOG_PROFILE_LIST_CAPABILITY, token_a, _TENANT_A),
        await _invoke(env, CATALOG_PROFILE_GET_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A),
        await _invoke(env, CATALOG_CAPABILITY_LIST_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A),
        await _invoke(
            env, CATALOG_CAPABILITY_GET_CAPABILITY, token_a, _TENANT_A, profile_id=_PROFILE_A, tool_name=_TOOL_QUERY
        ),
    ]

    blob = repr(payloads)
    for forbidden in (_SECRET_VALUE, _SECRET_HANDLE, "secret_handle", "access_token", "refresh_token"):
        assert forbidden not in blob, f"Catalog leaked {forbidden!r}"

    # The profile really does carry a secret handle — the Catalog simply never
    # projects it, so this assertion is proving redaction, not absence of data.
    stored = await env.connector_engine.profile_manager.get_profile(_PROFILE_A, tenant_id=_TENANT_A)
    assert stored.secret_handle == _SECRET_HANDLE


# ---------------------------------------------------------------------------
# 13, 15, 16 + mandatory E2E: the full authorized path through the Gateway
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_catalog_discovery_then_gateway_invocation(env: _Env) -> None:
    """MANDATORY E2E: discovery -> Gateway -> authorization -> canonical capability
    -> existing CapabilityDispatcher -> McpConnectorDriver -> MCP result.

    Entry is exclusively through the Gateway/Catalog capabilities. Nothing here
    calls `McpConnectorDriver` or `ConnectorDriverRegistry` directly, and no
    alternate execution path is constructed.
    """
    token_a = await _token(env, _TENANT_A, _USER_A)
    env.dispatched.clear()

    # 1. Catalog profile discovery — the caller learns which profile to use.
    profiles = await _invoke(env, CATALOG_PROFILE_LIST_CAPABILITY, token_a, _TENANT_A, active_only=True)
    discovered = next(p for p in profiles if p["profile_id"] == _PROFILE_A)
    assert discovered["is_active"] is True

    # 2. Catalog capability discovery — the caller learns which tool to call.
    capabilities = await _invoke(
        env, CATALOG_CAPABILITY_LIST_CAPABILITY, token_a, _TENANT_A, profile_id=discovered["profile_id"]
    )
    tool = next(c for c in capabilities if c["tool_name"] == _TOOL_QUERY)
    canonical = mcp_capability_name(discovered["profile_id"], tool["tool_name"])
    assert tool["capability_name"] == canonical

    # 3. Gateway invocation using only what discovery returned.
    result = await _invoke(
        env,
        GATEWAY_INVOKE_CAPABILITY,
        token_a,
        _TENANT_A,
        profile_id=discovered["profile_id"],
        tool_name=tool["tool_name"],
        arguments={"sql": "SELECT * FROM sales;"},
    )

    # 4. The structured MCP result reaches the caller unchanged.
    assert result["is_error"] is False
    assert result["content"][0]["text"] == "42 rows returned successfully."
    assert result["tool_name"] == _TOOL_QUERY
    assert result["profile_id"] == _PROFILE_A

    # 5. EXECUTION-BOUNDARY PROOF: the real `CapabilityDispatcher` was entered
    #    twice — once for the Gateway capability, and once for the canonical
    #    MCP capability the Gateway routed to. The second entry is what proves
    #    the Gateway did not execute anything itself.
    assert env.dispatched == [
        CATALOG_PROFILE_LIST_CAPABILITY,
        CATALOG_CAPABILITY_LIST_CAPABILITY,
        GATEWAY_INVOKE_CAPABILITY,
        canonical,
    ]

    # 6. The driver really was reached, through that dispatch — a runtime MCP
    #    session now exists for the profile, created by the driver's own
    #    lifecycle rather than by the Gateway.
    assert _PROFILE_A in env.mcp_driver._active_sessions


@pytest.mark.asyncio
async def test_gateway_routes_through_the_existing_dispatcher_not_the_driver(env: _Env) -> None:
    """The Gateway reaches `McpConnectorDriver` only *via* the canonical capability.

    Proven two ways, neither of which mocks the Gateway's own internals:
      - `BaseConnectorDriver.execute_action` — the driver's direct entry point
        a second execution engine would have used — is never called;
      - the dispatcher records the canonical capability, and the driver's
        registered handler is what actually invokes the tool.
    """
    token_a = await _token(env, _TENANT_A, _USER_A)
    env.dispatched.clear()

    with (
        patch.object(env.mcp_driver, "execute_action", AsyncMock()) as direct_driver_entry,
        patch.object(
            env.mcp_driver, "invoke_tool_with_retry", wraps=env.mcp_driver.invoke_tool_with_retry
        ) as tool_invocation,
    ):
        result = await _invoke(
            env,
            GATEWAY_INVOKE_CAPABILITY,
            token_a,
            _TENANT_A,
            profile_id=_PROFILE_A,
            tool_name=_TOOL_EXPORT,
            arguments={"limit": 10},
        )

    assert result["is_error"] is False
    direct_driver_entry.assert_not_called()

    # The driver was reached with exactly the profile/tenant/tool the Gateway
    # authorized — never a caller-supplied tenant.
    tool_invocation.assert_awaited_once()
    call_kwargs = tool_invocation.await_args.kwargs
    assert call_kwargs["profile_id"] == _PROFILE_A
    assert call_kwargs["tenant_id"] == _TENANT_A
    assert call_kwargs["tool_name"] == _TOOL_EXPORT
    assert call_kwargs["arguments"] == {"limit": 10}

    assert env.dispatched == [GATEWAY_INVOKE_CAPABILITY, mcp_capability_name(_PROFILE_A, _TOOL_EXPORT)]


@pytest.mark.asyncio
async def test_gateway_registration_is_present_and_correctly_classified(env: _Env) -> None:
    """All five capabilities are registered with the expected contract."""
    catalog_names = (
        CATALOG_PROFILE_LIST_CAPABILITY,
        CATALOG_PROFILE_GET_CAPABILITY,
        CATALOG_CAPABILITY_LIST_CAPABILITY,
        CATALOG_CAPABILITY_GET_CAPABILITY,
    )
    for name in catalog_names:
        descriptor = env.kernel.get_capability(name)
        assert descriptor.provider == "connector"
        assert descriptor.requires_authentication is True
        assert descriptor.requires_execution_context is True
        assert descriptor.required_permissions == ["connector:read"]
        assert descriptor.is_read_only is True
        assert descriptor.is_idempotent is True
        # Static front door, not a per-profile dynamic capability.
        assert descriptor.owner_id is None

    gateway = env.kernel.get_capability(GATEWAY_INVOKE_CAPABILITY)
    assert gateway.required_permissions == ["connector:execute"]
    assert gateway.requires_execution_context is True
    assert gateway.is_read_only is False
    assert gateway.is_idempotent is False
    assert set(gateway.parameters_schema["required"]) == {"profile_id", "tool_name"}
    assert "tenant_id" not in gateway.parameters_schema["properties"]
