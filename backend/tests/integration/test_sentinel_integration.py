"""Integration tests for KORTEX Sentinel Engine with Kernel runtime.

Covers:
- Kernel registration and boot sequence integration
- BootEngine health rollup integration via Kernel.health_check()
- Canonical capability invocation through Kernel.invoke_capability() with authentic session token
- Clean runtime shutdown and task draining
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import (
    PrincipalType,
    RolePermissionRecord,
    TokenPayload,
)
from kortex.engines.sentinel.constants import (
    CAPABILITY_DIAGNOSTICS_GET,
    CAPABILITY_HEALTH_GET,
    CAPABILITY_STATUS_GET,
    SENTINEL_PERMISSION_READ,
)
from kortex.engines.sentinel.engine import SentinelEngine
from kortex.engines.sentinel.models import SentinelConfig, SentinelStatus
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32


@pytest.mark.asyncio
async def test_sentinel_kernel_boot_and_dispatch_integration(tmp_path: Path) -> None:
    """Verify Sentinel boots in real Kernel, rolls up into health_check, and dispatches via capabilities."""
    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")

    storage_engine = StorageEngine(base_directory=str(tmp_path / "sentinel_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)

    sentinel_config = SentinelConfig(
        enable_background_monitor=False,
        startup_grace_seconds=0.0,
    )
    sentinel_engine = SentinelEngine(config=sentinel_config)

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(sentinel_engine)

    # Boot the Kernel
    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    # 1. Engine registration check
    resolved = kernel.get_engine("sentinel")
    assert resolved is sentinel_engine
    assert sentinel_engine.state.value == "RUNNING"

    # 2. Kernel health check rollup check
    sys_health = await kernel.health_check()
    assert "system_health" in sys_health
    reports = sys_health["system_health"]["engines"]
    assert "sentinel" in reports
    assert reports["sentinel"]["healthy"] is True

    # 3. Provision a test principal with system:sentinel:read permission
    tenant_id = "tenant-sentinel-test"
    principal_id = f"sentinel-operator-{uuid.uuid4().hex[:6]}"
    session_token = await _provision_sentinel_principal(
        kernel=kernel,
        security_engine=security_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
    )

    # 4. Invoke kortex.sentinel.health.get via Kernel.invoke_capability
    health_req = CapabilityRequest(
        capability_name=CAPABILITY_HEALTH_GET,
        session_token=session_token,
        parameters={},
        context={"resource_tenant_id": tenant_id},
    )
    health_res = await kernel.invoke_capability(health_req)
    assert health_res["healthy"] is True
    assert health_res["status"] == SentinelStatus.HEALTHY.value
    assert "storage" in health_res["subsystems"]
    assert "security" in health_res["subsystems"]

    # 5. Invoke kortex.sentinel.status.get
    status_req = CapabilityRequest(
        capability_name=CAPABILITY_STATUS_GET,
        session_token=session_token,
        parameters={},
        context={"resource_tenant_id": tenant_id},
    )
    status_res = await kernel.invoke_capability(status_req)
    assert status_res["engine"] == "sentinel"
    assert status_res["status"] == SentinelStatus.HEALTHY.value

    # 6. Invoke kortex.sentinel.diagnostics.get
    diag_req = CapabilityRequest(
        capability_name=CAPABILITY_DIAGNOSTICS_GET,
        session_token=session_token,
        parameters={},
        context={"resource_tenant_id": tenant_id},
    )
    diag_res = await kernel.invoke_capability(diag_req)
    assert diag_res["version"] == "1.0.0"
    assert "metrics" in diag_res

    # 7. Unauthenticated request is rejected
    unauth_req = CapabilityRequest(
        capability_name=CAPABILITY_HEALTH_GET,
        session_token=None,
    )
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(unauth_req)

    # 8. Unauthorized principal (lacking system:sentinel:read) is rejected by RBAC
    unauth_principal_id = f"viewer-{uuid.uuid4().hex[:6]}"
    unauth_token = await _provision_unauthorized_principal(
        security_engine=security_engine,
        tenant_id=tenant_id,
        principal_id=unauth_principal_id,
    )
    rbac_req = CapabilityRequest(
        capability_name=CAPABILITY_HEALTH_GET,
        session_token=unauth_token,
        parameters={},
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(rbac_req)

    # 9. Clean Shutdown
    await kernel.shutdown()
    assert kernel.state == KernelState.STOPPED
    assert sentinel_engine.state.value == "STOPPED"


async def _provision_sentinel_principal(
    kernel: Kernel,
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str,
) -> TokenPayload:
    """Helper to provision a test principal with sentinel read permission."""
    role_name = "sentinel-operator-role"
    password = "SafePassword123!"

    # Provision principal
    await security_engine.authentication_manager.provision_principal(
        tenant_id=tenant_id,
        principal_id=principal_id,
        principal_type=PrincipalType.USER,
        credential=password,
        roles=[role_name],
        attributes={"clearance_level": "INTERNAL"},
    )

    # Grant permission to role in DB
    async def _action(session: Any) -> None:
        perm_rec = RolePermissionRecord(
            id=str(uuid.uuid4()),
            role=role_name,
            permission=SENTINEL_PERMISSION_READ,
        )
        session.add(perm_rec)

    storage = kernel.get_engine("storage")
    await storage.data.execute_in_transaction(_action)

    # Authenticate and obtain session token
    principal = await security_engine.authenticate(
        {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": password,
            "principal_type": PrincipalType.USER.value,
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _provision_unauthorized_principal(
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str,
) -> TokenPayload:
    """Helper to provision a test principal without any roles or permissions."""
    password = "SafePassword123!"
    await security_engine.authentication_manager.provision_principal(
        tenant_id=tenant_id,
        principal_id=principal_id,
        principal_type=PrincipalType.USER,
        credential=password,
        roles=[],
        attributes={"clearance_level": "INTERNAL"},
    )
    principal = await security_engine.authenticate(
        {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": password,
            "principal_type": PrincipalType.USER.value,
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)
