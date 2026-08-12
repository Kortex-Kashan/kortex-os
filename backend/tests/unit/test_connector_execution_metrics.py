"""Unit tests for Connector Pipeline Stage & Retry Attempt Metric Instrumentation (Sub-Milestone 10.5.2a).

Tests verify stage metric mapping, retry attempt tracking closures, HTTP status inspection,
diagnostic failure isolation, cancellation safety, and single-counting ownership boundaries.
"""

from __future__ import annotations

import asyncio
import json
import pytest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from kortex.engines.connector.diagnostics import ConnectorDiagnostics
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.exceptions import (
    ConnectorOperationError,
    DriverExecutionError,
    DriverNotFoundError,
)
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorProfile,
    DriverMetadata,
)
from kortex.engines.connector.pipeline import ConnectorPipeline
from kortex.engines.connector.registry import ConnectorDriverRegistry


class MockableDriver(DummyConnectorDriver):
    """Driver subclass for testing pipeline execution and metrics."""

    def __init__(self, driver_id: str = "driver-test", side_effect: Any = None, return_value: Any = None) -> None:
        super().__init__()
        self._custom_driver_id = driver_id
        self._mock = AsyncMock(side_effect=side_effect, return_value=return_value)

    @property
    def metadata(self) -> DriverMetadata:
        return DriverMetadata(
            driver_id=self._custom_driver_id,
            display_name="Mock Driver",
            vendor="KORTEX",
            author="Tests",
            version="1.0.0",
            description="Mock driver for metrics testing",
            supported_actions=[
                ConnectorActionType.SEND,
                ConnectorActionType.RECEIVE,
                ConnectorActionType.FETCH,
                ConnectorActionType.PUSH,
                ConnectorActionType.VERIFY,
            ],
            supported_capabilities=[],
        )

    async def execute_action(
        self, request: ActionRequest, secret_token: str | None = None
    ) -> ActionResult:
        return await self._mock(request, secret_token)


@pytest.fixture
def registry() -> ConnectorDriverRegistry:
    return ConnectorDriverRegistry()


@pytest.fixture
def diagnostics(registry: ConnectorDriverRegistry) -> ConnectorDiagnostics:
    return ConnectorDiagnostics(registry=registry)


@pytest.fixture
def sample_profile() -> ConnectorProfile:
    return ConnectorProfile(
        profile_id="prof-test-1",
        name="Test Profile",
        driver_id="driver-test",
        max_retries=3,
        is_active=True,
    )


@pytest.fixture
def sample_request() -> ActionRequest:
    return ActionRequest(
        request_id="req-test-1",
        profile_id="prof-test-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "https://api.example.com/data"},
    )


@pytest.mark.asyncio
async def test_diagnostics_none_preserves_pipeline_behavior(
    registry: ConnectorDriverRegistry, sample_profile: ConnectorProfile, sample_request: ActionRequest
) -> None:
    """1. Verify diagnostics=None preserves existing Pipeline execution behavior."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=None)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "SUCCESS"
    assert result.response_payload["status_code"] == 200


@pytest.mark.asyncio
async def test_first_attempt_success_retry_count_zero(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """2. Verify first-attempt success results in retry_count remaining 0."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "SUCCESS"
    m = diagnostics.metrics()
    assert m["retry_count"] == 0
    assert m["http_status_codes"] == {200: 1}


