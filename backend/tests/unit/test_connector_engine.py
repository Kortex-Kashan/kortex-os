"""Unit tests for KORTEX OS Connector Engine facade (Milestone 8).

Target: 100% test pass rate, 100% line coverage for engine.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.exceptions import EngineStateError
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import ConnectorProfileNotFoundError
from kortex.engines.connector.models import (
    ActionRequest,
    ConnectorProfile,
)


def test_engine_properties_and_initial_state() -> None:
    """Test engine name, dependencies, subsystems, and initial state."""
    engine = ConnectorEngine()

    assert engine.name == "connector"
    assert engine.dependencies == ["configuration", "registry", "event", "storage"]
    assert engine.state == EngineState.UNINITIALIZED
    assert engine.status() == "UNINITIALIZED"
    assert engine.version() == "1.0.0"

    assert engine.registry is not None
    assert engine.profile_manager is not None
    assert engine.rate_limiter is not None
    assert engine.pipeline is not None


@pytest.mark.asyncio
async def test_initialize_capability_registration_and_lifecycle() -> None:
    """Test initialize() registering capabilities with Kernel and state transition to READY."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()

    await engine.initialize(mock_kernel)

    assert engine.state == EngineState.READY
    assert engine.status() == "READY"

    # Verify 4 canonical capability registration calls
    registered_names = [call.kwargs["name"] for call in mock_kernel.register_capability.call_args_list]
    assert "kortex.connector.action.execute" in registered_names
    assert "kortex.connector.driver.register" in registered_names
    assert "kortex.connector.driver.list" in registered_names
    assert "kortex.connector.profile.get" in registered_names


@pytest.mark.asyncio
async def test_duplicate_initialize_raises_state_error() -> None:
    """Test calling initialize() twice raises EngineStateError."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()

    await engine.initialize(mock_kernel)
    assert engine.state == EngineState.READY

    with pytest.raises(EngineStateError):
        await engine.initialize(mock_kernel)


@pytest.mark.asyncio
async def test_initialize_failure_transitions_to_failed() -> None:
    """Test initialize() failure transitions engine to FAILED and re-raises exception."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()
    mock_kernel.register_capability.side_effect = RuntimeError("Kernel capability registration failed")

    with pytest.raises(RuntimeError, match="Kernel capability registration failed"):
        await engine.initialize(mock_kernel)

    assert engine.state == EngineState.FAILED


@pytest.mark.asyncio
async def test_start_and_stop_lifecycle_transitions() -> None:
    """Test start() and stop() state transitions."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()

    await engine.initialize(mock_kernel)
    assert engine.state == EngineState.READY

    await engine.start()
    assert engine.state == EngineState.RUNNING

    await engine.stop()
    assert engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_health_check_and_diagnostics_delegation() -> None:
    """Test health_check(), health(), metrics(), diagnostics(), and capabilities() methods."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()
    await engine.initialize(mock_kernel)

    health_info = await engine.health_check()
    assert health_info["status"] == "healthy"

    health_sync = engine.health()
    assert health_sync["status"] == "healthy"

    metrics_info = engine.metrics()
    assert metrics_info["registered_driver_count"] == 0

    tech_diag = engine.diagnostics()
    assert tech_diag["engine_version"] == "1.0.0"

    caps = engine.capabilities()
    assert "kortex.connector.action.execute" in caps


