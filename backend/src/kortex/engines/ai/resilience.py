"""Production Provider Resilience Layer for KORTEX AI Orchestration Engine.

Governed by Milestone 9.2 architecture specification:
docs/architecture/ai_engine_m9_production_runtime_spec.md

Implements:
- Timeout protection preventing hung provider execution and GPU deadlocks
- Exponential backoff retry policies with jitter for transient provider failures
- Circuit breaker state machines (CLOSED, OPEN, HALF_OPEN)
- Multi-provider fallback chaining
"""

from __future__ import annotations

import asyncio
import enum
import logging
import random
import time
from typing import Final

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.exceptions import (
    AIProviderError,
    AIProviderTimeoutError,
    CircuitBreakerOpenError,
    PermanentProviderError,
    ProviderFallbackExhaustedError,
    TransientProviderError,
)
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse

logger = logging.getLogger("kortex.engines.ai.resilience")

DEFAULT_TIMEOUT_SECONDS: Final[float] = 60.0
DEFAULT_EMBEDDING_TIMEOUT_SECONDS: Final[float] = 10.0
DEFAULT_MAX_ATTEMPTS: Final[int] = 3
DEFAULT_INITIAL_DELAY: Final[float] = 1.0
DEFAULT_BACKOFF_FACTOR: Final[float] = 2.0
DEFAULT_MAX_DELAY: Final[float] = 60.0
DEFAULT_FAILURE_THRESHOLD: Final[int] = 3
DEFAULT_RECOVERY_TIMEOUT: Final[float] = 30.0

TRANSIENT_STATUS_CODES: Final[set[int]] = {429, 500, 502, 503, 504}
PERMANENT_STATUS_CODES: Final[set[int]] = {400, 401, 403, 404, 405, 422}


