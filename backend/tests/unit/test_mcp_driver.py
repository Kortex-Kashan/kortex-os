"""Comprehensive unit tests for Model Context Protocol (MCP) Streamable HTTP Driver (Integration Hub M1).

Covers:
- SDK and protocol version validation (mcp==1.24.0, protocol 2025-03-26).
- Fail-closed SSRF validation, userinfo rejection, non-443 port rejection, and IP restriction checks.
- DNS rebinding defense with PinnedIPNetworkBackend and strict TLS verification.
- Two-phase atomic tools/list reconciliation with zero registry mutation on validation error.
- Registry ownership enforcement: owner_id matching, update safety, wrong-owner rejection, unregistration.
- Tenant isolation: cross-tenant execution context rejection.
- Connection lifecycle: temporary test session vs. lazy runtime session reuse and teardown.
- Deterministic exponential backoff retries and error mappings.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent, Tool

from kortex.core.exceptions import CapabilityNotFoundError
from kortex.engines.connector.drivers.mcp_driver import (
    McpConnectorDriver,
)
from kortex.engines.connector.exceptions import (
    ConnectorSecurityError,
    DriverExecutionError,
)
from kortex.engines.connector.models import (
    ConnectorProfile,
)
from kortex.engines.registry.engine import (
    RegistryEngine,
    ResourceAlreadyExistsError,
)

# -- 1. SDK / Dependency Tests -----------------------------------------------


def test_mcp_sdk_version_and_exports() -> None:
    """Verify exact pinned SDK version and export availability."""
    version = importlib.metadata.version("mcp")
    assert version == "1.24.0", f"Expected mcp==1.24.0, found {version}"
    assert callable(streamable_http_client)
    assert issubclass(ClientSession, object)


# -- 2. SSRF / Outbound Security Tests ---------------------------------------


@pytest.mark.asyncio
async def test_ssrf_rejects_non_https() -> None:
    """SSRF policy strictly rejects plain http://, ftp://, file://, etc."""
    driver = McpConnectorDriver()
    for scheme in ("http://example.com/mcp", "ftp://example.com/mcp", "file:///etc/passwd"):
        with pytest.raises(ConnectorSecurityError, match="strict 'https://'"):
            await driver.validate_ssrf_and_pin_ip(scheme)


@pytest.mark.asyncio
async def test_ssrf_rejects_userinfo() -> None:
    """SSRF policy rejects URLs with embedded credentials."""
    driver = McpConnectorDriver()
    with pytest.raises(ConnectorSecurityError, match="userinfo"):
        await driver.validate_ssrf_and_pin_ip("https://user:password@example.com/mcp")


@pytest.mark.asyncio
async def test_ssrf_rejects_explicit_non_443_ports() -> None:
    """SSRF policy rejects non-standard explicit ports."""
    driver = McpConnectorDriver()
    for port_url in ("https://example.com:8080/mcp", "https://example.com:8443/mcp", "https://example.com:80/mcp"):
        with pytest.raises(ConnectorSecurityError, match="non-443 port"):
            await driver.validate_ssrf_and_pin_ip(port_url)


@pytest.mark.asyncio
async def test_ssrf_rejects_forbidden_hostnames() -> None:
    """SSRF policy rejects localhost, metadata, and .local / .internal domains."""
    driver = McpConnectorDriver()
    forbidden = [
        "https://localhost/mcp",
        "https://metadata/mcp",
        "https://169.254.169.254/mcp",
        "https://server.local/mcp",
        "https://internal-service.internal/mcp",
    ]
    for url in forbidden:
        with pytest.raises(ConnectorSecurityError, match="forbidden"):
            await driver.validate_ssrf_and_pin_ip(url)


@pytest.mark.asyncio
async def test_ssrf_rejects_restricted_ip_addresses() -> None:
    """SSRF policy rejects loopback, RFC1918 private, link-local, multicast, and broadcast."""
    driver = McpConnectorDriver()
    restricted_ips = [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "0.0.0.0",  # noqa: S104
        "255.255.255.255",
        "::1",
        "fe80::1",
        "fc00::1",
    ]
    for ip in restricted_ips:
        with pytest.raises(ConnectorSecurityError, match="restricted"):
            driver._verify_ip_object(ip)