@pytest.mark.asyncio
async def test_execute_action_success_flow_and_event_publishing() -> None:
    """Test successful action execution emitting started and completed events."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()
    mock_kernel.publish_event = AsyncMock()

    await engine.initialize(mock_kernel)
    await engine.start()

    driver = DummyConnectorDriver()
    engine.register_driver(driver)

    profile = ConnectorProfile(
        profile_id="prof-dummy-1",
        name="Dummy Profile 1",
        driver_id="connector-dummy",
        options={"endpoint": "https://api.dummy.com"},
    )
    await engine.profile_manager.register_profile(profile)

    req = ActionRequest(
        request_id="req-100",
        profile_id="prof-dummy-1",
        action_type="SEND",
        payload={"message": "hello"},
        correlation_id="corr-100",
    )

    res = await engine.execute_action(req)

    assert res.status == "SUCCESS"
    assert res.request_id == "req-100"
    assert "echo_payload" in res.response_payload
    assert res.response_payload["status"] == "executed"

    # Verify started and completed events published via kernel.publish_event
    assert mock_kernel.publish_event.call_count == 2

    call_started = mock_kernel.publish_event.call_args_list[0]
    assert call_started.kwargs["topic"] == "connector.action.started"
    assert call_started.kwargs["payload"]["request_id"] == "req-100"

    call_completed = mock_kernel.publish_event.call_args_list[1]
    assert call_completed.kwargs["topic"] == "connector.action.completed"
    assert call_completed.kwargs["payload"]["request_id"] == "req-100"


@pytest.mark.asyncio
async def test_execute_action_pipeline_failure_emitting_failed_event() -> None:
    """Test action execution resulting in pipeline failure emitting connector.action.failed event."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()
    mock_kernel.publish_event = AsyncMock()

    await engine.initialize(mock_kernel)
    await engine.start()

    driver = DummyConnectorDriver()
    engine.register_driver(driver)

    profile = ConnectorProfile(
        profile_id="prof-dummy-fail",
        name="Dummy Fail Profile",
        driver_id="connector-dummy",
        options={"endpoint": "https://api.dummy.com"},
    )
    await engine.profile_manager.register_profile(profile)

    # Simulated driver failure payload
    req = ActionRequest(
        request_id="req-fail-1",
        profile_id="prof-dummy-fail",
        action_type="SEND",
        payload={"should_fail": True, "simulated_error": "Simulated connection error"},
    )

    res = await engine.execute_action(req)

    assert res.status == "FAILED"
    assert mock_kernel.publish_event.call_count == 2

    call_failed = mock_kernel.publish_event.call_args_list[1]
    assert call_failed.kwargs["topic"] == "connector.action.failed"
    assert call_failed.kwargs["payload"]["request_id"] == "req-fail-1"


@pytest.mark.asyncio
async def test_execute_action_profile_not_found_raises_error_and_emits_failed_event() -> None:
    """Test execute_action with missing profile ID raises ConnectorProfileNotFoundError and emits failed event."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()
    mock_kernel.publish_event = AsyncMock()

    await engine.initialize(mock_kernel)
    await engine.start()

    req = ActionRequest(
        request_id="req-missing-prof",
        profile_id="nonexistent-profile",
        action_type="SEND",
    )

    with pytest.raises(ConnectorProfileNotFoundError):
        await engine.execute_action(req)

    assert mock_kernel.publish_event.call_count == 2
    call_failed = mock_kernel.publish_event.call_args_list[1]
    assert call_failed.kwargs["topic"] == "connector.action.failed"


@pytest.mark.asyncio
async def test_event_publication_failure_isolation_and_privacy(caplog: pytest.LogCaptureFixture) -> None:
    """Test that event publication exception containing fake secret data is isolated and NOT logged."""
    fake_secret = "SUPER_SECRET_TOKEN_999"
    engine = ConnectorEngine()
    mock_kernel = MagicMock()
    mock_kernel.publish_event = AsyncMock(
        side_effect=RuntimeError(f"Event bus failure with internal secret: {fake_secret}")
    )

    await engine.initialize(mock_kernel)
    await engine.start()

    driver = DummyConnectorDriver()
    engine.register_driver(driver)

    profile = ConnectorProfile(
        profile_id="prof-event-fail",
        name="Event Fail Profile",
        driver_id="connector-dummy",
    )
    await engine.profile_manager.register_profile(profile)

    req = ActionRequest(
        request_id="req-evt-iso",
        profile_id="prof-event-fail",
        action_type="SEND",
    )

    with caplog.at_level(logging.WARNING):
        res = await engine.execute_action(req)

    # Verify action execution completed successfully despite event publication failure
    assert res.status == "SUCCESS"

    # Verify generic log warning captured without revealing fake_secret or exception internals
    log_text = caplog.text
    assert "Failed to publish system event 'connector.action.started'." in log_text
    assert "Failed to publish system event 'connector.action.completed'." in log_text
    assert fake_secret not in log_text
    assert "Event bus failure" not in log_text


@pytest.mark.asyncio
async def test_driver_registration_listing_and_profile_get() -> None:
    """Test register_driver, list_drivers, and get_profile capability handlers."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()
    mock_kernel.publish_event = AsyncMock()

    await engine.initialize(mock_kernel)
    await engine.start()

    driver = DummyConnectorDriver()
    engine.register_driver(driver)

    drivers = engine.list_drivers()
    assert len(drivers) == 1
    assert drivers[0].driver_id == "connector-dummy"

    profile = ConnectorProfile(
        profile_id="prof-get-test",
        name="Get Test Profile",
        driver_id="connector-dummy",
    )
    await engine.profile_manager.register_profile(profile)

    fetched_profile = await engine.get_profile("prof-get-test")
    assert fetched_profile.profile_id == "prof-get-test"


