"""Unit tests for ConnectorPipeline stage coordinator (Milestone 6).

Target: 100% pass rate, 100% line coverage for pipeline.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.exceptions import (
    ConnectorOperationError,
    DriverExecutionError,
    DriverNotFoundError,
)
from kortex.engines.connector.interfaces import IConnectorPipeline
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorProfile,
)
from kortex.engines.connector.pipeline import ConnectorPipeline
from kortex.engines.connector.rate_limiter import TokenBucketRateLimiter
from kortex.engines.connector.registry import ConnectorDriverRegistry


def test_protocol_compliance() -> None:
    """Test that ConnectorPipeline satisfies IConnectorPipeline protocol."""
    reg = ConnectorDriverRegistry()
    pipeline = ConnectorPipeline(registry=reg)
    assert isinstance(pipeline, IConnectorPipeline)


@pytest.mark.asyncio
async def test_pipeline_execution_success() -> None:
    """Test end-to-end successful pipeline execution across all standard actions."""
    reg = ConnectorDriverRegistry()
    driver = DummyConnectorDriver()
    reg.register_driver(driver)

    limiter = TokenBucketRateLimiter(default_capacity=10.0, default_refill_rate=10.0)

    async def mock_secret_resolver(handle: str, tenant_id: str) -> str:
        return f"resolved-secret-for-{handle}"

    pipeline = ConnectorPipeline(registry=reg, rate_limiter=limiter, secret_resolver=mock_secret_resolver)

    profile = ConnectorProfile(
        profile_id="prof-pipeline-1",
        name="Pipeline Profile 1",
        driver_id="connector-dummy",
        secret_handle="sec-handle-xyz",
    )

    for action in [
        ConnectorActionType.SEND,
        ConnectorActionType.RECEIVE,
        ConnectorActionType.FETCH,
        ConnectorActionType.PUSH,
        ConnectorActionType.VERIFY,
    ]:
        req = ActionRequest(
            request_id=f"req-pipe-{action.value}",
            profile_id="prof-pipeline-1",
            action_type=action,
            payload={"test_key": "test_val"},
            correlation_id="corr-pipe-999",
        )

        res = await pipeline.execute(req, profile)

        assert isinstance(res, ActionResult)
        assert res.request_id == f"req-pipe-{action.value}"
        assert res.status == "SUCCESS"
        assert res.correlation_id == "corr-pipe-999"
        assert res.execution_time_ms >= 0.0
        assert res.response_payload["secret_authenticated"] is True
        assert res.response_payload["echo_payload"] == {"test_key": "test_val"}


@pytest.mark.asyncio
async def test_pipeline_complete_credential_flow() -> None:
    """Verify complete credential flow: secret_handle -> secret_resolver(handle) -> resolved secret token -> driver."""
    reg = ConnectorDriverRegistry()
    received_secret_token: str | None = None
    resolved_handles: list[str] = []

    class CredentialTestDriver(DummyConnectorDriver):
        async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
            nonlocal received_secret_token
            received_secret_token = secret_token
            return await super().execute_action(request, secret_token=secret_token)

    reg.register_driver(CredentialTestDriver())

    secret_handle = "sec-vault-handle-1001"
    expected_resolved_token = "token_super_secret_xyz_777"

    async def mock_secret_resolver(handle: str, tenant_id: str) -> str:
        resolved_handles.append(handle)
        if handle == secret_handle:
            return expected_resolved_token
        return "unknown"

    pipeline = ConnectorPipeline(registry=reg, secret_resolver=mock_secret_resolver)

    profile = ConnectorProfile(
        profile_id="prof-cred-flow",
        name="Cred Flow Profile",
        driver_id="connector-dummy",
        secret_handle=secret_handle,
    )

    req = ActionRequest(
        request_id="req-cred-flow",
        profile_id="prof-cred-flow",
        action_type=ConnectorActionType.SEND,
    )

    res = await pipeline.execute(req, profile)

    assert res.status == "SUCCESS"
    assert resolved_handles == [secret_handle]
    assert received_secret_token == expected_resolved_token
    assert received_secret_token != secret_handle
    assert res.error_details is None


@pytest.mark.asyncio
async def test_pipeline_profile_id_mismatch_rejection() -> None:
    """Test pipeline rejection when request.profile_id does not match profile.profile_id."""
    reg = ConnectorDriverRegistry()
    pipeline = ConnectorPipeline(registry=reg)

    profile = ConnectorProfile(
        profile_id="prof-expected",
        name="Expected Profile",
        driver_id="connector-dummy",
    )
    req = ActionRequest(
        request_id="req-mismatch",
        profile_id="prof-different",
        action_type=ConnectorActionType.SEND,
        correlation_id="corr-mismatch",
    )

    res = await pipeline.execute(req, profile)
    assert res.status == "FAILED"
    assert res.correlation_id == "corr-mismatch"
    assert "Mismatched profile_id" in res.error_details["error"]


@pytest.mark.asyncio
async def test_pipeline_inactive_profile_rejection() -> None:
    """Test pipeline rejection when profile.is_active is False."""
    reg = ConnectorDriverRegistry()
    pipeline = ConnectorPipeline(registry=reg)

    profile = ConnectorProfile(
        profile_id="prof-inactive",
        name="Inactive Profile",
        driver_id="connector-dummy",
        is_active=False,
    )
    req = ActionRequest(
        request_id="req-inact",
        profile_id="prof-inactive",
        action_type=ConnectorActionType.SEND,
        correlation_id="corr-inact",
    )

    res = await pipeline.execute(req, profile)
    assert res.status == "FAILED"
    assert res.correlation_id == "corr-inact"
    assert "inactive" in res.error_details["error"]


@pytest.mark.asyncio
async def test_pipeline_secret_resolution_without_resolver() -> None:
    """Test pipeline returns generic authentication failure without leaking secret_handle when resolver is None."""
    reg = ConnectorDriverRegistry()
    reg.register_driver(DummyConnectorDriver())
    pipeline = ConnectorPipeline(registry=reg, secret_resolver=None)

    secret_handle = "sensitive-secret-handle-999"
    profile_with_handle = ConnectorProfile(
        profile_id="prof-sec-no-resolver",
        name="Profile No Resolver",
        driver_id="connector-dummy",
        secret_handle=secret_handle,
    )
    req1 = ActionRequest(
        request_id="req-sec-1",
        profile_id="prof-sec-no-resolver",
        action_type=ConnectorActionType.SEND,
    )

    res1 = await pipeline.execute(req1, profile_with_handle)
    assert res1.status == "FAILED"
    assert res1.error_details["error"] == "Secret resolver unavailable."
    assert secret_handle not in str(res1.error_details)
    assert secret_handle not in str(res1)

    # Secret handle None without secret_resolver succeeds
    profile_no_handle = ConnectorProfile(
        profile_id="prof-no-handle",
        name="Profile No Handle",
        driver_id="connector-dummy",
        secret_handle=None,
    )
    req2 = ActionRequest(
        request_id="req-sec-2",
        profile_id="prof-no-handle",
        action_type=ConnectorActionType.SEND,
    )

    res2 = await pipeline.execute(req2, profile_no_handle)
    assert res2.status == "SUCCESS"
    assert res2.response_payload["secret_authenticated"] is False


@pytest.mark.asyncio
async def test_pipeline_secret_resolver_exception() -> None:
    """Test pipeline failure handling when secret_resolver raises an exception without leaking handle or exception."""
    reg = ConnectorDriverRegistry()

    raw_exception_text = "Internal vault secret_key_admin_super_secret_99"

    async def broken_secret_resolver(handle: str, tenant_id: str) -> str:
        raise RuntimeError(raw_exception_text)

    pipeline = ConnectorPipeline(registry=reg, secret_resolver=broken_secret_resolver)

    secret_handle = "sensitive-vault-handle-77"
    profile = ConnectorProfile(
        profile_id="prof-broken-sec",
        name="Broken Secret Profile",
        driver_id="connector-dummy",
        secret_handle=secret_handle,
    )
    req = ActionRequest(
        request_id="req-sec-err",
        profile_id="prof-broken-sec",
        action_type=ConnectorActionType.SEND,
    )

    res = await pipeline.execute(req, profile)
    assert res.status == "FAILED"
    assert res.error_details["error"] == "Failed to resolve connector credentials."
    assert secret_handle not in str(res.error_details)
    assert secret_handle not in str(res)
    assert raw_exception_text not in str(res.error_details)
    assert raw_exception_text not in str(res)


@pytest.mark.asyncio
async def test_pipeline_rate_limiting_throttling() -> None:
    """Test rate limiting stage failure when token acquisition returns False."""
    reg = ConnectorDriverRegistry()
    reg.register_driver(DummyConnectorDriver())

    # Rate limiter with 1 token capacity
    limiter = TokenBucketRateLimiter(default_capacity=1.0, default_refill_rate=0.0)
    pipeline = ConnectorPipeline(registry=reg, rate_limiter=limiter)

    profile = ConnectorProfile(
        profile_id="prof-throttled",
        name="Throttled Profile",
        driver_id="connector-dummy",
    )

    req1 = ActionRequest(request_id="r1", profile_id="prof-throttled", action_type=ConnectorActionType.SEND)
    req2 = ActionRequest(request_id="r2", profile_id="prof-throttled", action_type=ConnectorActionType.SEND)

    # First request acquires token
    res1 = await pipeline.execute(req1, profile)
    assert res1.status == "SUCCESS"

    # Second request is throttled
    res2 = await pipeline.execute(req2, profile)
    assert res2.status == "FAILED"
    assert "Rate limit exceeded" in res2.error_details["error"]


@pytest.mark.asyncio
async def test_pipeline_driver_not_found() -> None:
    """Test dispatch stage failure when driver is not registered."""
    reg = ConnectorDriverRegistry()
    pipeline = ConnectorPipeline(registry=reg)

    profile = ConnectorProfile(
        profile_id="prof-missing-drv",
        name="Missing Driver Profile",
        driver_id="non-existent-driver",
    )
    req = ActionRequest(request_id="r-missing", profile_id="prof-missing-drv", action_type=ConnectorActionType.SEND)

    res = await pipeline.execute(req, profile)
    assert res.status == "FAILED"
    assert "not found in registry" in res.error_details["error"]


@pytest.mark.asyncio
async def test_pipeline_driver_execution_retry_and_exhaustion() -> None:
    """Test exponential backoff retries on transient driver errors and exhaustion with generic error masking."""
    reg = ConnectorDriverRegistry()

    attempts = 0

    class RetryableFailDriver(DummyConnectorDriver):
        async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
            nonlocal attempts
            attempts += 1
            raise DriverExecutionError("Transient network failure")

    reg.register_driver(RetryableFailDriver())
    pipeline = ConnectorPipeline(registry=reg)

    profile = ConnectorProfile(
        profile_id="prof-retry-fail",
        name="Retry Fail Profile",
        driver_id="connector-dummy",
        max_retries=2,
    )
    req = ActionRequest(
        request_id="req-sim-fail",
        profile_id="prof-retry-fail",
        action_type=ConnectorActionType.SEND,
    )

    res = await pipeline.execute(req, profile)
    assert res.status == "FAILED"
    assert res.error_details["error"] == "Driver execution failed for driver 'connector-dummy'."
    assert attempts == 3  # Initial + 2 retries


@pytest.mark.asyncio
async def test_pipeline_driver_sensitive_exception_masking() -> None:
    """Test driver exception privacy masking when driver raises an exception containing fake sensitive data."""
    reg = ConnectorDriverRegistry()

    sensitive_text = "internal secret token: SUPER_SECRET_123"

    class SensitiveExceptionDriver(DummyConnectorDriver):
        async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
            raise RuntimeError(f"Connection failed with {sensitive_text}")

    reg.register_driver(SensitiveExceptionDriver())
    pipeline = ConnectorPipeline(registry=reg)

    profile = ConnectorProfile(profile_id="prof-sens", name="Sens Profile", driver_id="connector-dummy")
    req = ActionRequest(request_id="r-sens", profile_id="prof-sens", action_type=ConnectorActionType.SEND)

    res = await pipeline.execute(req, profile)
    assert res.status == "FAILED"
    assert res.error_details["error"] == "Driver execution failed for driver 'connector-dummy'."
    # Verify sensitive data is NOT exposed
    assert "SUPER_SECRET_123" not in str(res.error_details)
    assert "SUPER_SECRET_123" not in str(res)
    assert sensitive_text not in str(res.error_details)
    assert sensitive_text not in str(res)


@pytest.mark.asyncio
async def test_concurrent_pipeline_executions() -> None:
    """Test concurrent pipeline dispatches using asyncio.gather."""
    reg = ConnectorDriverRegistry()
    reg.register_driver(DummyConnectorDriver())
    pipeline = ConnectorPipeline(registry=reg)

    profile = ConnectorProfile(profile_id="prof-conc-pipe", name="Conc Pipe", driver_id="connector-dummy")

    async def worker(idx: int) -> ActionResult:
        req = ActionRequest(
            request_id=f"req-conc-{idx}",
            profile_id="prof-conc-pipe",
            action_type=ConnectorActionType.SEND,
        )
        return await pipeline.execute(req, profile)

    tasks = [worker(i) for i in range(15)]
    results = await asyncio.gather(*tasks)

    assert len(results) == 15
    assert all(r.status == "SUCCESS" for r in results)