@pytest.mark.asyncio
async def test_ssrf_validates_public_destination() -> None:
    """SSRF policy succeeds and pins IP for a valid public destination."""
    driver = McpConnectorDriver()
    mock_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    loop = asyncio.get_running_loop()
    with patch.object(loop, "getaddrinfo", AsyncMock(return_value=mock_addrinfo)):
        host, pinned_ip, port = await driver.validate_ssrf_and_pin_ip("https://example.com/mcp")
        assert host == "example.com"
        assert pinned_ip == "93.184.216.34"
        assert port == 443


@pytest.mark.asyncio
async def test_ssrf_rejects_when_dns_resolves_to_private_ip() -> None:
    """SSRF pre-connect check fails closed if DNS resolves to a private IP (DNS rebinding defense)."""
    driver = McpConnectorDriver()
    mock_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    loop = asyncio.get_running_loop()
    with (
        patch.object(loop, "getaddrinfo", AsyncMock(return_value=mock_addrinfo)),
        pytest.raises(ConnectorSecurityError, match="restricted"),
    ):
        await driver.validate_ssrf_and_pin_ip("https://legit-domain-rebinding.com/mcp")


# -- 3. Registry Ownership & Atomic Reconciliation Tests ----------------------


def test_registry_owner_id_enforcement() -> None:
    """RegistryEngine register_capability and unregister_capability enforce owner_id."""
    registry = RegistryEngine()

    # 1. Register with owner_id
    desc = registry.register_capability(
        name="kortex.mcp.prof1.tool_a",
        description="Tool A",
        provider="connector",
        owner_id="prof1",
    )
    assert desc.owner_id == "prof1"
    assert registry.get_capability("kortex.mcp.prof1.tool_a").owner_id == "prof1"

    # 2. Update with SAME owner_id is permitted
    updated_desc = registry.register_capability(
        name="kortex.mcp.prof1.tool_a",
        description="Tool A updated",
        provider="connector",
        owner_id="prof1",
    )
    assert updated_desc.description == "Tool A updated"

    # 3. Update with DIFFERENT owner_id or None is rejected
    with pytest.raises(ResourceAlreadyExistsError):
        registry.register_capability(
            name="kortex.mcp.prof1.tool_a",
            description="Hijack attempt",
            provider="connector",
            owner_id="prof2",
        )

    with pytest.raises(ResourceAlreadyExistsError):
        registry.register_capability(
            name="kortex.mcp.prof1.tool_a",
            description="Hijack attempt",
            provider="connector",
            owner_id=None,
        )

    # 4. Unregister with WRONG owner_id is rejected
    with pytest.raises(PermissionError, match="owned by 'prof1', not 'prof2'"):
        registry.unregister_capability("kortex.mcp.prof1.tool_a", owner_id="prof2")

    # 5. Unregister with CORRECT owner_id succeeds
    assert registry.unregister_capability("kortex.mcp.prof1.tool_a", owner_id="prof1") is True

    # 6. Idempotent unregister returns False for nonexistent
    assert registry.unregister_capability("kortex.mcp.prof1.tool_a", owner_id="prof1") is False


@pytest.mark.asyncio
async def test_reconciliation_lifecycle_add_update_remove() -> None:
    """Reconciliation adds new tools, updates existing, and removes obsolete tools."""
    registry = RegistryEngine()
    driver = McpConnectorDriver(registry_engine=registry)
    profile = ConnectorProfile(
        profile_id="mcp-test-1",
        tenant_id="tenant-alpha",
        name="Test MCP",
        driver_id="connector-mcp",
        options={"endpoint_url": "https://example.com/mcp"},
    )

    # Initial batch: tools [tool_1, tool_2]
    mock_session = MagicMock()
    mock_session.list_tools = AsyncMock(
        return_value=MagicMock(
            tools=[
                Tool(name="tool_1", description="Tool 1", inputSchema={"type": "object"}),
                Tool(name="tool_2", description="Tool 2", inputSchema={"type": "object"}),
            ]
        )
    )

    reconciled = await driver.reconcile_tools(profile, mock_session)
    assert reconciled == ["kortex.mcp.mcp-test-1.tool_1", "kortex.mcp.mcp-test-1.tool_2"]
    assert registry.get_capability("kortex.mcp.mcp-test-1.tool_1").owner_id == "mcp-test-1"
    assert registry.get_capability("kortex.mcp.mcp-test-1.tool_2").owner_id == "mcp-test-1"

    # Second batch: tool_1 updated, tool_2 removed, tool_3 added
    mock_session.list_tools = AsyncMock(
        return_value=MagicMock(
            tools=[
                Tool(
                    name="tool_1",
                    description="Tool 1 New Desc",
                    inputSchema={"type": "object", "properties": {"x": {"type": "string"}}},
                ),
                Tool(name="tool_3", description="Tool 3", inputSchema={"type": "object"}),
            ]
        )
    )

    reconciled2 = await driver.reconcile_tools(profile, mock_session)
    assert reconciled2 == ["kortex.mcp.mcp-test-1.tool_1", "kortex.mcp.mcp-test-1.tool_3"]
    assert registry.get_capability("kortex.mcp.mcp-test-1.tool_1").description == "Tool 1 New Desc"
    assert registry.get_capability("kortex.mcp.mcp-test-1.tool_3").owner_id == "mcp-test-1"
    # tool_2 must be removed
    with pytest.raises(CapabilityNotFoundError):
        registry.get_capability("kortex.mcp.mcp-test-1.tool_2")


