"""Connector Pipeline Stage Coordinator for KORTEX OS Connector Engine.

This module implements ConnectorPipeline, coordinating multi-stage connector action execution
(Validation -> Authentication -> Rate Limiting -> Dispatch -> Verification -> Audit)
in accordance with the Connector Engine Specification.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Callable, Coroutine

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

if TYPE_CHECKING:
    from kortex.engines.connector.diagnostics import ConnectorDiagnostics


class ConnectorPipeline(IConnectorPipeline):
    """Multi-stage coordinator for executing action requests through Connector Profiles and Drivers."""

    def __init__(
        self,
        registry: IConnectorDriverRegistry,
        rate_limiter: IRateLimiter | None = None,
        secret_resolver: Callable[[str, str], Coroutine[Any, Any, str | None]] | None = None,
        diagnostics: ConnectorDiagnostics | None = None,
    ) -> None:
        """Initialize ConnectorPipeline.

        Args:
            registry: IConnectorDriverRegistry instance for driver lookup.
            rate_limiter: Optional IRateLimiter instance for rate limiting.
            secret_resolver: Optional async callable for resolving a secret handle
                to a token string, taking ``(secret_handle, tenant_id)``. Tenant
                is threaded through so a resolved credential can never cross a
                tenant boundary (M6.0-2).
            diagnostics: Optional ConnectorDiagnostics instance for stage & attempt metric recording.
        """
        self._registry = registry
        self._rate_limiter = rate_limiter
        self._secret_resolver = secret_resolver
        self._diagnostics = diagnostics

    def _safe_record(self, record_fn_name: str, *args: Any, **kwargs: Any) -> None:
        """Helper to invoke a diagnostic recording method safely without altering execution flow."""
        if self._diagnostics is None:
            return
        try:
            fn = getattr(self._diagnostics, record_fn_name, None)
            if callable(fn):
                fn(*args, **kwargs)
        except Exception:
            pass

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
            self._safe_record("record_error_category", "unknown_error")
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
            self._safe_record("record_error_category", "unknown_error")
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
                self._safe_record("record_authentication_failure")
                self._safe_record("record_error_category", "authentication")
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
                secret_token = await self._secret_resolver(handle, request.tenant_id)
            except Exception:
                self._safe_record("record_authentication_failure")
                self._safe_record("record_error_category", "authentication")
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
                self._safe_record("record_rate_limit_rejection")
                self._safe_record("record_error_category", "rate_limit")
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

        # Stage 4: Dispatch Stage with Exponential Backoff Retry & Attempt Instrumentation
        try:
            driver = self._registry.get_driver(profile.driver_id)
        except DriverNotFoundError as err:
            self._safe_record("record_driver_failure")
            self._safe_record("record_error_category", "driver_not_found")
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

        attempts = 0

        async def _tracked_driver_execute(
            req: ActionRequest, secret_token: str | None = None
        ) -> ActionResult:
            nonlocal attempts
            attempts += 1
            return await driver.execute_action(req, secret_token=secret_token)

        try:
            driver_result = await execute_with_retry(
                _tracked_driver_execute,
                request,
                secret_token=secret_token,
                max_retries=profile.max_retries,
                base_delay=0.01,
                jitter=False,
                retryable_exceptions=(DriverExecutionError, ConnectorOperationError),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._safe_record("record_driver_failure")
            self._safe_record("record_error_category", "driver_execution")
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
        finally:
            retry_count = max(0, attempts - 1)
            if retry_count > 0:
                self._safe_record("record_retry", retry_count)

        # Stage 5 & 6: Verification, HTTP Status Recording & Audit Stage
        if isinstance(driver_result.response_payload, dict):
            status_code = driver_result.response_payload.get("status_code")
            if status_code is not None and not isinstance(status_code, bool):
                self._safe_record("record_http_status", status_code)
                code_int: int | None = None
                if isinstance(status_code, int):
                    code_int = status_code
                elif isinstance(status_code, str) and status_code.strip().isdigit():
                    code_int = int(status_code.strip())

                if code_int is not None:
                    if 400 <= code_int <= 499:
                        self._safe_record("record_error_category", "http_4xx")
                    elif 500 <= code_int <= 599:
                        self._safe_record("record_error_category", "http_5xx")

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return driver_result.model_copy(
            update={
                "execution_time_ms": round(elapsed_ms, 3),
                "correlation_id": request.correlation_id or driver_result.correlation_id,
            }
        )


__all__ = ["ConnectorPipeline"]
