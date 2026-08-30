"""Integration tests for KORTEX OS Connector Engine (Milestone 9).

Verifies full Kernel boot sequence, IoC container resolution, capability registration and dispatching,
real Event Engine system event pub/sub propagation, Storage Engine ICacheStore profile caching,
credential security isolation, aggregated health reporting, error isolation, and failure pathways.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.exceptions import KernelBootError
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import ConnectorProfileNotFoundError
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorProfile,
)
from kortex.engines.connector.profiles import ConnectorProfileManager
from kortex.engines.storage.engine import StorageEngine


@pytest.mark.asyncio
async def test_kernel_boot_and_connector_engine_ioc_registration(tmp_path) -> None:
    """1. Test Kernel boot sequence, dependency resolution, IoC registration, and clean shutdown."""
    kernel = Kernel()

    storage_engine = StorageEngine(base_directory=str(tmp_path / "conn_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)

    # Boot Kernel runtime
    await kernel.boot()

    assert kernel.state == KernelState.RUNNING
    assert storage_engine.state == EngineState.RUNNING
    assert connector_engine.state == EngineState.RUNNING

    # Verify IoC Container resolution by string key
    resolved_by_string = kernel.container.resolve("engine.connector")
    assert resolved_by_string is connector_engine

    # System health check aggregation
    health = await kernel.health_check()
    assert health["kernel_state"] == "RUNNING"
    assert "connector" in health["system_health"]["engines"]
    assert health["system_health"]["engines"]["connector"]["status"] == "healthy"

    # Shutdown
    await kernel.shutdown()
    assert kernel.state == KernelState.STOPPED
    assert connector_engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_kernel_capability_lookup_and_execution(tmp_path) -> None:
    """2. Test resolving and invoking canonical Connector capabilities through Kernel Capability Registry."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "cap_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    # 1. Look up driver registration capability
    register_driver_cap = kernel.get_capability("kortex.connector.driver.register")
    assert register_driver_cap.provider == "connector"

    driver = DummyConnectorDriver()
    kernel._registry_engine.get_raw_handler_for_testing("kortex.connector.driver.register")(driver)

    # 2. Look up driver listing capability
    drivers = kernel._registry_engine.get_raw_handler_for_testing("kortex.connector.driver.list")()
    assert len(drivers) == 1
    assert drivers[0].driver_id == "connector-dummy"

    # Register profile directly on engine's profile manager
    profile = ConnectorProfile(
        profile_id="prof-cap-1",
        name="Capability Test Profile",
        driver_id="connector-dummy",
        options={"endpoint": "https://api.dummy.com"},
    )
    await connector_engine.profile_manager.register_profile(profile)

    # 3. Look up profile retrieval capability
    fetched_profile = await kernel._registry_engine.get_raw_handler_for_testing(
        "kortex.connector.profile.get"
    )("prof-cap-1")
    assert fetched_profile.profile_id == "prof-cap-1"

    # 4. Look up action execution capability
    req = ActionRequest(
        request_id="req-cap-exec-1",
        profile_id="prof-cap-1",
        action_type="SEND",
        payload={"message": "capability test"},
        correlation_id="corr-cap-1",
    )

    res: ActionResult = await kernel._registry_engine.get_raw_handler_for_testing(
        "kortex.connector.action.execute"
    )(req)
    assert res.status == "SUCCESS"
    assert res.request_id == "req-cap-exec-1"
    assert res.correlation_id == "corr-cap-1"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_end_to_end_action_execution_with_real_event_engine_pubsub(tmp_path) -> None:
    """3. Test real Event Engine pub/sub propagation for connector action lifecycle events."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "event_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    driver = DummyConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        profile_id="prof-event-1",
        name="Event Integration Profile",
        driver_id="connector-dummy",
    )
    await connector_engine.profile_manager.register_profile(profile)

    received_events: list[Any] = []

    def event_handler(event: Any) -> None:
        if event.topic.startswith("connector."):
            received_events.append(event)

    # Subscribe to wildcard '*' for EventEngine compatibility
    kernel.subscribe_event("*", event_handler)

    req = ActionRequest(
        request_id="req-evt-pub-1",
        profile_id="prof-event-1",
        action_type="SEND",
        payload={"test": "event_flow"},
        correlation_id="corr-evt-pub-1",
    )

    res = await connector_engine.execute_action(req)
    assert res.status == "SUCCESS"

    await asyncio.sleep(0.05)

    # Verify received events
    topics = [evt.topic for evt in received_events]
    assert "connector.action.started" in topics
    assert "connector.action.completed" in topics

    started_evt = next(evt for evt in received_events if evt.topic == "connector.action.started")
    assert started_evt.payload["request_id"] == "req-evt-pub-1"
    assert started_evt.payload["correlation_id"] == "corr-evt-pub-1"

    completed_evt = next(evt for evt in received_events if evt.topic == "connector.action.completed")
    assert completed_evt.payload["request_id"] == "req-evt-pub-1"
    assert completed_evt.payload["correlation_id"] == "corr-evt-pub-1"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_driver_registration_event_propagation(tmp_path) -> None:
    """4. Test driver registration publishing connector.driver.registered event via Event Engine."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "driver_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    received_events: list[Any] = []

    def event_handler(event: Any) -> None:
        if event.topic == "connector.driver.registered":
            received_events.append(event)

    kernel.subscribe_event("connector.driver.registered", event_handler)

    driver = DummyConnectorDriver()
    connector_engine.register_driver(driver)

    await asyncio.sleep(0.05)

    assert len(received_events) == 1
    reg_evt = received_events[0]
    assert reg_evt.payload["driver_id"] == "connector-dummy"
    assert reg_evt.payload["driver_name"] == "Reference Dummy Connector Driver"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_storage_engine_icachestore_integration(tmp_path) -> None:
    """5. Test ConnectorProfileManager caching profiles in StorageEngine public cache store (ICacheStore)."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "cache_storage"))

    kernel.register_engine(storage_engine)
    await kernel.boot()

    # Access public ICacheStore property from StorageEngine
    cache_store = storage_engine.cache
    assert cache_store is not None

    pm = ConnectorProfileManager(cache_store=cache_store)

    profile = ConnectorProfile(
        profile_id="prof-cache-1",
        name="Cached Profile 1",
        driver_id="connector-dummy",
        rate_limit_per_sec=15.0,
    )
    await pm.register_profile(profile)

    # Verify cached entry exists in StorageEngine.cache
    cache_key = "connector:profile:prof-cache-1"
    cached_data = await cache_store.get(cache_key)
    assert cached_data is not None
    assert isinstance(cached_data, dict)
    assert cached_data["name"] == "Cached Profile 1"

    # Retrieve via profile manager (cache hit pathway)
    fetched = await pm.get_profile("prof-cache-1")
    assert fetched.name == "Cached Profile 1"

    # Delete profile and verify cache deletion
    deleted = await pm.delete_profile("prof-cache-1")
    assert deleted is True

    cached_after_del = await cache_store.get(cache_key)
    assert cached_after_del is None

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_security_credential_isolation_end_to_end(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """6. Test end-to-end credential flow ensuring secrets do not leak into events, diagnostics, logs, or results."""
    secret_handle = "vault:live_slack_api_token"
    resolved_secret = "RESOLVED_LIVE_SECRET_TOKEN_999"

    async def mock_secret_resolver(handle: str, tenant_id: str) -> str:
        if handle == secret_handle:
            return resolved_secret
        raise ValueError("Unknown secret handle")

    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "sec_storage"))
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    driver = DummyConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        profile_id="prof-sec-1",
        name="Secure Channel Profile",
        driver_id="connector-dummy",
        secret_handle=secret_handle,
    )
    await connector_engine.profile_manager.register_profile(profile)

    received_events: list[Any] = []

    def event_handler(event: Any) -> None:
        if event.topic.startswith("connector."):
            received_events.append(event)

    kernel.subscribe_event("*", event_handler)

    req = ActionRequest(
        request_id="req-sec-1",
        profile_id="prof-sec-1",
        action_type="SEND",
        payload={"msg": "secure"},
    )

    with caplog.at_level(logging.DEBUG):
        res = await connector_engine.execute_action(req)

    assert res.status == "SUCCESS"
    assert res.response_payload["secret_authenticated"] is True

    # Security Privacy Verification Across All Systems:
    # 1. Check ActionResult
    res_str = str(res.model_dump())
    assert resolved_secret not in res_str

    # 2. Check System Events
    for evt in received_events:
        evt_str = str(evt.payload)
        assert resolved_secret not in evt_str

    # 3. Check System Health & Diagnostics
    health = await kernel.health_check()
    health_str = str(health)
    assert resolved_secret not in health_str

    tech_diag = connector_engine.diagnostics()
    tech_str = str(tech_diag)
    assert resolved_secret not in tech_str

    # 4. Check Logging Output
    log_text = caplog.text
    assert resolved_secret not in log_text

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_aggregated_system_health_reporting(tmp_path) -> None:
    """7. Test Kernel aggregated health check reporting real ConnectorEngine component statuses."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "health_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    driver = DummyConnectorDriver()
    connector_engine.register_driver(driver)

    health = await kernel.health_check()

    assert health["kernel_state"] == "RUNNING"
    assert "connector" in health["system_health"]["engines"]

    conn_health = health["system_health"]["engines"]["connector"]
    assert conn_health["status"] == "healthy"
    assert conn_health["components"]["registry"]["status"] == "healthy"
    assert conn_health["components"]["registry"]["registered_driver_count"] == 1
    assert conn_health["components"]["profile_manager"]["status"] == "healthy"
    assert conn_health["components"]["rate_limiter"]["status"] == "healthy"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_event_subscriber_error_isolation(tmp_path) -> None:
    """8. Test Event Engine isolating subscriber exceptions without crashing action execution."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "sub_err_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    driver = DummyConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        profile_id="prof-sub-err",
        name="Subscriber Error Profile",
        driver_id="connector-dummy",
    )
    await connector_engine.profile_manager.register_profile(profile)

    def crashing_subscriber(event: Any) -> None:
        raise RuntimeError("Buggy subscriber crashed during event delivery!")

    kernel.subscribe_event("*", crashing_subscriber)

    req = ActionRequest(
        request_id="req-sub-err-1",
        profile_id="prof-sub-err",
        action_type="SEND",
    )

    res = await connector_engine.execute_action(req)
    assert res.status == "SUCCESS"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_event_publication_failure_isolation(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """9. Test ConnectorEngine handling kernel.publish_event exception cleanly with isolated warning log."""
    fake_secret = "SUPER_SECRET_FAILURE_TOKEN_777"
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "pub_err_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    # Inject mock publish_event after boot so boot completes normally
    kernel.publish_event = AsyncMock(
        side_effect=RuntimeError(f"Event Engine delivery broken with secret {fake_secret}")
    )

    driver = DummyConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        profile_id="prof-pub-err",
        name="Publish Error Profile",
        driver_id="connector-dummy",
    )
    await connector_engine.profile_manager.register_profile(profile)

    req = ActionRequest(
        request_id="req-pub-err-1",
        profile_id="prof-pub-err",
        action_type="SEND",
    )

    with caplog.at_level(logging.WARNING):
        res = await connector_engine.execute_action(req)

    assert res.status == "SUCCESS"

    # Verify event publication exception was isolated cleanly
    log_text = caplog.text
    assert "Failed to publish system event 'connector.action.started'." in log_text
    assert fake_secret not in log_text

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_dependency_boot_failure_when_unregistered() -> None:
    """10. Test registering ConnectorEngine without prerequisite engines raises KernelBootError."""
    kernel = Kernel()
    connector_engine = ConnectorEngine()

    # Register ConnectorEngine WITHOUT StorageEngine or ConfigurationEngine
    kernel.register_engine(connector_engine)

    with pytest.raises(KernelBootError) as exc_info:
        await kernel.boot()

    assert "depends on unregistered engine" in str(exc_info.value)


@pytest.mark.asyncio
async def test_storage_cache_failure_fallback_resiliency() -> None:
    """11. Test ConnectorProfileManager fallback to local memory when ICacheStore raises exceptions."""
    class FailingCacheStore:
        async def get(self, key: str) -> Any:
            raise RuntimeError("Cache storage connection timeout")

        async def set(self, key: str, value: Any, ttl_seconds: int = 3600) -> bool:
            raise RuntimeError("Cache storage write failure")

        async def delete(self, key: str) -> bool:
            raise RuntimeError("Cache storage delete failure")

        async def exists(self, key: str) -> bool:
            return False

        async def clear(self) -> bool:
            return False

    failing_cache = FailingCacheStore()  # type: ignore[arg-type]
    pm = ConnectorProfileManager(cache_store=failing_cache)

    profile = ConnectorProfile(
        profile_id="prof-resilient-1",
        name="Resilient Profile",
        driver_id="connector-dummy",
    )

    # Register profile should swallow cache set exception and save to memory
    await pm.register_profile(profile)

    # Get profile should swallow cache get exception and retrieve from memory
    fetched = await pm.get_profile("prof-resilient-1")
    assert fetched.name == "Resilient Profile"

    # Delete profile should swallow cache delete exception and delete from memory
    deleted = await pm.delete_profile("prof-resilient-1")
    assert deleted is True


@pytest.mark.asyncio
async def test_failure_pathway_missing_profile_and_failed_action(tmp_path) -> None:
    """12. Test failure pathways: ConnectorProfileNotFoundError for missing profile and simulated driver failure."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "fail_path_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    driver = DummyConnectorDriver()
    connector_engine.register_driver(driver)

    # 1. Missing Profile Execution Failure
    req_missing = ActionRequest(
        request_id="req-missing-1",
        profile_id="missing-profile-id",
        action_type="SEND",
    )
    with pytest.raises(ConnectorProfileNotFoundError):
        await connector_engine.execute_action(req_missing)

    # 2. Simulated Driver Action Execution Failure
    profile_fail = ConnectorProfile(
        profile_id="prof-sim-fail",
        name="Simulated Failure Profile",
        driver_id="connector-dummy",
    )
    await connector_engine.profile_manager.register_profile(profile_fail)

    req_fail = ActionRequest(
        request_id="req-sim-fail-1",
        profile_id="prof-sim-fail",
        action_type="SEND",
        payload={"should_fail": True, "simulated_error": "Simulated channel timeout"},
    )

    res = await connector_engine.execute_action(req_fail)
    assert res.status == "FAILED"
    assert res.error_details is not None
    assert "error" in res.error_details

    await kernel.shutdown()


