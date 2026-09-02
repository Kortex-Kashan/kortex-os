"""Unit tests for Connector Engine models, enums, exceptions, interfaces, and base driver (Milestone 1).

Target: 100% pass rate, 100% code coverage across Milestone 1 production files.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.exceptions import (
    ConnectorConnectionError,
    ConnectorDriverError,
    ConnectorEngineError,
    ConnectorOperationError,
    ConnectorProfileNotFoundError,
    ConnectorSecurityError,
    ConnectorValidationError,
    DriverExecutionError,
    DriverLoadError,
    DriverNotFoundError,
    RateLimitExceededError,
)
from kortex.engines.connector.interfaces import (
    IBaseConnectorDriver,
    IConnectorEngine,
)
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorCapability,
    ConnectorPipelineDefinition,
    ConnectorProfile,
    ConnectorStatus,
    DriverMetadata,
    PipelineStage,
)

# --- Enum Tests ---


def test_enum_values() -> None:
    """Verify enum string values and enum representation."""
    assert ConnectorActionType.SEND.value == "SEND"
    assert ConnectorActionType.RECEIVE.value == "RECEIVE"
    assert ConnectorActionType.FETCH.value == "FETCH"
    assert ConnectorActionType.PUSH.value == "PUSH"
    assert ConnectorActionType.VERIFY.value == "VERIFY"

    assert ConnectorCapability.SEND.value == "SEND"
    assert ConnectorCapability.TEST_CONNECTION.value == "TEST_CONNECTION"
    assert ConnectorCapability.AUTHENTICATE.value == "AUTHENTICATE"
    assert ConnectorCapability.WEBHOOK.value == "WEBHOOK"
    assert ConnectorCapability.STREAMING.value == "STREAMING"

    assert ConnectorStatus.HEALTHY.value == "HEALTHY"
    assert ConnectorStatus.DEGRADED.value == "DEGRADED"
    assert ConnectorStatus.UNHEALTHY.value == "UNHEALTHY"
    assert ConnectorStatus.DISCONNECTED.value == "DISCONNECTED"


def test_enum_conversions() -> None:
    """Verify string to enum parsing."""
    assert ConnectorActionType("SEND") == ConnectorActionType.SEND
    assert ConnectorCapability("WEBHOOK") == ConnectorCapability.WEBHOOK
    assert ConnectorStatus("HEALTHY") == ConnectorStatus.HEALTHY

    with pytest.raises(ValueError):
        ConnectorActionType("INVALID_ACTION")


# --- Model Tests ---


def test_driver_metadata_model() -> None:
    """Test DriverMetadata model creation, defaults, and immutability."""
    meta = DriverMetadata(
        driver_id="drv-dummy",
        display_name="Dummy Driver",
        vendor="KORTEX",
        author="Engineering Team",
        version="1.0.0",
        description="Reference dummy driver plugin",
        supported_actions=[ConnectorActionType.SEND, ConnectorActionType.FETCH],
        supported_capabilities=[ConnectorCapability.SEND, ConnectorCapability.TEST_CONNECTION],
    )

    assert meta.driver_id == "drv-dummy"
    assert meta.is_sandboxed is True
    assert meta.license == "MIT"
    assert meta.homepage is None
    assert ConnectorActionType.SEND in meta.supported_actions

    # Test immutability
    with pytest.raises(ValidationError):
        meta.driver_id = "new-id"  # type: ignore[misc]


def test_driver_metadata_validation() -> None:
    """Test missing required fields in DriverMetadata."""
    with pytest.raises(ValidationError):
        DriverMetadata(
            driver_id="drv-1",
            display_name="Incomplete",
            # missing vendor, author, version, description
        )  # type: ignore[call-arg]


def test_connector_profile_model() -> None:
    """Test ConnectorProfile model defaults, serialization, and immutability."""
    profile = ConnectorProfile(
        profile_id="prof-100",
        name="Production REST API",
        driver_id="drv-http",
    )

    assert profile.profile_id == "prof-100"
    assert profile.secret_handle is None
    assert profile.rate_limit_per_sec == 10.0
    assert profile.max_retries == 3
    assert profile.options == {}
    assert profile.is_active is True

    # Dump and validate roundtrip
    dumped = profile.model_dump()
    assert dumped["profile_id"] == "prof-100"
    restored = ConnectorProfile.model_validate(dumped)
    assert restored == profile

    # Immutability
    with pytest.raises(ValidationError):
        profile.name = "Updated Name"  # type: ignore[misc]


def test_action_request_model() -> None:
    """Test ActionRequest model creation and immutability."""
    req = ActionRequest(
        request_id="req-001",
        profile_id="prof-100",
        action_type=ConnectorActionType.SEND,
        payload={"data": "test"},
    )

    assert req.request_id == "req-001"
    assert req.action_type == ConnectorActionType.SEND
    assert req.payload == {"data": "test"}
    assert req.tenant_id == "default"
    assert req.correlation_id is None

    with pytest.raises(ValidationError):
        req.tenant_id = "tenant-2"  # type: ignore[misc]


def test_action_result_model() -> None:
    """Test ActionResult model defaults and fields."""
    res = ActionResult(
        request_id="req-001",
        status="SUCCESS",
        response_payload={"result": "ok"},
        execution_time_ms=12.5,
    )

    assert res.request_id == "req-001"
    assert res.status == "SUCCESS"
    assert res.execution_time_ms == 12.5
    assert res.error_details is None

    with pytest.raises(ValidationError):
        res.status = "FAILED"  # type: ignore[misc]


def test_pipeline_stage_model() -> None:
    """Test PipelineStage model."""
    stage = PipelineStage(
        stage_id="stg-auth",
        stage_type="AUTHENTICATION",
        stage_options={"header": "Bearer"},
    )

    assert stage.stage_id == "stg-auth"
    assert stage.is_optional is False
    assert stage.stage_options == {"header": "Bearer"}


def test_connector_pipeline_definition_model() -> None:
    """Test ConnectorPipelineDefinition model."""
    stage = PipelineStage(stage_id="stg-1", stage_type="RATE_LIMIT")
    pipeline = ConnectorPipelineDefinition(
        pipeline_id="pipe-1",
        profile_id="prof-100",
        stages=[stage],
    )

    assert pipeline.pipeline_id == "pipe-1"
    assert len(pipeline.stages) == 1
    assert pipeline.stages[0].stage_id == "stg-1"


# --- Exception Tests ---


def test_exception_hierarchy() -> None:
    """Test exception inheritance and parameter initialization."""
    base_err = ConnectorEngineError("Engine failed", details={"code": 500})
    assert str(base_err) == "Engine failed"
    assert base_err.message == "Engine failed"
    assert base_err.details == {"code": 500}

    # Verify inheritance
    assert issubclass(ConnectorOperationError, ConnectorEngineError)
    assert issubclass(ConnectorDriverError, ConnectorEngineError)
    assert issubclass(DriverNotFoundError, ConnectorDriverError)
    assert issubclass(DriverExecutionError, ConnectorDriverError)
    assert issubclass(DriverLoadError, ConnectorDriverError)
    assert issubclass(ConnectorProfileNotFoundError, ConnectorEngineError)
    assert issubclass(ConnectorValidationError, ConnectorEngineError)
    assert issubclass(RateLimitExceededError, ConnectorEngineError)
    assert issubclass(ConnectorSecurityError, ConnectorEngineError)
    assert issubclass(ConnectorConnectionError, ConnectorEngineError)


def test_raising_exceptions() -> None:
    """Test raising custom exceptions with message and details."""
    with pytest.raises(DriverNotFoundError) as exc_info:
        raise DriverNotFoundError("Driver missing", details={"driver_id": "drv-null"})

    err = exc_info.value
    assert isinstance(err, ConnectorDriverError)
    assert isinstance(err, ConnectorEngineError)
    assert err.message == "Driver missing"
    assert err.details == {"driver_id": "drv-null"}


# --- BaseConnectorDriver ABC Tests ---


class ConcreteDummyDriver(BaseConnectorDriver):
    """Valid concrete implementation of BaseConnectorDriver for testing."""

    @property
    def metadata(self) -> DriverMetadata:
        return DriverMetadata(
            driver_id="drv-dummy-test",
            display_name="Dummy Test Driver",
            vendor="KORTEX",
            author="Tester",
            version="1.0.0",
            description="Concrete dummy driver for unit testing",
            supported_actions=[ConnectorActionType.SEND, ConnectorActionType.VERIFY],
            supported_capabilities=[ConnectorCapability.SEND],
        )

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(
            request_id=request.request_id,
            status="SUCCESS",
            response_payload={"echo": request.payload},
        )

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


def test_base_connector_driver_abc_enforcement() -> None:
    """Verify that BaseConnectorDriver cannot be instantiated directly or with missing abstract methods."""
    with pytest.raises(TypeError):
        BaseConnectorDriver()  # type: ignore[abstract]

    class IncompleteDriver(BaseConnectorDriver):
        pass

    with pytest.raises(TypeError):
        IncompleteDriver()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_concrete_connector_driver_behavior() -> None:
    """Test helper properties and method calls on a valid concrete driver."""
    driver = ConcreteDummyDriver()

    assert driver.driver_id == "drv-dummy-test"
    assert driver.supported_actions == [ConnectorActionType.SEND, ConnectorActionType.VERIFY]
    assert driver.supports_action(ConnectorActionType.SEND) is True
    assert driver.supports_action(ConnectorActionType.RECEIVE) is False

    req = ActionRequest(
        request_id="req-t1",
        profile_id="prof-100",
        action_type=ConnectorActionType.SEND,
        payload={"hello": "world"},
    )
    result = await driver.execute_action(req)
    assert result.status == "SUCCESS"
    assert result.response_payload == {"echo": {"hello": "world"}}

    prof = ConnectorProfile(profile_id="prof-100", name="Test", driver_id="drv-dummy-test")
    connected = await driver.test_connection(prof)
    assert connected is True


# --- Protocol Runtime Compatibility Tests ---


def test_protocol_runtime_checks() -> None:
    """Test @runtime_checkable protocol compatibility."""
    driver = ConcreteDummyDriver()

    assert isinstance(driver, IBaseConnectorDriver)

    class DummyEngine:
        async def execute_action(self, request: ActionRequest) -> ActionResult:
            return ActionResult(request_id=request.request_id, status="SUCCESS")

        def register_driver(self, driver: IBaseConnectorDriver) -> None:
            pass

        def list_drivers(self) -> list[DriverMetadata]:
            return []

        async def get_profile(self, profile_id: str) -> ConnectorProfile:
            return ConnectorProfile(profile_id=profile_id, name="P", driver_id="D")

    engine = DummyEngine()
    assert isinstance(engine, IConnectorEngine)

    class InvalidEngine:
        pass

    assert not isinstance(InvalidEngine(), IConnectorEngine)
