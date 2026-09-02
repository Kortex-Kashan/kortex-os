"""Reference Dummy Connector Driver Plugin for KORTEX OS Connector Engine.

This module implements DummyConnectorDriver, a reference driver plugin inheriting
BaseConnectorDriver for pipeline verification, testing, and mock action dispatches
without external network dependencies, in accordance with the Connector Engine Specification.
"""

from __future__ import annotations

import time
from typing import Any

from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.exceptions import DriverExecutionError
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorCapability,
    ConnectorProfile,
    DriverMetadata,
)


class DummyConnectorDriver(BaseConnectorDriver):
    """Reference dummy connector driver plugin for testing and mock action dispatches.

    Operating 100% offline with zero network or external dependencies, this driver
    provides deterministic mock execution responses and connectivity tests for
    all standard ConnectorActionType actions.
    """

    @property
    def metadata(self) -> DriverMetadata:
        """Return immutable driver metadata object."""
        return DriverMetadata(
            driver_id="connector-dummy",
            display_name="Reference Dummy Connector Driver",
            vendor="KORTEX",
            author="KORTEX Core Team",
            version="1.0.0",
            description=("Reference dummy connector driver plugin for testing and mock action dispatches."),
            license="MIT",
            is_sandboxed=True,
            supported_actions=[
                ConnectorActionType.SEND,
                ConnectorActionType.RECEIVE,
                ConnectorActionType.FETCH,
                ConnectorActionType.PUSH,
                ConnectorActionType.VERIFY,
            ],
            supported_capabilities=[
                ConnectorCapability.SEND,
                ConnectorCapability.RECEIVE,
                ConnectorCapability.FETCH,
                ConnectorCapability.PUSH,
                ConnectorCapability.VERIFY,
                ConnectorCapability.TEST_CONNECTION,
                ConnectorCapability.AUTHENTICATE,
                ConnectorCapability.WEBHOOK,
                ConnectorCapability.STREAMING,
            ],
        )

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        """Execute a mock connector action and return deterministic result payload.

        Args:
            request: Immutable ActionRequest object.
            secret_token: Optional resolved authentication secret token string.

        Returns:
            ActionResult payload detailing execution status and response data.

        Raises:
            DriverExecutionError: If request.action_type is not supported by driver.
        """
        start_time = time.perf_counter()

        if not self.supports_action(request.action_type):
            raise DriverExecutionError(
                f"Action '{request.action_type.value}' is not supported by driver '{self.driver_id}'.",
                details={
                    "action_type": request.action_type.value,
                    "driver_id": self.driver_id,
                    "request_id": request.request_id,
                },
            )

        # Check for simulated error options in payload or options
        options = request.options or {}
        payload = request.payload or {}

        should_fail = options.get("should_fail", False) or payload.get("should_fail", False)
        simulated_error = options.get("simulated_error") or payload.get("simulated_error")

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if should_fail or simulated_error:
            error_msg = str(simulated_error) if simulated_error else "Simulated driver execution failure"
            return ActionResult(
                request_id=request.request_id,
                status="FAILED",
                response_payload={},
                execution_time_ms=round(elapsed_ms, 3),
                error_details={"error": error_msg, "driver_id": self.driver_id},
                correlation_id=request.correlation_id,
            )

        response_data: dict[str, Any] = {
            "action": request.action_type.value,
            "status": "executed",
            "echo_payload": payload,
            "correlation_id": request.correlation_id,
            "mock_driver_id": self.driver_id,
            "secret_authenticated": secret_token is not None,
        }

        return ActionResult(
            request_id=request.request_id,
            status="SUCCESS",
            response_payload=response_data,
            execution_time_ms=round(elapsed_ms, 3),
            error_details=None,
            correlation_id=request.correlation_id,
        )

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        """Test connectivity and configuration validity for a target profile.

        Args:
            profile: ConnectorProfile instance.
            secret_token: Optional resolved authentication secret token string.

        Returns:
            True if connection test succeeds, False otherwise.
        """
        if not profile.is_active:
            return False

        options = profile.options or {}
        return not options.get("simulate_connection_failure", False)


__all__ = ["DummyConnectorDriver"]
