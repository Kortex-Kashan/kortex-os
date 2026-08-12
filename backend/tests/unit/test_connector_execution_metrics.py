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
