"""
Unit tests for TenantScopedProcessAnalyticsRepository:
Tenant isolation, SQL aggregations, timeout handling, and deterministic ordering.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from kortex.engines.process_intelligence.analyzer import ProcessAnalyzer
from kortex.engines.process_intelligence.exceptions import ProcessAnalyticsTimeoutError
from kortex.engines.process_intelligence.repository import (
    TenantScopedProcessAnalyticsRepository,
)
from kortex.engines.process_intelligence.tables import (
    t_workflow_instances,
    t_workflow_step_runs,
)
from kortex.engines.storage.stores.data_store import RelationalDataStore


class MockDbManager:
    """Mock DatabaseEngineManager for async sqlite in-memory testing."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_session(self) -> AsyncIterator[AsyncSession]:
        async with AsyncSession(self._engine) as session:
            yield session


@pytest.fixture
async def memory_data_store() -> AsyncIterator[RelationalDataStore]:
    # Use SQLite in-memory with unique URI per test
    test_id = str(uuid.uuid4())
    engine = create_async_engine(f"sqlite+aiosqlite:///file:{test_id}?mode=memory&cache=shared")

    # Create tables
    from kortex.engines.process_intelligence.tables import metadata as pi_metadata

    async with engine.begin() as conn:
        await conn.run_sync(pi_metadata.create_all)

    db_mgr = MockDbManager(engine)
    data_store = RelationalDataStore(db_mgr)
    yield data_store

    await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_isolation_in_repository(memory_data_store: RelationalDataStore) -> None:
    analyzer = ProcessAnalyzer()
    repo_a = TenantScopedProcessAnalyticsRepository(memory_data_store, "tenant_A", analyzer)
    repo_b = TenantScopedProcessAnalyticsRepository(memory_data_store, "tenant_B", analyzer)

    now = datetime.datetime.now(datetime.UTC)
    t0 = now - datetime.timedelta(days=5)

    # Insert instances for Tenant A and Tenant B
    async def _seed(session: AsyncSession) -> None:
        # Tenant A instance
        await session.execute(
            t_workflow_instances.insert().values(
                id="inst_a1",
                tenant_id="tenant_A",
                definition_id="def_1",
                definition_version="1.0.0",
                state="COMPLETED",
                status="COMPLETED",
                created_at=t0,
                updated_at=t0 + datetime.timedelta(seconds=10),
            )
        )
        # Tenant B instance
        await session.execute(
            t_workflow_instances.insert().values(
                id="inst_b1",
                tenant_id="tenant_B",
                definition_id="def_1",
                definition_version="1.0.0",
                state="FAILED",
                status="FAILED",
                created_at=t0,
                updated_at=t0 + datetime.timedelta(seconds=5),
            )
        )

    await memory_data_store.execute_in_transaction(_seed)

    # Query Tenant A summary
    kpis_a = await repo_a.get_summary_kpis(
        definition_id=None,
        since=now - datetime.timedelta(days=10),
        until=now + datetime.timedelta(days=1),
        window_clamped=False,
    )
    assert kpis_a.total_instances == 1
    assert kpis_a.completed_runs == 1
    assert kpis_a.failed_runs == 0
    assert kpis_a.avg_cycle_time_ms == 10000.0

    # Query Tenant B summary
    kpis_b = await repo_b.get_summary_kpis(
        definition_id=None,
        since=now - datetime.timedelta(days=10),
        until=now + datetime.timedelta(days=1),
        window_clamped=False,
    )
    assert kpis_b.total_instances == 1
    assert kpis_b.completed_runs == 0
    assert kpis_b.failed_runs == 1


@pytest.mark.asyncio
async def test_operation_timeout_handling(memory_data_store: RelationalDataStore) -> None:
    analyzer = ProcessAnalyzer()
    # Set tight timeout of 0.05 seconds
    repo = TenantScopedProcessAnalyticsRepository(memory_data_store, "tenant_A", analyzer, timeout_seconds=0.05)

    # Wrap an artificially delayed action
    async def _slow_action(session: AsyncSession) -> None:
        await asyncio.sleep(0.2)

    with pytest.raises(ProcessAnalyticsTimeoutError):
        await repo._execute_with_timeout(_slow_action)


@pytest.mark.asyncio
async def test_trace_extraction_and_version_filtering(memory_data_store: RelationalDataStore) -> None:
    analyzer = ProcessAnalyzer()
    repo = TenantScopedProcessAnalyticsRepository(memory_data_store, "tenant_X", analyzer)

    now = datetime.datetime.now(datetime.UTC)
    t0 = now - datetime.timedelta(days=2)

    async def _seed(session: AsyncSession) -> None:
        # Version 1.0.0
        await session.execute(
            t_workflow_instances.insert().values(
                id="inst_v1",
                tenant_id="tenant_X",
                definition_id="multi_ver",
                definition_version="1.0.0",
                state="COMPLETED",
                status="COMPLETED",
                created_at=t0,
                updated_at=t0 + datetime.timedelta(seconds=5),
            )
        )
        await session.execute(
            t_workflow_step_runs.insert().values(
                id="s_v1_1",
                instance_id="inst_v1",
                step_id="step_old",
                status="COMPLETED",
                started_at=t0,
                completed_at=t0 + datetime.timedelta(seconds=2),
            )
        )
        # Version 2.0.0 (more recent)
        await session.execute(
            t_workflow_instances.insert().values(
                id="inst_v2",
                tenant_id="tenant_X",
                definition_id="multi_ver",
                definition_version="2.0.0",
                state="COMPLETED",
                status="COMPLETED",
                created_at=t0 + datetime.timedelta(hours=1),
                updated_at=t0 + datetime.timedelta(hours=1, seconds=5),
            )
        )
        await session.execute(
            t_workflow_step_runs.insert().values(
                id="s_v2_1",
                instance_id="inst_v2",
                step_id="step_new",
                status="COMPLETED",
                started_at=t0 + datetime.timedelta(hours=1),
                completed_at=t0 + datetime.timedelta(hours=1, seconds=3),
            )
        )

    await memory_data_store.execute_in_transaction(_seed)

    # 1. When version is omitted, defaults to latest version observed (2.0.0)
    traces, _total, ver_analyzed, avails = await repo.get_traces_for_mining(
        definition_id="multi_ver",
        version=None,
        since=t0 - datetime.timedelta(days=1),
        until=now,
        max_instances=1000,
    )
    assert ver_analyzed == "2.0.0"
    assert len(traces) == 1
    assert traces[0].steps[0].step_id == "step_new"
    assert "1.0.0" in avails and "2.0.0" in avails

    # 2. When version is explicitly requested (1.0.0)
    traces_v1, _, ver_v1, _ = await repo.get_traces_for_mining(
        definition_id="multi_ver",
        version="1.0.0",
        since=t0 - datetime.timedelta(days=1),
        until=now,
        max_instances=1000,
    )
    assert ver_v1 == "1.0.0"
    assert len(traces_v1) == 1
    assert traces_v1[0].steps[0].step_id == "step_old"