# -- Milestone 9.1 Remediation Integration Tests ------------------------------

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from kortex.engines.connector.exceptions import ConnectorSecurityError
from kortex.engines.connector.models import (
    ConnectorActionHistoryModel,
    ConnectorProfileModel,
)
from kortex.engines.connector.rate_limiter import TokenBucketRateLimiter


@pytest.mark.asyncio
async def test_idatastore_profile_persistence_and_query(tmp_path) -> None:
    """13. Test durable ConnectorProfile persistence via Storage Engine IDataStore."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "idatastore_prof_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    # Create tables in DatabaseEngineManager
    await kernel._db_manager.create_all_tables()

    profile = ConnectorProfile(
        profile_id="prof-db-1",
        name="DB Profile 1",
        driver_id="connector-dummy",
        secret_handle="vault:db_token",
        rate_limit_per_sec=25.0,
    )
    await connector_engine.profile_manager.register_profile(profile)

    # Verify profile row persisted in database table via IDataStore
    async def _query_profile(session: AsyncSession) -> ConnectorProfileModel | None:
        stmt = select(ConnectorProfileModel).where(ConnectorProfileModel.id == "prof-db-1")
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    data_store = storage_engine.data
    model = await data_store.execute_in_transaction(_query_profile)
    assert model is not None
    assert model.name == "DB Profile 1"
    assert model.driver_id == "connector-dummy"
    assert model.secret_handle == "vault:db_token"
    assert model.rate_limit_per_sec == 25.0

    # Test updating profile via register_profile
    updated_profile = ConnectorProfile(
        profile_id="prof-db-1",
        name="Updated DB Profile 1",
        driver_id="connector-dummy",
        secret_handle="vault:db_token_v2",
        rate_limit_per_sec=50.0,
    )
    await connector_engine.profile_manager.register_profile(updated_profile)

    updated_model = await data_store.execute_in_transaction(_query_profile)
    assert updated_model is not None
    assert updated_model.name == "Updated DB Profile 1"
    assert updated_model.secret_handle == "vault:db_token_v2"
    assert updated_model.rate_limit_per_sec == 50.0

    # Delete profile and verify DB deletion
    deleted = await connector_engine.profile_manager.delete_profile("prof-db-1")
    assert deleted is True

    deleted_model = await data_store.execute_in_transaction(_query_profile)
    assert deleted_model is None

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_action_execution_history_persistence(tmp_path) -> None:
    """14. Test sanitized action execution history persistence in Storage Engine IDataStore."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "hist_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()
    await kernel._db_manager.create_all_tables()

    driver = DummyConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        profile_id="prof-hist-1",
        name="History Test Profile",
        driver_id="connector-dummy",
    )
    await connector_engine.profile_manager.register_profile(profile)

    req = ActionRequest(
        request_id="req-hist-100",
        profile_id="prof-hist-1",
        action_type="SEND",
        payload={"msg": "history test"},
        correlation_id="corr-hist-100",
    )

    res = await connector_engine.execute_action(req)
    assert res.status == "SUCCESS"

    # Query history record from IDataStore
    async def _query_history(session: AsyncSession) -> ConnectorActionHistoryModel | None:
        stmt = select(ConnectorActionHistoryModel).where(ConnectorActionHistoryModel.id == "req-hist-100")
        db_res = await session.execute(stmt)
        return db_res.scalar_one_or_none()

    data_store = storage_engine.data
    hist_entry = await data_store.execute_in_transaction(_query_history)

    assert hist_entry is not None
    assert hist_entry.id == "req-hist-100"
    assert hist_entry.profile_id == "prof-hist-1"
    assert hist_entry.action_type == "SEND"
    assert hist_entry.status == "SUCCESS"
    assert hist_entry.correlation_id == "corr-hist-100"
    assert hist_entry.driver_id == "connector-dummy"
    assert hist_entry.execution_time_ms > 0.0

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_rbac_capability_authorization(tmp_path) -> None:
    """15. Test RBAC capability permission enforcement on execute_action."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "rbac_storage"))
    connector_engine = ConnectorEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(connector_engine)
    await kernel.boot()

    driver = DummyConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        profile_id="prof-rbac-1",
        name="RBAC Profile",
        driver_id="connector-dummy",
    )
    await connector_engine.profile_manager.register_profile(profile)

    # 1. Unauthorized request (missing kortex.connector.action.execute permission)
    req_unauth = ActionRequest(
        request_id="req-rbac-unauth",
        profile_id="prof-rbac-1",
        action_type="SEND",
        options={"granted_permissions": ["kortex.workflow.execute"]},
    )
    with pytest.raises(ConnectorSecurityError) as exc_info:
        await connector_engine.execute_action(req_unauth)

    assert "missing required permission" in str(exc_info.value)

    # 2. Authorized request (granted kortex.connector.action.execute permission)
    req_auth = ActionRequest(
        request_id="req-rbac-auth",
        profile_id="prof-rbac-1",
        action_type="SEND",
        options={"granted_permissions": ["kortex.connector.action.execute", "kortex.workflow.execute"]},
    )
    res = await connector_engine.execute_action(req_auth)
    assert res.status == "SUCCESS"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_rate_limiter_cache_storage_and_concurrency(tmp_path) -> None:
    """16. Test TokenBucketRateLimiter with Storage Engine ICacheStore, capacity, refill, and concurrency."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "rate_storage"))

    kernel.register_engine(storage_engine)
    await kernel.boot()

    cache_store = storage_engine.cache
    limiter = TokenBucketRateLimiter(cache_store=cache_store, default_capacity=2.0, default_refill_rate=1.0)

    # Acquire initial 2 tokens
    assert await limiter.acquire_token("key-1", tokens=1.0) is True
    assert await limiter.acquire_token("key-1", tokens=1.0) is True

    # 3rd token request exceeds burst capacity (2.0)
    assert await limiter.acquire_token("key-1", tokens=1.0) is False

    # Concurrent token acquisition test
    results = await asyncio.gather(
        limiter.acquire_token("key-concurrent", tokens=1.0, capacity=5.0),
        limiter.acquire_token("key-concurrent", tokens=1.0, capacity=5.0),
        limiter.acquire_token("key-concurrent", tokens=1.0, capacity=5.0),
    )
    assert all(results)

    await kernel.shutdown()
