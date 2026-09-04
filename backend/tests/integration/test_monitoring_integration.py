"""Integration tests for KORTEX Monitoring Engine with Kernel runtime.

Tests:
- Kernel registration and boot sequence integration
- BootEngine health rollup integration via Kernel.health_check()
- Canonical capability invocation through Kernel.invoke_capability() with authentic session token:
  1. kortex.monitoring.metrics.get
  2. kortex.monitoring.timeseries.get
  3. kortex.monitoring.dashboard.get
  4. kortex.monitoring.diagnostics.get
- Security enforcement:
  * Unauthenticated invocation rejected (AuthenticationError)
  * Unauthorized invocation rejected (AuthorizationDeniedError)
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
from kortex.engines.monitoring.constants import (
    CAPABILITY_MONITORING_DASHBOARD_GET,
    CAPABILITY_MONITORING_DIAGNOSTICS_GET,
    CAPABILITY_MONITORING_METRICS_GET,
    CAPABILITY_MONITORING_TIMESERIES_GET,
    PERMISSION_MONITORING_READ,
)
from kortex.engines.monitoring.engine import MonitoringEngine
from kortex.engines.monitoring.models import MonitoringConfig
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import (
    PrincipalType,
    RolePermissionRecord,
    TokenPayload,
)
from kortex.engines.sentinel.engine import SentinelEngine
from kortex.engines.sentinel.models import SentinelConfig
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32


@pytest.mark.asyncio
async def test_monitoring_kernel_boot_and_dispatch_integration(tmp_path: Path) -> None:
    """Verify Monitoring boots in real Kernel, rolls up into health_check, and dispatches all 4 capabilities."""
    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")

    storage_engine = StorageEngine(base_directory=str(tmp_path / "monitoring_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    sentinel_engine = SentinelEngine(config=SentinelConfig(enable_background_monitor=False, startup_grace_seconds=0.0))
    monitoring_engine = MonitoringEngine(config=MonitoringConfig(collect_interval_seconds=60.0))

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(sentinel_engine)
    kernel.register_engine(monitoring_engine)

    # Boot the Kernel
    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    # 1. Verify engine registration
    resolved = kernel.get_engine("monitoring")
    assert resolved is monitoring_engine
    assert monitoring_engine.state.value == "RUNNING"

    # 2. Kernel health check rollup check
    sys_health = await kernel.health_check()
    assert "system_health" in sys_health
    reports = sys_health["system_health"]["engines"]
    assert "monitoring" in reports
    assert reports["monitoring"]["healthy"] is True

    # 3. Provision an authorized operator principal with system:monitoring:read permission
    tenant_id = "tenant-monitoring-test"
    principal_id = f"monitoring-operator-{uuid.uuid4().hex[:6]}"
    session_token = await _provision_monitoring_principal(
        kernel=kernel,
        security_engine=security_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
    )

    # Trigger a collection cycle to seed telemetry
    await monitoring_engine.collect_now()

    # 4. Invoke kortex.monitoring.metrics.get
    metrics_req = CapabilityRequest(
        capability_name=CAPABILITY_MONITORING_METRICS_GET,
        session_token=session_token,
        parameters={"subsystem": "system"},
        context={"resource_tenant_id": tenant_id},
    )
    metrics_res = await kernel.invoke_capability(metrics_req)
    assert isinstance(metrics_res, list)
    assert len(metrics_res) >= 1
    assert any(m["name"] == "system.memory.working_set_mb" for m in metrics_res)

    # 5. Invoke kortex.monitoring.timeseries.get
    ts_req = CapabilityRequest(
        capability_name=CAPABILITY_MONITORING_TIMESERIES_GET,
        session_token=session_token,
        parameters={"metric_name": "system.memory.working_set_mb"},
        context={"resource_tenant_id": tenant_id},
    )
    ts_res = await kernel.invoke_capability(ts_req)
    assert isinstance(ts_res, dict)
    assert ts_res["metric_name"] == "system.memory.working_set_mb"
    assert len(ts_res["points"]) >= 1

    # 6. Invoke kortex.monitoring.dashboard.get
    dash_req = CapabilityRequest(
        capability_name=CAPABILITY_MONITORING_DASHBOARD_GET,
        session_token=session_token,
        parameters={},
        context={"resource_tenant_id": tenant_id},
    )
    dash_res = await kernel.invoke_capability(dash_req)
    assert isinstance(dash_res, dict)
    assert "system_resources" in dash_res
    assert "sentinel_health" in dash_res
    assert "top_metrics" in dash_res
    assert "active_alerts" in dash_res

    # 7. Invoke kortex.monitoring.diagnostics.get
    diag_req = CapabilityRequest(
        capability_name=CAPABILITY_MONITORING_DIAGNOSTICS_GET,
        session_token=session_token,
        parameters={},
        context={"resource_tenant_id": tenant_id},
    )
    diag_res = await kernel.invoke_capability(diag_req)
    assert isinstance(diag_res, dict)
    assert diag_res["engine"] == "monitoring"
    assert diag_res["version"] == "1.0.0"

    # 8. Security verification: Unauthenticated caller rejected
    unauth_req = CapabilityRequest(
        capability_name=CAPABILITY_MONITORING_DASHBOARD_GET,
        session_token=None,
        parameters={},
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(unauth_req)

    # 9. Security verification: Unauthorized caller rejected (lacks system:monitoring:read)
    unauthorized_token = await _provision_unauthorized_principal(
        security_engine=security_engine,
        tenant_id=tenant_id,
        principal_id=f"unauthorized-user-{uuid.uuid4().hex[:6]}",
    )
    rbac_req = CapabilityRequest(
        capability_name=CAPABILITY_MONITORING_DASHBOARD_GET,
        session_token=unauthorized_token,
        parameters={},
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(rbac_req)

    # 10. Clean shutdown
    await kernel.shutdown()
    assert kernel.state == KernelState.STOPPED
    assert monitoring_engine.state.value == "STOPPED"


async def _provision_monitoring_principal(
    kernel: Kernel,
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str,
) -> TokenPayload:
    """Helper to provision a test principal with monitoring read permission."""
    role_name = "monitoring-operator-role"
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
            permission=PERMISSION_MONITORING_READ,
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
    """Helper to provision a test principal without monitoring permissions."""
    role_name = "unauthorized-viewer-role"
    password = "SafePassword123!"

    await security_engine.authentication_manager.provision_principal(
        tenant_id=tenant_id,
        principal_id=principal_id,
        principal_type=PrincipalType.USER,
        credential=password,
        roles=[role_name],
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
