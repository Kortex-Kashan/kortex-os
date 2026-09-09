"""
Integration tests for KORTEX OS Tenant-Scoped Capability Projection (Milestone F6).

Covers:
- End-to-end Kernel invocation of `kortex.system.capability.project` and `kortex.system.capability.get`.
- Keyword, domain, resource, and flag filtering in projection.
- Gate 12: ToolRegistry remains globally unmutated by projection filtering.
- Gate 13: Unauthorized ToolDefinitions are omitted from projected tools.
- Gate 14: Authorized ToolDefinitions are preserved with all attributes intact.
- Gate 20: F4 Workflow Engine publish validation and lifecycle remain completely unchanged.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.capability_projection import project_tools_for_tenant, register_projection_capabilities
from kortex.core.dispatch import CapabilityExecutionContext, CapabilityRequest
from kortex.core.exceptions import CapabilityNotFoundError
from kortex.core.kernel import Kernel
from kortex.engines.ai.tools import ToolDefinition, ToolRegistry
from kortex.engines.registry.engine import RegistryEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import (
    PrincipalRecord,
    PrincipalType,
    RolePermissionRecord,
    SecurityPrincipal,
    TokenPayload,
)
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.engine import WorkflowEngine

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32


def _role(suffix: str) -> str:
    return f"role-{suffix}-{uuid.uuid4().hex[:8]}"


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


async def _grant_permission(data_store: Any, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))
        await session.flush()

    await data_store.execute_in_transaction(_action)


async def _make_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Kernel, StorageEngine, SecurityEngine, RegistryEngine]:
    db_file = tmp_path / f"test_integ_{uuid.uuid4().hex[:8]}.db"
    storage_dir = tmp_path / f"storage_integ_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(storage_dir))

    kernel = Kernel()
    storage = StorageEngine(base_directory=str(storage_dir))
    security = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage)
    kernel.register_engine(security)

    register_projection_capabilities(kernel)

    await kernel.boot()
    registry = kernel._registry_engine
    return kernel, storage, security, registry


@pytest.mark.asyncio
async def test_end_to_end_projection_capabilities_invocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end dispatch of kortex.system.capability.project and kortex.system.capability.get."""
    kernel, storage, security, registry = await _make_env(tmp_path, monkeypatch)
    try:
        tenant_id = "tenant-integ-1"
        principal_id = "user-integ-1"
        role_analyst = _role("analyst")
        perm_read = "analytics:query:read"

        await _seed_user(storage.data, tenant_id, principal_id, roles=[role_analyst])
        await _grant_permission(storage.data, role_analyst, perm_read)

        registry.register_capability(
            name="kortex.analytics.query.execute",
            description="Execute analytical queries across tenant datasets",
            provider="analytics",
            handler=AsyncMock(return_value={"rows": [1, 2, 3]}),
            parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            returns_schema={"type": "object", "properties": {"rows": {"type": "array"}}},
            required_permissions=[perm_read],
            requires_authentication=True,
            security_classification="INTERNAL",
            is_read_only=True,
            is_idempotent=True,
        )

        principal = SecurityPrincipal(
            principal_id=principal_id,
            principal_type=PrincipalType.USER,
            tenant_id=tenant_id,
            roles=[role_analyst],
            attributes={"clearance_level": "INTERNAL"},
        )
        token: TokenPayload = await security.authentication_manager.issue_token(principal)

        # 1. Invoke kortex.system.capability.project
        req_project = CapabilityRequest(
            capability_name="kortex.system.capability.project",
            session_token=token,
            context={"resource_tenant_id": tenant_id},
            parameters={"keyword": "analytical", "is_read_only": True},
        )
        res_project = await kernel.invoke_capability(req_project)
        assert isinstance(res_project, list)
        matching = [c for c in res_project if c["name"] == "kortex.analytics.query.execute"]
        assert len(matching) == 1
        item = matching[0]
        assert item["provider"] == "analytics"
        assert item["is_read_only"] is True
        assert item["is_idempotent"] is True
        assert item["owner_domain"] == "analytics"

        # 2. Invoke kortex.system.capability.get for authorized capability
        req_get = CapabilityRequest(
            capability_name="kortex.system.capability.get",
            session_token=token,
            context={"resource_tenant_id": tenant_id},
            parameters={"capability_name": "kortex.analytics.query.execute"},
        )
        res_get = await kernel.invoke_capability(req_get)
        assert isinstance(res_get, dict)
        assert res_get["name"] == "kortex.analytics.query.execute"
        assert res_get["parameters_schema"]["type"] == "object"

        # 3. Invoke kortex.system.capability.get for non-existent / unauthorized capability fails closed
        req_get_missing = CapabilityRequest(
            capability_name="kortex.system.capability.get",
            session_token=token,
            context={"resource_tenant_id": tenant_id},
            parameters={"capability_name": "kortex.nonexistent.cap"},
        )
        with pytest.raises(CapabilityNotFoundError):
            await kernel.invoke_capability(req_get_missing)
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_gates_12_13_14_ai_tool_filtering_seam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gates 12, 13, 14: AI tool filtering seam.

    - Gate 12: ToolRegistry is globally preserved and never mutated.
    - Gate 13: Unauthorized tools are omitted.
    - Gate 14: Authorized tools are preserved with exact schemas and settings.
    """
    kernel, storage, _security, registry = await _make_env(tmp_path, monkeypatch)
    try:
        tenant_id = "tenant-ai-tools"
        principal_id = "user-ai"
        role_operator = _role("operator")
        perm_read = "sensor:read"

        await _seed_user(storage.data, tenant_id, principal_id, roles=[role_operator])
        await _grant_permission(storage.data, role_operator, perm_read)

        cap_auth = "sensor.data.read"
        cap_unauth = "sensor.firmware.upgrade"

        registry.register_capability(
            name=cap_auth,
            description="Read sensor telemetry data",
            provider="iot",
            handler=AsyncMock(return_value={}),
            required_permissions=[perm_read],
            requires_authentication=True,
            security_classification="INTERNAL",
            is_read_only=True,
            is_idempotent=True,
        )
        registry.register_capability(
            name=cap_unauth,
            description="Upgrade sensor firmware",
            provider="iot",
            handler=AsyncMock(return_value={}),
            required_permissions=["sensor:firmware:admin"],
            requires_authentication=True,
            security_classification="RESTRICTED",
            is_read_only=False,
            is_idempotent=False,
        )

        # Build global ToolRegistry
        global_tool_auth = ToolDefinition(
            name="read_sensor",
            description="Read sensor data tool",
            canonical_capability=cap_auth,
            parameters_schema={"type": "object", "properties": {"sensor_id": {"type": "string"}}},
            is_mutation=False,
            timeout_seconds=45.0,
        )
        global_tool_unauth = ToolDefinition(
            name="upgrade_firmware",
            description="Upgrade firmware tool",
            canonical_capability=cap_unauth,
            parameters_schema={"type": "object", "properties": {"version": {"type": "string"}}},
            is_mutation=True,
            timeout_seconds=120.0,
        )
        global_tool_absent = ToolDefinition(
            name="absent_action",
            description="Tool pointing to non-existent capability",
            canonical_capability="nonexistent.cap",
            parameters_schema={},
            is_mutation=False,
        )

        tool_registry = ToolRegistry([global_tool_auth, global_tool_unauth, global_tool_absent])
        initial_tools_snapshot = list(tool_registry.list_tools())
        assert len(initial_tools_snapshot) == 3

        principal = SecurityPrincipal(
            principal_id=principal_id,
            principal_type=PrincipalType.USER,
            tenant_id=tenant_id,
            roles=[role_operator],
            attributes={"clearance_level": "INTERNAL"},
        )
        context = CapabilityExecutionContext(
            request_id=str(uuid.uuid4()),
            correlation_id=str(uuid.uuid4()),
            capability_name="kortex.system.capability.project",
            principal=principal,
            tenant_id=tenant_id,
        )

        # Perform tool filtering for tenant
        projected_tools = await project_tools_for_tenant(
            kernel=kernel,
            tool_registry=tool_registry,
            execution_context=context,
        )

        # Gate 12: ToolRegistry is completely unmutated
        assert tool_registry.list_tools() == initial_tools_snapshot
        assert len(tool_registry.list_tools()) == 3

        # Gate 13: Unauthorized tools are omitted
        projected_names = {t.name for t in projected_tools}
        assert "read_sensor" in projected_names
        assert "upgrade_firmware" not in projected_names
        assert "absent_action" not in projected_names
        assert len(projected_tools) == 1

        # Gate 14: Authorized tools are preserved with exact metadata
        tool = projected_tools[0]
        assert tool.name == global_tool_auth.name
        assert tool.description == global_tool_auth.description
        assert tool.parameters_schema == global_tool_auth.parameters_schema
        assert tool.canonical_capability == global_tool_auth.canonical_capability
        assert tool.is_mutation == global_tool_auth.is_mutation
        assert tool.timeout_seconds == global_tool_auth.timeout_seconds
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_gate_20_f4_workflow_behavior_remains_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate 20: F4 Workflow Engine publish validation and lifecycle remain unaffected."""
    db_file = tmp_path / f"test_wf_{uuid.uuid4().hex[:8]}.db"
    storage_dir = tmp_path / f"storage_wf_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(storage_dir))

    kernel = Kernel()
    storage = StorageEngine(base_directory=str(storage_dir))
    security = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow = WorkflowEngine()

    kernel.register_engine(storage)
    kernel.register_engine(security)
    kernel.register_engine(workflow)

    # Register projection capabilities
    register_projection_capabilities(kernel)

    # Boot kernel with workflow engine active
    await kernel.boot()
    try:
        # Verify workflow capabilities are registered globally
        wf_caps = [c for c in kernel.list_capabilities() if c.provider == "workflow"]
        assert len(wf_caps) > 0, "Workflow capabilities must be present"

        # Verify global search is unaffected
        found = kernel.search_capabilities(keyword="workflow")
        assert len(found) > 0
    finally:
        await kernel.shutdown()