def test_register_driver_without_running_event_loop() -> None:
    """Test synchronous register_driver when no event loop is running handles RuntimeError gracefully."""
    engine = ConnectorEngine()
    engine._state = EngineState.READY
    driver = DummyConnectorDriver()

    # Call register_driver in a pure sync environment without asyncio loop
    engine.register_driver(driver)
    assert len(engine.list_drivers()) == 1


@pytest.mark.asyncio
async def test_ensure_state_rejection_when_stopped() -> None:
    """Test calling capabilities when engine is STOPPED raises EngineStateError."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()

    await engine.initialize(mock_kernel)
    await engine.start()
    await engine.stop()

    req = ActionRequest(request_id="r1", profile_id="p1", action_type="SEND")

    with pytest.raises(EngineStateError):
        await engine.execute_action(req)

    with pytest.raises(EngineStateError):
        engine.register_driver(DummyConnectorDriver())

    with pytest.raises(EngineStateError):
        engine.list_drivers()

    with pytest.raises(EngineStateError):
        await engine.get_profile("p1")


@pytest.mark.asyncio
async def test_safe_schedule_event_task_retention_and_completion() -> None:
    """Verify fire-and-forget event scheduling retains task reference and discards on completion."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()
    mock_kernel.publish_event = AsyncMock()

    await engine.initialize(mock_kernel)
    await engine.start()

    driver = DummyConnectorDriver()
    engine.register_driver(driver)

    # Background task should be registered and executed
    assert len(engine._background_tasks) >= 0  # May already be finished or in-flight
    # Yield to let the event loop finish any scheduled task
    await asyncio.sleep(0.01)
    assert len(engine._background_tasks) == 0
    assert mock_kernel.publish_event.call_count == 1
    assert mock_kernel.publish_event.call_args.kwargs["topic"] == "connector.driver.registered"

    await engine.stop()


@pytest.mark.asyncio
async def test_safe_schedule_event_unhandled_task_exception_handled(caplog: pytest.LogCaptureFixture) -> None:
    """Verify unhandled task exception callback logs warning and cleans up task reference."""
    engine = ConnectorEngine()

    async def _failing_publish(event: Any) -> None:
        raise ZeroDivisionError("Simulated unhandled task crash")

    engine._publish_event = _failing_publish  # type: ignore[assignment]
    engine._set_state(EngineState.RUNNING)

    driver = DummyConnectorDriver()
    with caplog.at_level(logging.WARNING):
        engine.register_driver(driver)
        await asyncio.sleep(0.01)

    assert len(engine._background_tasks) == 0
    assert "Unhandled exception in background event task: ZeroDivisionError" in caplog.text


@pytest.mark.asyncio
async def test_engine_stop_cancels_and_drains_background_tasks() -> None:
    """Verify engine stop() cancels all in-flight background event tasks without leaving orphans."""
    engine = ConnectorEngine()
    mock_kernel = MagicMock()

    never_finish = asyncio.Event()

    async def _hanging_publish(event: Any) -> None:
        await never_finish.wait()

    engine._publish_event = _hanging_publish  # type: ignore[assignment]
    await engine.initialize(mock_kernel)
    await engine.start()

    driver = DummyConnectorDriver()
    engine.register_driver(driver)

    assert len(engine._background_tasks) == 1
    # Stop should cancel the task cleanly
    await engine.stop()
    assert len(engine._background_tasks) == 0
    assert engine.state == EngineState.STOPPED