@pytest.mark.asyncio
async def test_success_after_retries(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """3. Verify success after 2 retries records retry_count = 2."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=[
            DriverExecutionError("Attempt 1 failure"),
            DriverExecutionError("Attempt 2 failure"),
            ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
        ],
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "SUCCESS"
    m = diagnostics.metrics()
    assert m["retry_count"] == 2
    assert m["http_status_codes"] == {200: 1}


@pytest.mark.asyncio
async def test_retry_exhaustion(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """4. Verify retry exhaustion records total retries performed and driver failure."""
    # max_retries = 3 -> 4 total physical attempts
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=DriverExecutionError("Persistent network error"),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["retry_count"] == 3
    assert m["driver_failures"] == 1
    assert m["error_categories"]["driver_execution"] == 1


@pytest.mark.asyncio
async def test_non_retryable_driver_exception(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """5. Verify non-retryable exception fails immediately with retry_count = 0."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=ValueError("Non-retryable input error"),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["retry_count"] == 0
    assert m["driver_failures"] == 1
    assert m["error_categories"]["driver_execution"] == 1


@pytest.mark.asyncio
async def test_driver_not_found_error(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """6. Verify DriverNotFoundError records driver_not_found error category."""
    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["driver_failures"] == 1
    assert m["error_categories"]["driver_not_found"] == 1


@pytest.mark.asyncio
async def test_authentication_failure_metrics(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
) -> None:
    """7. Verify missing or failing secret_resolver records authentication failure."""
    profile_with_secret = ConnectorProfile(
        profile_id="prof-sec-1",
        name="Secret Profile",
        driver_id="driver-test",
        secret_handle="vault:secret-key",
        is_active=True,
    )
    request = ActionRequest(
        request_id="req-sec-1",
        profile_id="prof-sec-1",
        action_type=ConnectorActionType.FETCH,
    )

    # 1. Missing secret_resolver
    pipeline1 = ConnectorPipeline(registry=registry, secret_resolver=None, diagnostics=diagnostics)
    result1 = await pipeline1.execute(request, profile_with_secret)
    assert result1.status == "FAILED"

    # 2. Failing secret_resolver (raises exception)
    failing_resolver = AsyncMock(side_effect=RuntimeError("Vault connection error"))
    pipeline2 = ConnectorPipeline(registry=registry, secret_resolver=failing_resolver, diagnostics=diagnostics)
    result2 = await pipeline2.execute(request, profile_with_secret)
    assert result2.status == "FAILED"

    m = diagnostics.metrics()
    assert m["authentication_failures"] == 2
    assert m["error_categories"]["authentication"] == 2


@pytest.mark.asyncio
async def test_rate_limit_rejection_metrics(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """8. Verify Stage 3 rate limit token refusal records rate_limit_rejections."""
    mock_limiter = MagicMock()
    mock_limiter.acquire_token = AsyncMock(return_value=False)

    pipeline = ConnectorPipeline(registry=registry, rate_limiter=mock_limiter, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["rate_limit_rejections"] == 1
    assert m["error_categories"]["rate_limit"] == 1


@pytest.mark.asyncio
async def test_http_200_status_recording(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """9. Verify HTTP 200 response records status_code 200 without HTTP error category."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "SUCCESS"
    m = diagnostics.metrics()
    assert m["http_status_codes"] == {200: 1}
    assert m["error_categories"]["http_4xx"] == 0
    assert m["error_categories"]["http_5xx"] == 0
    assert m["driver_failures"] == 0


@pytest.mark.asyncio
async def test_http_404_status_recording(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """10. Verify HTTP 404 response records http_4xx error category and NOT driver_failure."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(
            request_id="req-test-1",
            status="FAILED",
            response_payload={"status_code": 404},
            error_details={"error": "Not Found"},
        ),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["http_status_codes"] == {404: 1}
    assert m["error_categories"]["http_4xx"] == 1
    # Valid HTTP failure payload is NOT a driver failure!
    assert m["driver_failures"] == 0


@pytest.mark.asyncio
async def test_http_429_status_recording(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """11. Verify HTTP 429 response records http_4xx error category and NOT driver_failure."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(
            request_id="req-test-1",
            status="FAILED",
            response_payload={"status_code": 429},
            error_details={"error": "Too Many Requests"},
        ),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["http_status_codes"] == {429: 1}
    assert m["error_categories"]["http_4xx"] == 1
    assert m["driver_failures"] == 0


@pytest.mark.asyncio
async def test_http_500_status_recording(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """12. Verify HTTP 500 response records http_5xx error category and NOT driver_failure."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(
            request_id="req-test-1",
            status="FAILED",
            response_payload={"status_code": "500"},  # String numeric convertable to int
            error_details={"error": "Internal Server Error"},
        ),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["http_status_codes"] == {500: 1}
    assert m["error_categories"]["http_5xx"] == 1
    assert m["driver_failures"] == 0


@pytest.mark.asyncio
async def test_generic_driver_exception_recording(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """13. Verify generic driver exception records driver_execution + driver_failure."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=RuntimeError("Driver crashed"),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["driver_failures"] == 1
    assert m["error_categories"]["driver_execution"] == 1


@pytest.mark.asyncio
async def test_diagnostics_recording_exception_isolation(
    registry: ConnectorDriverRegistry,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """14. Verify diagnostics recording exception does not alter or replace original ActionResult."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    mock_broken_diag = MagicMock()
    mock_broken_diag.record_http_status.side_effect = RuntimeError("Diagnostics storage failure")
    mock_broken_diag.record_retry.side_effect = RuntimeError("Diagnostics storage failure")

    pipeline = ConnectorPipeline(registry=registry, diagnostics=mock_broken_diag)
    result = await pipeline.execute(sample_request, sample_profile)

    # Pipeline execution MUST succeed cleanly despite diagnostics exception!
    assert result.status == "SUCCESS"
    assert result.response_payload["status_code"] == 200


@pytest.mark.asyncio
async def test_cancellation_does_not_cause_additional_retry(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """15. Verify asyncio.CancelledError re-raises immediately without causing retries."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=asyncio.CancelledError(),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)

    with pytest.raises(asyncio.CancelledError):
        await pipeline.execute(sample_request, sample_profile)

    m = diagnostics.metrics()
    assert m["retry_count"] == 0


@pytest.mark.asyncio
async def test_stage_metrics_do_not_alter_total_executions(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """16. Verify stage metric methods in Pipeline DO NOT alter top-level total_executions."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=[
            DriverExecutionError("Attempt 1 failure"),
            ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
        ],
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)
    result = await pipeline.execute(sample_request, sample_profile)

    assert result.status == "SUCCESS"
    m = diagnostics.metrics()
    # Stage/retry metrics recorded correctly:
    assert m["retry_count"] == 1
    assert m["http_status_codes"] == {200: 1}
    # Top-level outcome counters MUST remain 0 until ConnectorEngine.execute_action calls record_execution!
    assert m["total_executions"] == 0
    assert m["successful_executions"] == 0
    assert m["failed_executions"] == 0


@pytest.mark.asyncio
async def test_secret_tokens_and_handles_never_reach_diagnostics(
    registry: ConnectorDriverRegistry,
) -> None:
    """17. Verify secret tokens and handles never leak into diagnostic calls."""
    profile_with_secret = ConnectorProfile(
        profile_id="prof-sec-2",
        name="Secret Profile",
        driver_id="driver-test",
        secret_handle="vault:secret-token-super-sensitive",
        is_active=True,
    )
    sec_request = ActionRequest(
        request_id="req-sec-2",
        profile_id="prof-sec-2",
        action_type=ConnectorActionType.FETCH,
    )
    mock_resolver = AsyncMock(return_value="raw-secret-token-value-xyz")

    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-sec-2", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    mock_diag_spy = MagicMock()

    pipeline = ConnectorPipeline(
        registry=registry,
        secret_resolver=mock_resolver,
        diagnostics=mock_diag_spy,
    )
    result = await pipeline.execute(sec_request, profile_with_secret)

    assert result.status == "SUCCESS"

    # Inspect all calls made to mock_diag_spy
    for call in mock_diag_spy.method_calls:
        call_str = str(call)
        assert "vault:secret-token" not in call_str
        assert "raw-secret-token" not in call_str


@pytest.mark.asyncio
async def test_stage_1_profile_mismatch_and_inactive(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_request: ActionRequest,
) -> None:
    """18. Verify Stage 1 profile mismatch and inactive profile validation metrics."""
    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)

    # 1. Profile mismatch
    profile_mismatch = ConnectorProfile(profile_id="prof-mismatch", name="Diff", driver_id="d1")
    res1 = await pipeline.execute(sample_request, profile_mismatch)
    assert res1.status == "FAILED"

    # 2. Inactive profile
    profile_inactive = ConnectorProfile(profile_id="prof-test-1", name="Inactive", driver_id="d1", is_active=False)
    res2 = await pipeline.execute(sample_request, profile_inactive)
    assert res2.status == "FAILED"

    m = diagnostics.metrics()
    assert m["error_categories"]["unknown_error"] == 2
    assert m["authentication_failures"] == 0
    assert m["driver_failures"] == 0
    assert m["rate_limit_rejections"] == 0


@pytest.mark.asyncio
async def test_non_cancelled_base_exception_propagates_uncaught(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """19. Verify non-CancelledError BaseException propagates uncaught without being converted to ActionResult."""
    class CustomBaseException(BaseException):
        pass

    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=CustomBaseException("Critical system interrupt"),
    )
    registry.register_driver(mock_driver)

    pipeline = ConnectorPipeline(registry=registry, diagnostics=diagnostics)

    with pytest.raises(CustomBaseException):
        await pipeline.execute(sample_request, sample_profile)

    m = diagnostics.metrics()
    assert m["driver_failures"] == 0
    assert m["retry_count"] == 0


# =============================================================================
# ConnectorEngine Top-Level Execution & Cancellation Metrics Tests (10.5.2b)
# =============================================================================

from kortex.core.base_engine import EngineState, EngineStateError
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import ConnectorProfileNotFoundError, ConnectorSecurityError


@pytest.mark.asyncio
async def test_engine_metrics_scenario_a_success_execution(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario A: Successful Action Execution recorded in engine metrics."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    result = await engine.execute_action(sample_request)

    assert result.status == "SUCCESS"
    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["successful_executions"] == 1
    assert m["failed_executions"] == 0
    assert m["total_latency_ms"] > 0
    assert m["min_latency_ms"] is not None
    assert m["max_latency_ms"] is not None
    assert m["per_driver_executions"] == {"driver-test": 1}
    assert m["per_action_type_executions"] == {"FETCH": 1}


@pytest.mark.asyncio
async def test_engine_metrics_scenario_b_failed_action_result(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario B: FAILED ActionResult from Pipeline recorded as 1 failed execution."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(
            request_id="req-test-1",
            status="FAILED",
            response_payload={"status_code": 500},
            error_details={"error": "Server Error"},
        ),
    )
    registry.register_driver(mock_driver)

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    result = await engine.execute_action(sample_request)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["successful_executions"] == 0
    assert m["failed_executions"] == 1
    assert m["per_driver_executions"] == {"driver-test": 1}


@pytest.mark.asyncio
async def test_engine_metrics_scenario_c_rbac_denial(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
) -> None:
    """Scenario C: RBAC permission denial records failed execution with driver_id=None."""
    req_rbac_denied = ActionRequest(
        request_id="req-rbac-1",
        profile_id="prof-test-1",
        action_type=ConnectorActionType.FETCH,
        options={"granted_permissions": ["kortex.other.permission"]},
    )

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.initialize(MagicMock())
    await engine.start()

    with pytest.raises(ConnectorSecurityError):
        await engine.execute_action(req_rbac_denied)

    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["successful_executions"] == 0
    assert m["failed_executions"] == 1
    assert m["per_driver_executions"] == {}


@pytest.mark.asyncio
async def test_engine_metrics_scenario_d_profile_not_found(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_request: ActionRequest,
) -> None:
    """Scenario D: Profile not found records failed execution with driver_id=None."""
    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.initialize(MagicMock())
    await engine.start()

    with pytest.raises(ConnectorProfileNotFoundError):
        await engine.execute_action(sample_request)

    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["successful_executions"] == 0
    assert m["failed_executions"] == 1
    assert m["per_driver_executions"] == {}


@pytest.mark.asyncio
async def test_engine_metrics_scenario_e_rate_limit_failure(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario E: Rate limit rejection records rate_limit_rejections + 1 failed execution."""
    mock_limiter = MagicMock()
    mock_limiter.acquire_token = AsyncMock(return_value=False)

    engine = ConnectorEngine(registry=registry, rate_limiter=mock_limiter, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    result = await engine.execute_action(sample_request)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["failed_executions"] == 1
    assert m["rate_limit_rejections"] == 1
    assert m["error_categories"]["rate_limit"] == 1


@pytest.mark.asyncio
async def test_engine_metrics_scenario_f_authentication_failure(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
) -> None:
    """Scenario F: Authentication failure records authentication_failures + 1 failed execution."""
    profile_secret = ConnectorProfile(
        profile_id="prof-sec-1",
        name="Sec Profile",
        driver_id="driver-test",
        secret_handle="vault:handle",
        is_active=True,
    )
    req_sec = ActionRequest(
        request_id="req-sec-1",
        profile_id="prof-sec-1",
        action_type=ConnectorActionType.FETCH,
    )

    engine = ConnectorEngine(registry=registry, secret_resolver=None, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(profile_secret)
    await engine.initialize(MagicMock())
    await engine.start()

    result = await engine.execute_action(req_sec)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["failed_executions"] == 1
    assert m["authentication_failures"] == 1
    assert m["error_categories"]["authentication"] == 1


@pytest.mark.asyncio
async def test_engine_metrics_scenario_g_driver_exception(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario G: Driver execution exception records driver_failures + 1 failed execution."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=DriverExecutionError("Network socket closed"),
    )
    registry.register_driver(mock_driver)

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    result = await engine.execute_action(sample_request)

    assert result.status == "FAILED"
    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["failed_executions"] == 1
    assert m["driver_failures"] == 1
    assert m["error_categories"]["driver_execution"] == 1


@pytest.mark.asyncio
async def test_engine_metrics_scenario_h_unexpected_exception(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario H: Unexpected exception in execute_action records 1 failed execution and re-raises."""
    mock_pipeline = MagicMock()
    mock_pipeline.execute = AsyncMock(side_effect=RuntimeError("Unexpected pipeline collapse"))

    engine = ConnectorEngine(registry=registry, pipeline=mock_pipeline, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    with pytest.raises(RuntimeError):
        await engine.execute_action(sample_request)

    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["failed_executions"] == 1


@pytest.mark.asyncio
async def test_engine_metrics_scenario_i_cancellation(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario I: Task cancellation records cancellation_count + 1 failed execution and re-raises CancelledError."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=asyncio.CancelledError(),
    )
    registry.register_driver(mock_driver)

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    with pytest.raises(asyncio.CancelledError):
        await engine.execute_action(sample_request)

    m = diagnostics.metrics()
    assert m["cancellation_count"] == 1
    assert m["total_executions"] == 1
    assert m["failed_executions"] == 1


@pytest.mark.asyncio
async def test_engine_metrics_scenario_j_concurrent_success_executions(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
) -> None:
    """Scenario J: 10 concurrent successful executions correctly accumulate total and success counters."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-concurrent", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    tasks = [
        engine.execute_action(
            ActionRequest(
                request_id=f"req-conc-{i}",
                profile_id="prof-test-1",
                action_type=ConnectorActionType.FETCH,
            )
        )
        for i in range(10)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 10
    assert all(r.status == "SUCCESS" for r in results)
    m = diagnostics.metrics()
    assert m["total_executions"] == 10
    assert m["successful_executions"] == 10
    assert m["failed_executions"] == 0
    assert m["per_driver_executions"] == {"driver-test": 10}


@pytest.mark.asyncio
async def test_engine_metrics_scenario_k_mixed_concurrent_executions(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
) -> None:
    """Scenario K: Mixed concurrent executions (5 success / 5 failed) accumulate cleanly."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=[
            ActionResult(request_id="r0", status="SUCCESS", response_payload={"status_code": 200}),
            ActionResult(request_id="r1", status="FAILED", response_payload={"status_code": 500}),
            ActionResult(request_id="r2", status="SUCCESS", response_payload={"status_code": 200}),
            ActionResult(request_id="r3", status="FAILED", response_payload={"status_code": 404}),
            ActionResult(request_id="r4", status="SUCCESS", response_payload={"status_code": 200}),
            ActionResult(request_id="r5", status="FAILED", response_payload={"status_code": 503}),
            ActionResult(request_id="r6", status="SUCCESS", response_payload={"status_code": 200}),
            ActionResult(request_id="r7", status="FAILED", response_payload={"status_code": 400}),
            ActionResult(request_id="r8", status="SUCCESS", response_payload={"status_code": 200}),
            ActionResult(request_id="r9", status="FAILED", response_payload={"status_code": 500}),
        ],
    )
    registry.register_driver(mock_driver)

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    tasks = [
        engine.execute_action(
            ActionRequest(
                request_id=f"req-mix-{i}",
                profile_id="prof-test-1",
                action_type=ConnectorActionType.FETCH,
            )
        )
        for i in range(10)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 10
    m = diagnostics.metrics()
    assert m["total_executions"] == 10
    assert m["successful_executions"] == 5
    assert m["failed_executions"] == 5


@pytest.mark.asyncio
async def test_engine_metrics_scenario_l_diagnostic_storage_failure_isolation(
    registry: ConnectorDriverRegistry,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario L: Diagnostic storage failure does not alter or replace successful ActionResult."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    mock_broken_diag = MagicMock()
    mock_broken_diag.record_execution.side_effect = RuntimeError("Diagnostics database crashed")

    engine = ConnectorEngine(registry=registry, diagnostics=mock_broken_diag)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    result = await engine.execute_action(sample_request)

    assert result.status == "SUCCESS"
    assert result.response_payload["status_code"] == 200


@pytest.mark.asyncio
async def test_engine_metrics_scenario_m_event_publication_failure_isolation(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario M: Event publication failure does not prevent top-level execution metrics recording."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    mock_kernel = MagicMock()
    mock_kernel.publish_event = AsyncMock(side_effect=RuntimeError("Kernel event engine unavailable"))

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.initialize(kernel=mock_kernel)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.start()

    result = await engine.execute_action(sample_request)

    assert result.status == "SUCCESS"
    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["successful_executions"] == 1


@pytest.mark.asyncio
async def test_engine_metrics_scenario_n_history_persistence_failure_isolation(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario N: Action history persistence failure does not prevent metrics recording."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    mock_store = MagicMock()
    mock_store.execute_in_transaction = AsyncMock(side_effect=RuntimeError("DataStore transaction deadlock"))

    engine = ConnectorEngine(registry=registry, data_store=mock_store, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    result = await engine.execute_action(sample_request)

    assert result.status == "SUCCESS"
    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["successful_executions"] == 1


@pytest.mark.asyncio
async def test_engine_metrics_scenario_o_latency_aggregation(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario O: Latency metrics (min, max, total, average) aggregate accurately."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    for _ in range(3):
        await engine.execute_action(sample_request)

    m = diagnostics.metrics()
    assert m["total_executions"] == 3
    assert m["successful_executions"] == 3
    assert m["total_latency_ms"] > 0
    assert m["min_latency_ms"] is not None and m["min_latency_ms"] > 0
    assert m["max_latency_ms"] is not None and m["max_latency_ms"] >= m["min_latency_ms"]
    assert m["average_latency_ms"] is not None and m["average_latency_ms"] > 0


@pytest.mark.asyncio
async def test_engine_metrics_scenario_p_single_counting_invariant(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario P: Assert total_executions == successful_executions + failed_executions invariant."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=[
            ActionResult(request_id="r1", status="SUCCESS", response_payload={"status_code": 200}),
            ActionResult(request_id="r2", status="FAILED", response_payload={"status_code": 500}),
            DriverExecutionError("Driver crash"),
        ],
    )
    registry.register_driver(mock_driver)

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    # 1. Success
    await engine.execute_action(sample_request)
    # 2. Failed ActionResult
    await engine.execute_action(sample_request)
    # 3. Exception
    await engine.execute_action(sample_request)

    m = diagnostics.metrics()
    assert m["total_executions"] == 3
    assert m["successful_executions"] == 1
    assert m["failed_executions"] == 2
    assert m["total_executions"] == m["successful_executions"] + m["failed_executions"]


@pytest.mark.asyncio
async def test_engine_metrics_scenario_q_engine_state_failure(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_request: ActionRequest,
) -> None:
    """Scenario Q: Calling execute_action in STOPPED state records 1 failed execution with driver_id=None."""
    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    # Engine is UNINITIALIZED / STOPPED

    with pytest.raises(EngineStateError):
        await engine.execute_action(sample_request)

    m = diagnostics.metrics()
    assert m["total_executions"] == 1
    assert m["successful_executions"] == 0
    assert m["failed_executions"] == 1
    assert m["per_driver_executions"] == {}


@pytest.mark.asyncio
async def test_engine_metrics_scenario_r_diagnostic_execution_recording_failure_warning(
    registry: ConnectorDriverRegistry,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scenario R: Diagnostic execution recording failure logs generic warning without raw exception text."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(request_id="req-test-1", status="SUCCESS", response_payload={"status_code": 200}),
    )
    registry.register_driver(mock_driver)

    mock_broken_diag = MagicMock()
    mock_broken_diag.record_execution.side_effect = RuntimeError("SECRET_SENSITIVE_KEY_123_FAILED")

    engine = ConnectorEngine(registry=registry, diagnostics=mock_broken_diag)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    with caplog.at_level("WARNING"):
        result = await engine.execute_action(sample_request)

    assert result.status == "SUCCESS"
    assert "Failed to record connector execution metrics." in caplog.text
    # Assert sensitive exception details are NEVER logged!
    assert "SECRET_SENSITIVE_KEY_123_FAILED" not in caplog.text


@pytest.mark.asyncio
async def test_engine_metrics_scenario_s_diagnostic_cancellation_recording_failure_warning(
    registry: ConnectorDriverRegistry,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scenario S: Diagnostic cancellation recording failure logs generic warning without raw exception text."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=asyncio.CancelledError(),
    )
    registry.register_driver(mock_driver)

    mock_broken_diag = MagicMock()
    mock_broken_diag.record_cancellation.side_effect = RuntimeError("SECRET_CANCEL_FAIL_999")

    engine = ConnectorEngine(registry=registry, diagnostics=mock_broken_diag)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    with caplog.at_level("WARNING"):
        with pytest.raises(asyncio.CancelledError):
            await engine.execute_action(sample_request)

    assert "Failed to record connector cancellation metric." in caplog.text
    assert "SECRET_CANCEL_FAIL_999" not in caplog.text


@pytest.mark.asyncio
async def test_engine_metrics_scenario_t_base_exception_propagation(
    registry: ConnectorDriverRegistry,
    diagnostics: ConnectorDiagnostics,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Scenario T: Custom BaseException (non-CancelledError) bubbles up without being caught by except Exception."""
    class CustomProcessExit(BaseException):
        pass

    mock_driver = MockableDriver(
        driver_id="driver-test",
        side_effect=CustomProcessExit("Process exit signal"),
    )
    registry.register_driver(mock_driver)

    engine = ConnectorEngine(registry=registry, diagnostics=diagnostics)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    with pytest.raises(CustomProcessExit):
        await engine.execute_action(sample_request)


@pytest.mark.asyncio
async def test_initialize_kernel_storage_resolution_exception(registry: ConnectorDriverRegistry) -> None:
    """Test initialize() when Kernel container.resolve raises an exception."""
    engine = ConnectorEngine(registry=registry)
    mock_kernel = MagicMock()
    mock_kernel.container.resolve.side_effect = RuntimeError("StorageEngine not in IoC container")
    await engine.initialize(mock_kernel)
    assert engine.state == EngineState.READY


@pytest.mark.asyncio
async def test_safe_record_helpers_when_diagnostics_is_none(
    registry: ConnectorDriverRegistry,
    sample_request: ActionRequest,
) -> None:
    """Test _safe_record_execution, _safe_record_cancellation, and _record_action_history fallback guards."""
    engine = ConnectorEngine(registry=registry, diagnostics=None)
    engine._diagnostics = None
    engine._safe_record_execution(is_success=True, latency_ms=10.0)
    engine._safe_record_cancellation()

    dummy_result = ActionResult(request_id="req-test-1", status="SUCCESS")
    await engine._record_action_history(sample_request, dummy_result)



@pytest.mark.asyncio
async def test_record_action_history_data_store_execution(
    registry: ConnectorDriverRegistry,
    sample_profile: ConnectorProfile,
    sample_request: ActionRequest,
) -> None:
    """Test action history persistence closure execution via data_store transaction."""
    mock_driver = MockableDriver(
        driver_id="driver-test",
        return_value=ActionResult(
            request_id="req-test-1",
            status="FAILED",
            response_payload={"status_code": 500},
            error_details={"error": "Database error"},
        ),
    )
    registry.register_driver(mock_driver)

    mock_data_store = MagicMock()

    async def mock_exec_tx(fn: Any) -> None:
        mock_session = MagicMock()
        await fn(mock_session)
        assert mock_session.add.called

    mock_data_store.execute_in_transaction = AsyncMock(side_effect=mock_exec_tx)

    engine = ConnectorEngine(registry=registry, data_store=mock_data_store)
    await engine.profile_manager.register_profile(sample_profile)
    await engine.initialize(MagicMock())
    await engine.start()

    result = await engine.execute_action(sample_request)
    assert result.status == "FAILED"
    assert mock_data_store.execute_in_transaction.called