class CircuitState(enum.StrEnum):
    """Operational states for the provider circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """State machine tracking provider failures and failing fast when degraded."""

    def __init__(
        self,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be > 0")

        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_state_change: float = time.monotonic()
        self._last_failure_reason: str | None = None

    @property
    def state(self) -> CircuitState:
        """Current circuit state, automatically advancing from OPEN to HALF_OPEN if timeout elapsed."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._last_state_change = time.monotonic()
                logger.info("Circuit breaker transitioned from OPEN to HALF_OPEN (recovery probe).")
        return self._state

    @property
    def consecutive_failures(self) -> int:
        """Count of contiguous failures since last reset."""
        return self._consecutive_failures

    @property
    def failure_threshold(self) -> int:
        """Configured threshold of failures required to trip the circuit."""
        return self._failure_threshold

    @property
    def recovery_timeout(self) -> float:
        """Configured recovery cooldown in seconds."""
        return self._recovery_timeout

    @property
    def last_failure_reason(self) -> str | None:
        """Diagnostic description of the most recent failure."""
        return self._last_failure_reason

    def can_execute(self) -> bool:
        """Determine if a request may proceed, or raise CircuitBreakerOpenError."""
        current_state = self.state
        if current_state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
            return True

        remaining = max(0.0, self._recovery_timeout - (time.monotonic() - self._last_state_change))
        raise CircuitBreakerOpenError(
            f"Provider circuit breaker is OPEN ({self._consecutive_failures} failures). "
            f"Recovery cooldown active for {remaining:.1f}s more."
        )

    def record_success(self) -> None:
        """Record a successful execution, closing the circuit if currently HALF_OPEN."""
        if self._state != CircuitState.CLOSED:
            logger.info("Circuit breaker closed after successful execution probe.")
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_reason = None
        self._last_state_change = time.monotonic()

    def record_failure(self, exc: Exception | None = None) -> None:
        """Record a failed execution, tripping to OPEN if threshold reached or in probe mode."""
        self._consecutive_failures += 1
        self._last_failure_reason = str(exc) if exc else "Unknown error"

        if self._state == CircuitState.HALF_OPEN or self._consecutive_failures >= self._failure_threshold:
            self._state = CircuitState.OPEN
            self._last_state_change = time.monotonic()
            logger.warning(
                "Circuit breaker tripped to OPEN (%d consecutive failures): %s",
                self._consecutive_failures,
                self._last_failure_reason,
            )

    def reset(self) -> None:
        """Explicitly reset the circuit breaker to clean CLOSED state."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_reason = None
        self._last_state_change = time.monotonic()


class RetryPolicy:
    """Configurable retry policy with exponential backoff, jitter, and error classification."""

    def __init__(
        self,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        initial_delay: float = DEFAULT_INITIAL_DELAY,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        max_delay: float = DEFAULT_MAX_DELAY,
        jitter: bool = True,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if initial_delay < 0:
            raise ValueError("initial_delay must be >= 0")
        if backoff_factor < 1.0:
            raise ValueError("backoff_factor must be >= 1.0")
        if max_delay < initial_delay:
            raise ValueError("max_delay must be >= initial_delay")

        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay
        self.jitter = jitter

    def is_transient(self, exc: Exception) -> bool:
        """Determine if an exception represents a retryable transient failure."""
        if isinstance(exc, PermanentProviderError):
            return False

        if isinstance(exc, (ValueError, TypeError, PermissionError, KeyError)):
            return False

        if isinstance(exc, (TransientProviderError, AIProviderTimeoutError, TimeoutError, ConnectionError, OSError)):
            return True

        status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if isinstance(status_code, int):
            if status_code in TRANSIENT_STATUS_CODES:
                return True
            if status_code in PERMANENT_STATUS_CODES:
                return False

        msg = str(exc).lower()
        permanent_indicators = (
            "400",
            "401",
            "403",
            "404",
            "405",
            "422",
            "unauthorized",
            "forbidden",
            "permission denied",
            "invalid request",
            "not found",
            "authentication failure",
        )
        if any(ind in msg for ind in permanent_indicators):
            return False

        transient_indicators = (
            "429",
            "500",
            "502",
            "503",
            "504",
            "rate limit",
            "too many requests",
            "overloaded",
            "timeout",
            "timed out",
            "connection reset",
            "connection refused",
            "temporarily unavailable",
            "resource temporarily unavailable",
        )
        return any(ind in msg for ind in transient_indicators)

    def compute_delay(self, attempt: int, custom_jitter: float | None = None) -> float:
        """Calculate exponential backoff delay for the given 1-indexed attempt count."""
        exponent = max(0, attempt - 1)
        base = self.initial_delay * (self.backoff_factor**exponent)
        delay = min(self.max_delay, base)

        if self.jitter:
            if custom_jitter is not None:
                delay += custom_jitter
            else:
                delay += random.uniform(0.0, 0.25 * delay)  # noqa: S311
        return delay


class ResilientAIProvider(BaseAIProvider):
    """Resilient wrapper for BaseAIProvider implementing timeouts, retries, and circuit breaking."""

    def __init__(
        self,
        provider: BaseAIProvider,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        embedding_timeout_seconds: float = DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
        telemetry: object | None = None,
    ) -> None:
        if provider is None:
            raise ValueError("provider must not be None")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if embedding_timeout_seconds <= 0:
            raise ValueError("embedding_timeout_seconds must be > 0")

        self._provider = provider
        self._retry_policy = retry_policy or RetryPolicy()
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._timeout_seconds = timeout_seconds
        self._embedding_timeout_seconds = embedding_timeout_seconds
        self._telemetry = telemetry

    @property
    def metadata(self) -> AIProviderMetadata:
        """Return provider metadata from underlying provider."""
        return self._provider.metadata

    @property
    def provider_id(self) -> str:
        """Return unique provider identifier."""
        return self._provider.provider_id

    @property
    def supported_models(self) -> list[str]:
        """Return list of supported models."""
        return self._provider.supported_models

    @property
    def underlying_provider(self) -> BaseAIProvider:
        """Access the inner wrapped BaseAIProvider instance."""
        return self._provider

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Access the attached circuit breaker."""
        return self._circuit_breaker

    @property
    def retry_policy(self) -> RetryPolicy:
        """Access the attached retry policy."""
        return self._retry_policy

    @property
    def timeout_seconds(self) -> float:
        """Configured timeout for generation turns in seconds."""
        return self._timeout_seconds

    @property
    def embedding_timeout_seconds(self) -> float:
        """Configured timeout for embedding generation in seconds."""
        return self._embedding_timeout_seconds

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Generate a text response with timeout enforcement, retries, and circuit breaker protection."""
        attempt = 1
        while True:
            self._circuit_breaker.can_execute()

            try:
                async with asyncio.timeout(self._timeout_seconds):
                    response = await self._provider.generate_text(request)
                self._circuit_breaker.record_success()
                return response

            except TimeoutError as exc:
                timeout_err = AIProviderTimeoutError(
                    f"Provider '{self.provider_id}' timed out after {self._timeout_seconds}s"
                )
                self._circuit_breaker.record_failure(timeout_err)

                if self._telemetry and hasattr(self._telemetry, "emit_provider_timeout"):
                    try:
                        await self._telemetry.emit_provider_timeout(
                            provider_id=self.provider_id,
                            timeout_seconds=self._timeout_seconds,
                            tenant_id=getattr(request, "tenant_id", None),
                        )
                    except Exception as te_exc:
                        logger.debug("Failed to emit timeout telemetry: %s", te_exc)

                if self._retry_policy.is_transient(timeout_err) and attempt < self._retry_policy.max_attempts:
                    delay = self._retry_policy.compute_delay(attempt)
                    logger.warning(
                        "Provider '%s' timed out (attempt %d/%d), retrying in %.2fs...",
                        self.provider_id,
                        attempt,
                        self._retry_policy.max_attempts,
                        delay,
                    )
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
                raise timeout_err from exc

            except Exception as exc:
                self._circuit_breaker.record_failure(exc)
                is_transient = self._retry_policy.is_transient(exc)

                if self._telemetry and hasattr(self._telemetry, "emit_provider_failure"):
                    try:
                        await self._telemetry.emit_provider_failure(
                            provider_id=self.provider_id,
                            error_category=type(exc).__name__,
                            is_transient=is_transient,
                            tenant_id=getattr(request, "tenant_id", None),
                        )
                    except Exception as te_exc:
                        logger.debug("Failed to emit failure telemetry: %s", te_exc)

                if is_transient and attempt < self._retry_policy.max_attempts:
                    delay = self._retry_policy.compute_delay(attempt)
                    logger.warning(
                        "Provider '%s' failed with %s (attempt %d/%d), retrying in %.2fs...",
                        self.provider_id,
                        type(exc).__name__,
                        attempt,
                        self._retry_policy.max_attempts,
                        delay,
                    )
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
                raise

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings with timeout enforcement, retries, and circuit breaker protection."""
        attempt = 1
        while True:
            self._circuit_breaker.can_execute()

            try:
                async with asyncio.timeout(self._embedding_timeout_seconds):
                    embeddings = await self._provider.generate_embeddings(texts)
                self._circuit_breaker.record_success()
                return embeddings

            except TimeoutError as exc:
                timeout_err = AIProviderTimeoutError(
                    f"Provider '{self.provider_id}' embeddings timed out after {self._embedding_timeout_seconds}s"
                )
                self._circuit_breaker.record_failure(timeout_err)

                if self._retry_policy.is_transient(timeout_err) and attempt < self._retry_policy.max_attempts:
                    delay = self._retry_policy.compute_delay(attempt)
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
                raise timeout_err from exc

            except Exception as exc:
                self._circuit_breaker.record_failure(exc)
                is_transient = self._retry_policy.is_transient(exc)

                if is_transient and attempt < self._retry_policy.max_attempts:
                    delay = self._retry_policy.compute_delay(attempt)
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
                raise

    async def health_check(self) -> bool:
        """Check provider reachability; immediately returns False if circuit is OPEN."""
        if self._circuit_breaker.state == CircuitState.OPEN:
            return False
        try:
            return await self._provider.health_check()
        except Exception:
            return False


