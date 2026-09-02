"""Unit tests for DummyConnectorDriver (Milestone 3).

Target: 100% pass rate, 100% line coverage for kortex.engines.connector.drivers.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.exceptions import DriverExecutionError
from kortex.engines.connector.interfaces import IBaseConnectorDriver
from kortex.engines.connector.loader import ConnectorDriverLoader
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorCapability,
    ConnectorProfile,
)
from kortex.engines.connector.registry import ConnectorDriverRegistry


def test_dummy_driver_metadata() -> None:
    """Test DummyConnectorDriver metadata properties and correctness."""
    driver = DummyConnectorDriver()
    meta = driver.metadata

    assert meta.driver_id == "connector-dummy"
    assert meta.display_name == "Reference Dummy Connector Driver"
    assert meta.vendor == "KORTEX"
    assert meta.author == "KORTEX Core Team"
    assert meta.version == "1.0.0"
    assert meta.license == "MIT"
    assert meta.is_sandboxed is True

    assert ConnectorActionType.SEND in meta.supported_actions
    assert ConnectorActionType.RECEIVE in meta.supported_actions
    assert ConnectorActionType.FETCH in meta.supported_actions
    assert ConnectorActionType.PUSH in meta.supported_actions
    assert ConnectorActionType.VERIFY in meta.supported_actions

    assert ConnectorCapability.SEND in meta.supported_capabilities
    assert ConnectorCapability.TEST_CONNECTION in meta.supported_capabilities


def test_dummy_driver_protocol_and_abc_compliance() -> None:
    """Test DummyConnectorDriver inheritance and protocol checks."""
    driver = DummyConnectorDriver()

    assert isinstance(driver, BaseConnectorDriver)
    assert isinstance(driver, IBaseConnectorDriver)
    assert driver.driver_id == "connector-dummy"
    assert driver.supports_action(ConnectorActionType.SEND) is True
    assert driver.supports_action(ConnectorActionType.VERIFY) is True


@pytest.mark.asyncio
async def test_execute_all_supported_actions() -> None:
    """Test deterministic mock execution for every supported action type."""
    driver = DummyConnectorDriver()

    for action in [
        ConnectorActionType.SEND,
        ConnectorActionType.RECEIVE,
        ConnectorActionType.FETCH,
        ConnectorActionType.PUSH,
        ConnectorActionType.VERIFY,
    ]:
        req = ActionRequest(
            request_id=f"req-{action.value}",
            profile_id="prof-dummy",
            action_type=action,
            payload={"key": "value"},
            correlation_id="corr-123",
        )

        res = await driver.execute_action(req, secret_token="token-abc")
        assert isinstance(res, ActionResult)
        assert res.request_id == f"req-{action.value}"
        assert res.status == "SUCCESS"
        assert res.correlation_id == "corr-123"
        assert res.execution_time_ms >= 0.0
        assert res.error_details is None

        payload = res.response_payload
        assert payload["action"] == action.value
        assert payload["status"] == "executed"
        assert payload["echo_payload"] == {"key": "value"}
        assert payload["correlation_id"] == "corr-123"
        assert payload["mock_driver_id"] == "connector-dummy"
        assert payload["secret_authenticated"] is True


@pytest.mark.asyncio
async def test_execute_without_secret_token() -> None:
    """Test mock execution without providing secret_token."""
    driver = DummyConnectorDriver()
    req = ActionRequest(
        request_id="req-no-token",
        profile_id="prof-dummy",
        action_type=ConnectorActionType.SEND,
    )
    res = await driver.execute_action(req, secret_token=None)
    assert res.status == "SUCCESS"
    assert res.response_payload["secret_authenticated"] is False


@pytest.mark.asyncio
async def test_simulated_failure_options() -> None:
    """Test simulated failure using should_fail or simulated_error flags."""
    driver = DummyConnectorDriver()

    # Failure via options should_fail
    req1 = ActionRequest(
        request_id="req-fail-1",
        profile_id="prof-dummy",
        action_type=ConnectorActionType.SEND,
        options={"should_fail": True},
    )
    res1 = await driver.execute_action(req1)
    assert res1.status == "FAILED"
    assert res1.error_details is not None
    assert "Simulated driver execution failure" in res1.error_details["error"]

    # Failure via options simulated_error
    req2 = ActionRequest(
        request_id="req-fail-2",
        profile_id="prof-dummy",
        action_type=ConnectorActionType.SEND,
        options={"simulated_error": "Custom API timeout error"},
    )
    res2 = await driver.execute_action(req2)
    assert res2.status == "FAILED"
    assert res2.error_details is not None
    assert res2.error_details["error"] == "Custom API timeout error"

    # Failure via payload should_fail
    req3 = ActionRequest(
        request_id="req-fail-3",
        profile_id="prof-dummy",
        action_type=ConnectorActionType.SEND,
        payload={"should_fail": True},
    )
    res3 = await driver.execute_action(req3)
    assert res3.status == "FAILED"


@pytest.mark.asyncio
async def test_unsupported_action_raises_error() -> None:
    """Test that executing an unsupported action raises DriverExecutionError."""
    driver = DummyConnectorDriver()

    # Mock action request with invalid supports_action return
    class UnsupportedActionRequest(ActionRequest):
        pass

    req = ActionRequest(
        request_id="req-unsupported",
        profile_id="prof-dummy",
        action_type=ConnectorActionType.SEND,
    )

    # Temporarily monkeypatch supports_action to return False
    original_supports = driver.supports_action
    driver.supports_action = lambda action_type: False  # type: ignore[assignment]

    try:
        with pytest.raises(DriverExecutionError) as exc_info:
            await driver.execute_action(req)
        assert "not supported by driver" in exc_info.value.message
    finally:
        driver.supports_action = original_supports


@pytest.mark.asyncio
async def test_test_connection() -> None:
    """Test connectivity check under active, inactive, and simulated failure scenarios."""
    driver = DummyConnectorDriver()

    # Active profile
    prof_active = ConnectorProfile(profile_id="p1", name="Active", driver_id="connector-dummy")
    assert await driver.test_connection(prof_active) is True

    # Inactive profile
    prof_inactive = ConnectorProfile(profile_id="p2", name="Inactive", driver_id="connector-dummy", is_active=False)
    assert await driver.test_connection(prof_inactive) is False

    # Simulated connection failure option
    prof_sim_fail = ConnectorProfile(
        profile_id="p3",
        name="SimFail",
        driver_id="connector-dummy",
        options={"simulate_connection_failure": True},
    )
    assert await driver.test_connection(prof_sim_fail) is False


def test_input_models_immutability() -> None:
    """Verify input ActionRequest and ConnectorProfile models are immutable."""
    req = ActionRequest(
        request_id="req-imm",
        profile_id="prof-imm",
        action_type=ConnectorActionType.SEND,
    )
    with pytest.raises(ValidationError):
        req.request_id = "new-id"  # type: ignore[misc]

    prof = ConnectorProfile(profile_id="p-imm", name="Imm", driver_id="connector-dummy")
    with pytest.raises(ValidationError):
        prof.name = "New Name"  # type: ignore[misc]


def test_registry_integration() -> None:
    """Test registering DummyConnectorDriver in ConnectorDriverRegistry."""
    registry = ConnectorDriverRegistry()
    driver = DummyConnectorDriver()

    registered = registry.register_driver(driver)
    assert registered.driver_id == "connector-dummy"
    assert registry.get_driver("connector-dummy") == driver

    # Discovery by action and capability
    action_drivers = registry.find_drivers_for_action(ConnectorActionType.SEND)
    assert len(action_drivers) == 1
    assert action_drivers[0].driver_id == "connector-dummy"


def test_loader_integration() -> None:
    """Test dynamically loading and discovering DummyConnectorDriver via ConnectorDriverLoader."""
    loader = ConnectorDriverLoader()

    # Dynamic class loading
    driver = loader.load_driver(
        module_path="kortex.engines.connector.drivers.dummy_driver",
        class_name="DummyConnectorDriver",
    )
    assert isinstance(driver, DummyConnectorDriver)
    assert driver.driver_id == "connector-dummy"

    # Package discovery
    metadata_list = loader.discover_drivers("kortex.engines.connector.drivers")
    assert any(m.driver_id == "connector-dummy" for m in metadata_list)
