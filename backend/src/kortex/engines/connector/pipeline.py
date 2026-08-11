"""Connector Pipeline Stage Coordinator for KORTEX OS Connector Engine.

This module implements ConnectorPipeline, coordinating multi-stage connector action execution
(Validation -> Authentication -> Rate Limiting -> Dispatch -> Verification -> Audit)
in accordance with the Connector Engine Specification.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Coroutine

from kortex.engines.connector.exceptions import (
    ConnectorOperationError,
    DriverExecutionError,
    DriverNotFoundError,
)
from kortex.engines.connector.interfaces import (
    IConnectorDriverRegistry,
    IConnectorPipeline,
    IRateLimiter,
)
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorProfile,
)
from kortex.engines.connector.rate_limiter import execute_with_retry


class ConnectorPipeline(IConnectorPipeline):
    """Multi-stage coordinator for executing action requests through Connector Profiles and Drivers."""

    def __init__(
        self,
        registry: IConnectorDriverRegistry,
        rate_limiter: IRateLimiter | None = None,
        secret_resolver: Callable[[str], Coroutine[Any, Any, str | None]] | None = None,
    ) -> None:
        """Initialize ConnectorPipeline.

        Args:
            registry: IConnectorDriverRegistry instance for driver lookup.
            rate_limiter: Optional IRateLimiter instance for rate limiting.
            secret_resolver: Optional async callable for resolving secret handle to token string.
        """
        self._registry = registry
        self._rate_limiter = rate_limiter
        self._secret_resolver = secret_resolver

    async def execute(
        self, request: ActionRequest, profile: ConnectorProfile
    ) -> ActionResult:
        """Execute a multi-stage pipeline for an action request through target ConnectorProfile.

        Stages:
        1. Validation & Active Status Check (Profile ID Match & Profile Active Check)
        2. Authentication Stage (Secret Handle Resolution)
        3. Rate Limiting Stage (Token Bucket Acquisition)
        4. Dispatch Stage (Driver Execution with Exponential Backoff Retry)
        5. Verification & Audit Stage (Metrics & Result Formatting)

        Args:
            request: ActionRequest model instance.
            profile: ConnectorProfile model instance.

        Returns:
            ActionResult payload summarizing execution outcome.
        """
        start_time = time.perf_counter()

        # Stage 1: Validation & Active Status Check
        if request.profile_id != profile.profile_id:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return ActionResult(
                request_id=request.request_id,
                status="FAILED",
                response_payload={},
                execution_time_ms=round(elapsed_ms, 3),
                error_details={
                    "error": (
                        f"Mismatched profile_id: request profile_id '{request.profile_id}' "
                        f"does not match profile_id '{profile.profile_id}'."
                    ),
                    "request_profile_id": request.profile_id,
                    "profile_id": profile.profile_id,
                },
                correlation_id=request.correlation_id,
            )

        if not profile.is_active:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return ActionResult(
                request_id=request.request_id,
                status="FAILED",
                response_payload={},
                execution_time_ms=round(elapsed_ms, 3),
                error_details={
                    "error": f"Connector profile '{profile.profile_id}' is inactive.",
                    "profile_id": profile.profile_id,
                },
                correlation_id=request.correlation_id,
            )

        # Stage 2: Authentication Stage
        secret_token: str | None = None
        if profile.secret_handle and profile.secret_handle.strip():
            handle = profile.secret_handle.strip()
            if self._secret_resolver is None:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return ActionResult(
                    request_id=request.request_id,
                    status="FAILED",
                    response_payload={},
                    execution_time_ms=round(elapsed_ms, 3),
                    error_details={
                        "error": "Secret resolver unavailable.",
                        "profile_id": profile.profile_id,
                    },
                    correlation_id=request.correlation_id,
                )

            try:
                secret_token = await self._secret_resolver(handle)
            except Exception:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return ActionResult(
                    request_id=request.request_id,
                    status="FAILED",
                    response_payload={},
                    execution_time_ms=round(elapsed_ms, 3),
                    error_details={
                        "error": "Failed to resolve connector credentials.",
                        "profile_id": profile.profile_id,
                    },
                    correlation_id=request.correlation_id,
                )

        # Stage 3: Rate Limiting Stage
        if self._rate_limiter is not None:
            rate_key = f"profile:{profile.profile_id}"
            acquired = await self._rate_limiter.acquire_token(rate_key)
            if not acquired:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return ActionResult(
                    request_id=request.request_id,
                    status="FAILED",
                    response_payload={},
                    execution_time_ms=round(elapsed_ms, 3),
                    error_details={
                        "error": f"Rate limit exceeded for profile '{profile.profile_id}'.",
                        "profile_id": profile.profile_id,
                    },
                    correlation_id=request.correlation_id,
                )

        # Stage 4: Dispatch Stage with Exponential Backoff Retry
        try:
            driver = self._registry.get_driver(profile.driver_id)
        except DriverNotFoundError as err:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return ActionResult(
                request_id=request.request_id,
                status="FAILED",
                response_payload={},
                execution_time_ms=round(elapsed_ms, 3),
                error_details={
                    "error": str(err),
                    "driver_id": profile.driver_id,
                    "profile_id": profile.profile_id,
                },
                correlation_id=request.correlation_id,
            )

        try:
            driver_result = await execute_with_retry(
                driver.execute_action,
                request,
                secret_token=secret_token,
                max_retries=profile.max_retries,
                base_delay=0.01,
                jitter=False,
                retryable_exceptions=(DriverExecutionError, ConnectorOperationError),
            )
        except Exception:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return ActionResult(
                request_id=request.request_id,
                status="FAILED",
                response_payload={},
                execution_time_ms=round(elapsed_ms, 3),
                error_details={
                    "error": f"Driver execution failed for driver '{profile.driver_id}'.",
                    "driver_id": profile.driver_id,
                    "profile_id": profile.profile_id,
                },
                correlation_id=request.correlation_id,
            )

        # Stage 5 & 6: Verification & Audit Stage
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return driver_result.model_copy(
            update={
                "execution_time_ms": round(elapsed_ms, 3),
                "correlation_id": request.correlation_id or driver_result.correlation_id,
            }
        )


__all__ = ["ConnectorPipeline"]