class ProviderFallbackChain:
    """Executes requests across an ordered sequence of candidate providers with automatic failover."""

    def __init__(
        self,
        providers: list[BaseAIProvider],
        retry_policy: RetryPolicy | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        telemetry: object | None = None,
    ) -> None:
        if not providers:
            raise ValueError("ProviderFallbackChain requires at least one provider.")

        self._telemetry = telemetry
        wrapped_providers: list[ResilientAIProvider] = []
        for p in providers:
            if isinstance(p, ResilientAIProvider):
                wrapped_providers.append(p)
            else:
                wrapped_providers.append(
                    ResilientAIProvider(
                        provider=p,
                        retry_policy=retry_policy,
                        timeout_seconds=timeout_seconds,
                        telemetry=telemetry,
                    )
                )
        self._providers = wrapped_providers

    @property
    def providers(self) -> list[ResilientAIProvider]:
        """Ordered list of resilient providers in the chain."""
        return list(self._providers)

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Attempt text generation sequentially across providers until one succeeds."""
        attempted_errors: list[tuple[str, str]] = []

        for idx, provider in enumerate(self._providers):
            try:
                return await provider.generate_text(request)
            except (
                CircuitBreakerOpenError,
                AIProviderTimeoutError,
                AIProviderError,
                TimeoutError,
                ConnectionError,
                OSError,
            ) as exc:
                error_summary = f"{type(exc).__name__}: {exc}"
                attempted_errors.append((provider.provider_id, error_summary))
                logger.warning(
                    "Fallback provider '%s' failed: %s. Attempting next provider...",
                    provider.provider_id,
                    error_summary,
                )
                if (
                    idx + 1 < len(self._providers)
                    and self._telemetry
                    and hasattr(self._telemetry, "emit_provider_fallback")
                ):
                    try:
                        next_provider = self._providers[idx + 1]
                        await self._telemetry.emit_provider_fallback(
                            primary_provider_id=provider.provider_id,
                            fallback_provider_id=next_provider.provider_id,
                            reason=type(exc).__name__,
                            tenant_id=getattr(request, "tenant_id", None),
                        )
                    except Exception as te_exc:
                        logger.debug("Failed to emit fallback telemetry: %s", te_exc)
                continue
            except Exception as exc:
                # Client/validation/programming errors are not provider failures -> fail fast
                if not isinstance(exc, (AIProviderError, TimeoutError, ConnectionError, OSError)):
                    raise
                attempted_errors.append((provider.provider_id, type(exc).__name__))
                continue

        summary = ", ".join(f"{pid}:{err}" for pid, err in attempted_errors)
        raise ProviderFallbackExhaustedError(
            f"All providers in fallback chain exhausted ({len(attempted_errors)} failed): {summary}"
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Attempt embedding generation sequentially across providers until one succeeds."""
        attempted_errors: list[tuple[str, str]] = []

        for provider in self._providers:
            try:
                return await provider.generate_embeddings(texts)
            except (
                CircuitBreakerOpenError,
                AIProviderTimeoutError,
                AIProviderError,
                TimeoutError,
                ConnectionError,
                OSError,
            ) as exc:
                attempted_errors.append((provider.provider_id, type(exc).__name__))
                logger.warning(
                    "FallbackChain: Provider '%s' embeddings failed (%s), attempting next candidate...",
                    provider.provider_id,
                    type(exc).__name__,
                )
                continue
            except Exception as exc:
                if not isinstance(exc, (AIProviderError, TimeoutError, ConnectionError, OSError)):
                    raise
                attempted_errors.append((provider.provider_id, type(exc).__name__))
                continue

        summary = ", ".join(f"{pid}:{err}" for pid, err in attempted_errors)
        raise ProviderFallbackExhaustedError(
            f"All providers in fallback chain exhausted for embeddings ({len(attempted_errors)} failed): {summary}"
        )

    async def health_check(self) -> dict[str, bool]:
        """Return reachability map for all providers in the fallback chain."""
        results: dict[str, bool] = {}
        for p in self._providers:
            results[p.provider_id] = await p.health_check()
        return results


__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "ProviderFallbackChain",
    "ResilientAIProvider",
    "RetryPolicy",
]