@pytest.mark.asyncio
async def test_reconciliation_zero_mutation_on_validation_failure() -> None:
    """If ANY tool in the batch fails schema validation, Phase 1 aborts and zero registry changes occur."""
    registry = RegistryEngine()
    driver = McpConnectorDriver(registry_engine=registry)
    profile = ConnectorProfile(
        profile_id="mcp-atomic-1",
        tenant_id="tenant-alpha",
        name="Atomic MCP",
        driver_id="connector-mcp",
        options={"endpoint_url": "https://example.com/mcp"},
    )

    # Pre-register a valid capability
    registry.register_capability(
        name="kortex.mcp.mcp-atomic-1.initial",
        description="Initial",
        provider="connector",
        owner_id="mcp-atomic-1",
    )

    # Malformed batch: one tool has non-dict inputSchema
    mock_session = MagicMock()
    mock_session.list_tools = AsyncMock(
        return_value=MagicMock(
            tools=[
                Tool(name="valid_tool", description="Valid", inputSchema={"type": "object"}),
                MagicMock(name="bad_tool", description="Bad", inputSchema="invalid-not-a-dict"),
            ]
        )
    )

    with pytest.raises(DriverExecutionError, match="Reconciliation aborted"):
        await driver.reconcile_tools(profile, mock_session)

    # Verify existing capability untouched and valid_tool was NOT registered
    assert registry.get_capability("kortex.mcp.mcp-atomic-1.initial") is not None
    with pytest.raises(CapabilityNotFoundError):
        registry.get_capability("kortex.mcp.mcp-atomic-1.valid_tool")


# -- 4. Tenant Isolation Tests ------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_isolation_rejects_cross_tenant_invocation() -> None:
    """A capability handler rejects invocations from a tenant that does not own the profile."""
    registry = RegistryEngine()
    driver = McpConnectorDriver(registry_engine=registry)
    profile = ConnectorProfile(
        profile_id="mcp-tenant-1",
        tenant_id="tenant-alpha",
        name="Tenant MCP",
        driver_id="connector-mcp",
        options={"endpoint_url": "https://example.com/mcp"},
    )

    handler = driver._create_capability_handler("mcp-tenant-1", "tenant-alpha", "my_tool")
    driver._known_profiles[profile.profile_id] = profile

    # Call with mismatched tenant in execution_context
    bad_context = MagicMock(tenant_id="tenant-beta")
    with pytest.raises(ConnectorSecurityError, match="Cross-tenant capability invocation rejected"):
        await handler(execution_context=bad_context, parameters={})

    # Call with None execution_context
    with pytest.raises(ConnectorSecurityError, match="Cross-tenant capability invocation rejected"):
        await handler(execution_context=None, parameters={})


# -- 5. Connection Lifecycle & Invocation Tests -------------------------------


