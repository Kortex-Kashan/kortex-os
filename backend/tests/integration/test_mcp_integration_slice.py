"""Vertical slice integration test for Integration Hub M1: MCP Foundation + Capability Projection Bridge.

Verifies end-to-end:
Tenant ConnectorProfile
  -> SecretStore
  -> MCP Streamable HTTP Client
  -> MCP initialize
  -> tools/list
  -> MCP capability descriptors
  -> RegistryEngine
  -> CapabilityProjection / F6
  -> CapabilityDispatcher
  -> MCP call_tool
  -> Structured Result
  -> Profile deletion teardown & capability cleanup
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from argon2 import PasswordHasher
from mcp.types import CallToolResult, TextContent, Tool
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.capability_projection import register_projection_capabilities
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import CapabilityNotFoundError
from kortex.core.kernel import Kernel
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import ConnectorSecurityError
from kortex.engines.connector.models import ConnectorProfile
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, PrincipalType, SecurityPrincipal
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32


async def _seed_user(
    data_store: Any,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("test-credential")

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=True,
                credential_hash=credential_hash,
                roles=roles or [],
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await data_store.execute_in_transaction(_action)


@pytest.mark.asyncio
async def test_mcp_vertical_slice_end_to_end(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end vertical slice test verifying profile lifecycle, projection, and dispatch."""
    db_file = tmp_path / f"test_mcp_{uuid.uuid4().hex[:8]}.db"
    storage_dir = tmp_path / f"storage_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(storage_dir))

    # 1. Initialize Kernel and Core Engines
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(storage_dir))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    registry_engine = kernel.get_engine("registry")
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)

    # Register F6 tenant-scoped capability projection
    register_projection_capabilities(kernel)

    await kernel.boot()

    # Verify MCP driver is wired
    mcp_driver = connector_engine.mcp_driver
    assert mcp_driver is not None
    connector_engine.register_driver(mcp_driver)

    # Wire mock secret resolver
    mock_secret_resolver = AsyncMock(return_value="mcp-token-secret-xyz")
    mcp_driver.set_secret_resolver(mock_secret_resolver)

    profile_id = "mcp-slice-profile-1"
    tenant_id_a = "tenant-corp-a"
    tenant_id_b = "tenant-corp-b"

    profile = ConnectorProfile(
        profile_id=profile_id,
        tenant_id=tenant_id_a,
        name="Corp A Remote Analytics",
        driver_id="connector-mcp",
        options={"endpoint_url": "https://analytics.example.com/mcp"},
        secret_handle="secret://tenants/tenant-corp-a/mcp-key",
    )

    # 2. Test Connection (Validation Only - No persistent session)
    mock_ssrf_res = ("analytics.example.com", "93.184.216.34", 443)
    with (
        patch.object(mcp_driver, "validate_ssrf_and_pin_ip", AsyncMock(return_value=mock_ssrf_res)),
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
                return_value=MagicMock(
                    tools=[
                        Tool(
                            name="query_data",
                            description="Run query against warehouse",
                            inputSchema={"type": "object", "properties": {"sql": {"type": "string"}}},
                        ),
                        Tool(
                            name="export_csv",
                            description="Export dataset to CSV format",
                            inputSchema={"type": "object", "properties": {"limit": {"type": "integer"}}},
                        ),
                    ]
                )
            )
            mock_sess.call_tool = AsyncMock(
                return_value=CallToolResult(
                    content=[TextContent(type="text", text="42 rows returned successfully.")],
                    isError=False,
                )
            )
            mock_sess_cls.return_value = mock_sess

            # Test connection
            tested = await mcp_driver.test_connection(profile)
            assert tested is True
            assert profile_id not in mcp_driver._active_sessions

            # 3. Save / Register ConnectorProfile in ConnectorEngine
            await connector_engine.register_profile(profile)

            # 4. Remote tools/list reconciliation
            reconciled = await mcp_driver.reconcile_tools(profile, mock_sess)
            assert f"kortex.mcp.{profile_id}.query_data" in reconciled
            assert f"kortex.mcp.{profile_id}.export_csv" in reconciled

            # 5. Check RegistryEngine capability descriptors and ownership
            desc = registry_engine.get_capability(f"kortex.mcp.{profile_id}.query_data")
            assert desc.owner_id == profile_id
            assert desc.description == "Run query against warehouse"
            assert desc.requires_execution_context is True

            # 6. Issue tokens for Tenant A and Tenant B
            await _seed_user(storage_engine.data, tenant_id_a, "user-corp-a-1")
            await _seed_user(storage_engine.data, tenant_id_b, "user-corp-b-1")

            principal_a = SecurityPrincipal(
                principal_id="user-corp-a-1",
                principal_type=PrincipalType.USER,
                tenant_id=tenant_id_a,
                roles=[],
                attributes={"clearance_level": "INTERNAL"},
            )
            token_a = await security_engine.authentication_manager.issue_token(principal_a)

            principal_b = SecurityPrincipal(
                principal_id="user-corp-b-1",
                principal_type=PrincipalType.USER,
                tenant_id=tenant_id_b,
                roles=[],
                attributes={"clearance_level": "INTERNAL"},
            )
            token_b = await security_engine.authentication_manager.issue_token(principal_b)

            # F6 Tenant Capability Projection for Tenant A
            req_project_a = CapabilityRequest(
                capability_name="kortex.system.capability.project",
                session_token=token_a,
                context={"resource_tenant_id": tenant_id_a},
                parameters={"keyword": "warehouse"},
            )
            projected = await kernel.invoke_capability(req_project_a)
            projected_names = [c["name"] for c in projected]
            assert f"kortex.mcp.{profile_id}.query_data" in projected_names

            # 7. CapabilityDispatcher Invocation for Tenant A
            req_invoke_a = CapabilityRequest(
                capability_name=f"kortex.mcp.{profile_id}.query_data",
                session_token=token_a,
                context={"resource_tenant_id": tenant_id_a},
                parameters={"sql": "SELECT * FROM sales;"},
            )
            result = await kernel.invoke_capability(req_invoke_a)
            assert result["is_error"] is False
            assert result["content"][0]["text"] == "42 rows returned successfully."
            assert result["tool_name"] == "query_data"
            assert result["profile_id"] == profile_id

            # Active runtime session is now cached in memory
            assert profile_id in mcp_driver._active_sessions

            # Invoking Tenant A's MCP capability with Tenant B's credentials must fail closed
            req_invoke_b = CapabilityRequest(
                capability_name=f"kortex.mcp.{profile_id}.query_data",
                session_token=token_b,
                context={"resource_tenant_id": tenant_id_b},
                parameters={"sql": "SELECT * FROM secret_corp_a_table;"},
            )
            with pytest.raises(ConnectorSecurityError, match="Cross-tenant capability invocation rejected"):
                await kernel.invoke_capability(req_invoke_b)

            # 8. Profile Deletion / Teardown
            await connector_engine.delete_profile(profile_id, principal=principal_a)

            # Assert runtime session is closed and removed
            assert profile_id not in mcp_driver._active_sessions

            # Assert capabilities are removed from RegistryEngine
            with pytest.raises(CapabilityNotFoundError):
                registry_engine.get_capability(f"kortex.mcp.{profile_id}.query_data")
            with pytest.raises(CapabilityNotFoundError):
                registry_engine.get_capability(f"kortex.mcp.{profile_id}.export_csv")

    await kernel.shutdown()
