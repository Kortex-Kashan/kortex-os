"""
Kernel integration tests for Process Intelligence Engine.

Exercises capability registration, authenticated dispatch through Kernel.invoke_capability,
tenant scoping, and fail-closed security for unauthenticated callers.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import cast

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.process_intelligence.engine import ProcessIntelligenceEngine
from kortex.engines.process_intelligence.models import (
    BottlenecksResult,
    ProcessGraph,
    ProcessSummaryKPIs,
    VariantListResult,
)
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError
from kortex.engines.security.models import TokenPayload
from kortex.engines.storage.engine import StorageEngine

_MASTER_KEY = b"\xaa" * 32
_SIGNING_KEY = b"\xbb" * 32


async def _boot_test_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, str, str]:
    from tests.unit.test_capability_dispatch_adversarial import (
        _grant_role_permission,
        _seed_principal,
    )

    kernel = Kernel()
    db_file = tmp_path / "integration_pi.db"
    kernel._db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_file}")

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    security_engine = SecurityEngine(master_key=_MASTER_KEY, signing_private_key=_SIGNING_KEY)
    pi_engine = ProcessIntelligenceEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(pi_engine)

    await kernel.boot()

    tenant_id = "tenant-pi-test"
    role = "role-pi-analyst"

    # Grant workflow:read to role
    await _grant_role_permission(storage_engine.data, role, "workflow:read")
    await _seed_principal(
        storage_engine.data,
        tenant_id,
        "principal-pi-user",
        roles=[role],
        clearance_level="INTERNAL",
    )

    # Seed test workflow instances and step runs into database
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    t0 = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)

    async def _seed(session: AsyncSession) -> None:
        await session.execute(
            text(
                """
                INSERT INTO workflow_instances (
                    id, definition_id, definition_version, tenant_id,
                    current_step_index, state, status, context_json,
                    compensation_stack_json, trace_id, version, created_at, updated_at
                ) VALUES (
                    :id, :definition_id, :definition_version, :tenant_id,
                    :current_step_index, :state, :status, :context_json,
                    :compensation_stack_json, :trace_id, :version, :created_at, :updated_at
                )
                """
            ),
            {
                "id": "inst_pi_1",
                "definition_id": "order_process",
                "definition_version": "1.0.0",
                "tenant_id": tenant_id,
                "current_step_index": 2,
                "state": "COMPLETED",
                "status": "COMPLETED",
                "context_json": "{}",
                "compensation_stack_json": "[]",
                "trace_id": "tr_1",
                "version": 1,
                "created_at": t0,
                "updated_at": t0 + datetime.timedelta(seconds=15),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO workflow_step_runs (
                    id, instance_id, step_id, attempt, status,
                    started_at, completed_at, created_at, updated_at
                ) VALUES (
                    :id, :instance_id, :step_id, :attempt, :status,
                    :started_at, :completed_at, :created_at, :updated_at
                )
                """
            ),
            {
                "id": "sr_1",
                "instance_id": "inst_pi_1",
                "step_id": "validate_order",
                "attempt": 1,
                "status": "COMPLETED",
                "started_at": t0,
                "completed_at": t0 + datetime.timedelta(seconds=5),
                "created_at": t0,
                "updated_at": t0 + datetime.timedelta(seconds=5),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO workflow_step_runs (
                    id, instance_id, step_id, attempt, status,
                    started_at, completed_at, created_at, updated_at
                ) VALUES (
                    :id, :instance_id, :step_id, :attempt, :status,
                    :started_at, :completed_at, :created_at, :updated_at
                )
                """
            ),
            {
                "id": "sr_2",
                "instance_id": "inst_pi_1",
                "step_id": "charge_card",
                "attempt": 1,
                "status": "COMPLETED",
                "started_at": t0 + datetime.timedelta(seconds=5),
                "completed_at": t0 + datetime.timedelta(seconds=15),
                "created_at": t0 + datetime.timedelta(seconds=5),
                "updated_at": t0 + datetime.timedelta(seconds=15),
            },
        )

    await storage_engine.data.execute_in_transaction(_seed)

    return kernel, storage_engine, tenant_id, "principal-pi-user"


async def _issue_token(kernel: Kernel, tenant_id: str, principal_id: str) -> TokenPayload:
    from tests.unit.test_capability_dispatch_adversarial import _issue_token

    sec_engine = cast(SecurityEngine, kernel.get_engine("security"))
    return await _issue_token(sec_engine, tenant_id, principal_id)


@pytest.mark.asyncio
async def test_process_intelligence_kernel_dispatch_flow(tmp_path: Path) -> None:
    kernel, _storage_engine, tenant_id, principal_id = await _boot_test_kernel(tmp_path)
    token = await _issue_token(kernel, tenant_id, principal_id)

    # 1. Summary KPI capability
    cap_request = CapabilityRequest(
        capability_name="kortex.process_intelligence.summary.get",
        session_token=token,
        parameters={"definition_id": "order_process"},
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(cap_request)
    assert isinstance(result, ProcessSummaryKPIs)
    assert result.total_instances == 1
    assert result.completed_runs == 1
    assert result.avg_cycle_time_ms == 15000.0

    # 2. Bottlenecks capability
    cap_request_bn = CapabilityRequest(
        capability_name="kortex.process_intelligence.bottlenecks.get",
        session_token=token,
        parameters={"definition_id": "order_process"},
        context={"resource_tenant_id": tenant_id},
    )
    res_bn = await kernel.invoke_capability(cap_request_bn)
    assert isinstance(res_bn, BottlenecksResult)
    assert len(res_bn.steps) == 2

    # 3. Process Graph capability
    cap_request_pg = CapabilityRequest(
        capability_name="kortex.process_intelligence.process_graph.get",
        session_token=token,
        parameters={"definition_id": "order_process"},
        context={"resource_tenant_id": tenant_id},
    )
    res_pg = await kernel.invoke_capability(cap_request_pg)
    assert isinstance(res_pg, ProcessGraph)
    assert len(res_pg.nodes) >= 3

    # 4. Trace Variants capability
    cap_request_tv = CapabilityRequest(
        capability_name="kortex.process_intelligence.variants.list",
        session_token=token,
        parameters={"definition_id": "order_process"},
        context={"resource_tenant_id": tenant_id},
    )
    res_tv = await kernel.invoke_capability(cap_request_tv)
    assert isinstance(res_tv, VariantListResult)
    assert res_tv.total_variants_discovered == 1
    assert res_tv.returned_variants[0].steps == ["validate_order", "charge_card"]


@pytest.mark.asyncio
async def test_unauthenticated_scheduled_workflow_fails_closed(tmp_path: Path) -> None:
    kernel, _, _, _ = await _boot_test_kernel(tmp_path)

    # Unattended invocation with session_token=None
    unauth_request = CapabilityRequest(
        capability_name="kortex.process_intelligence.summary.get",
        session_token=None,
        parameters={"definition_id": "order_process"},
    )
    with pytest.raises(AuthenticationError) as exc_info:
        await kernel.invoke_capability(unauth_request)

    assert "session token is required" in str(exc_info.value)