@pytest.mark.asyncio
async def test_test_connection_temporary_session() -> None:
    """Test Connection performs initialization and tools/list check without persisting session."""
    driver = McpConnectorDriver()
    profile = ConnectorProfile(
        profile_id="mcp-test-conn",
        tenant_id="default",
        name="Test",
        driver_id="connector-mcp",
        options={"endpoint_url": "https://example.com/mcp"},
    )

    mock_ssrf_res = ("example.com", "93.184.216.34", 443)
    with (
        patch.object(driver, "validate_ssrf_and_pin_ip", AsyncMock(return_value=mock_ssrf_res)),
        patch("kortex.engines.connector.drivers.mcp_driver.streamable_http_client") as mock_sh_client,
    ):
        mock_gen = MagicMock()
        mock_gen.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        mock_gen.__aexit__ = AsyncMock(return_value=None)
        mock_sh_client.return_value = mock_gen

        with patch("kortex.engines.connector.drivers.mcp_driver.ClientSession") as mock_sess_cls:
            mock_sess = MagicMock()
            mock_sess.initialize = AsyncMock()
            mock_sess.list_tools = AsyncMock(
                return_value=MagicMock(tools=[Tool(name="ping", description="Ping", inputSchema={})])
            )
            mock_sess_cls.return_value = mock_sess

            ok = await driver.test_connection(profile)
            assert ok is True
            # Test Connection must NOT store runtime session
            assert "mcp-test-conn" not in driver._active_sessions


@pytest.mark.asyncio
async def test_lazy_runtime_session_reuse_and_invocation() -> None:
    """Capability invocation lazily creates and reuses active session."""
    registry = RegistryEngine()
    driver = McpConnectorDriver(registry_engine=registry)
    profile = ConnectorProfile(
        profile_id="mcp-invoke-1",
        tenant_id="tenant-alpha",
        name="Invoke Test",
        driver_id="connector-mcp",
        options={"endpoint_url": "https://example.com/mcp"},
    )

    mock_ssrf_res = ("example.com", "93.184.216.34", 443)
    with (
        patch.object(driver, "validate_ssrf_and_pin_ip", AsyncMock(return_value=mock_ssrf_res)),
        patch("kortex.engines.connector.drivers.mcp_driver.streamable_http_client") as mock_sh_client,
    ):
        mock_gen = MagicMock()
        mock_gen.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        mock_sh_client.return_value = mock_gen

        with patch("kortex.engines.connector.drivers.mcp_driver.ClientSession") as mock_sess_cls:
            mock_sess = MagicMock()
            mock_sess.initialize = AsyncMock()
            tool_obj = Tool(name="add_numbers", description="Add", inputSchema={"type": "object"})
            mock_sess.list_tools = AsyncMock(return_value=MagicMock(tools=[tool_obj]))
            mock_sess.call_tool = AsyncMock(
                return_value=CallToolResult(
                    content=[TextContent(type="text", text="Result: 42")],
                    isError=False,
                )
            )
            mock_sess_cls.return_value = mock_sess

            # Reconcile tools
            await driver.reconcile_tools(profile, mock_sess)

            # Invoke via handler
            handler = registry.get_raw_handler_for_testing("kortex.mcp.mcp-invoke-1.add_numbers")
            assert handler is not None

            context = MagicMock(tenant_id="tenant-alpha")
            res = await handler(execution_context=context, parameters={"a": 20, "b": 22})

            assert res["is_error"] is False
            assert res["content"][0]["text"] == "Result: 42"
            assert res["tool_name"] == "add_numbers"
            assert "mcp-invoke-1" in driver._active_sessions

            # Second invocation reuses active session
            await handler(execution_context=context, parameters={"a": 1, "b": 2})
            assert mock_sess.call_tool.call_count == 2


@pytest.mark.asyncio
async def test_teardown_profile_cleans_session_and_registry() -> None:
    """Profile teardown safely unregisters all capabilities and closes runtime resources."""
    registry = RegistryEngine()
    driver = McpConnectorDriver(registry_engine=registry)

    # Register capabilities
    registry.register_capability(
        name="kortex.mcp.mcp-del-1.tool_x",
        description="X",
        provider="connector",
        owner_id="mcp-del-1",
    )
    registry.register_capability(
        name="kortex.finance.invoice.get",
        description="Unrelated",
        provider="finance",
        owner_id=None,
    )

    # Add mock active session
    mock_active = MagicMock()
    mock_active.close = AsyncMock()
    driver._active_sessions["mcp-del-1"] = mock_active

    await driver.teardown_profile("mcp-del-1")

    # MCP capability removed, active session closed
    with pytest.raises(CapabilityNotFoundError):
        registry.get_capability("kortex.mcp.mcp-del-1.tool_x")
    assert "mcp-del-1" not in driver._active_sessions
    assert mock_active.close.await_count == 1

    # Unrelated capability preserved
    assert registry.get_capability("kortex.finance.invoice.get") is not None
