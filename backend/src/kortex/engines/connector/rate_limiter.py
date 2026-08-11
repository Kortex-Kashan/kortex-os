"""Token Bucket Rate Limiter and Exponential Backoff Manager for KORTEX OS Connector Engine.

This module implements TokenBucketRateLimiter (satisfying IRateLimiter) backed by optional
Storage Engine ICacheStore with local in-memory fallback, and provides exponential backoff
delay calculations and async retry handling in accordance with the Connector Engine Specification.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Callable, Coroutine

from kortex.engines.connector.exceptions import RateLimitExceededError
from kortex.engines.connector.interfaces import IRateLimiter
from kortex.engines.storage.interfaces import ICacheStore


def calculate_backoff_delay(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    backoff_factor: float = 2.0,
) -> float:
    """Calculate exponential backoff delay with optional randomized jitter.

    Args:
        attempt: Zero-based or 1-based attempt index (0, 1, 2...).
        base_delay: Initial delay in seconds (default 1.0).
        max_delay: Maximum delay cap in seconds (default 60.0).
        jitter: Whether to apply randomized jitter (default True).
        backoff_factor: Multiplicative factor for exponential scaling (default 2.0).

    Returns:
        Calculated delay in seconds as float.
    """
    attempt_idx = max(0, attempt)
    calculated = base_delay * (backoff_factor**attempt_idx)
    capped_delay = min(max_delay, calculated)

    if jitter:
        return random.uniform(0.0, capped_delay)

    return capped_delay


class TokenBucketRateLimiter(IRateLimiter):
    """Token-bucket rate limiter implementing IRateLimiter protocol.

    Supports out-of-the-box integration with Storage Engine ICacheStore with fallback
    to local in-memory thread-safe rate limit tracking.
    """

    def __init__(
        self,
        cache_store: ICacheStore | None = None,
        default_capacity: float = 10.0,
        default_refill_rate: float = 10.0,
    ) -> None:
        """Initialize TokenBucketRateLimiter.

        Args:
            cache_store: Optional ICacheStore instance from Storage Engine.
            default_capacity: Default maximum bucket burst capacity.
            default_refill_rate: Default token replenishment rate per second.
        """
        self._cache_store = cache_store
        self._default_capacity = float(default_capacity)
        self._default_refill_rate = float(default_refill_rate)
        self._local_buckets: dict[str, dict[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _get_lock_for_key(self, key: str) -> asyncio.Lock:
        """Get or create per-key asyncio.Lock safely."""
        async with self._global_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    def _get_cache_key(self, key: str) -> str:
        """Format canonical cache key string."""
        return f"connector:ratelimit:{key.strip()}"

    async def _read_bucket_state(
        self, key: str, capacity: float, refill_rate: float
    ) -> dict[str, float]:
        """Read bucket state from cache_store or local memory, initializing if missing."""
        now = time.monotonic()
        if self._cache_store is not None:
            try:
                cached = await self._cache_store.get(self._get_cache_key(key))
                if isinstance(cached, dict):
                    return {
                        "tokens": float(cached.get("tokens", capacity)),
                        "last_refill": float(cached.get("last_refill", now)),
                        "capacity": float(cached.get("capacity", capacity)),
                        "refill_rate": float(cached.get("refill_rate", refill_rate)),
                    }
            except Exception:
                pass  # Fall through to local buckets on cache errors

        if key in self._local_buckets:
            return dict(self._local_buckets[key])

        return {
            "tokens": capacity,
            "last_refill": now,
            "capacity": capacity,
            "refill_rate": refill_rate,
        }

    async def _write_bucket_state(self, key: str, state: dict[str, float]) -> None:
        """Write bucket state to cache_store or local memory."""
        self._local_buckets[key] = dict(state)
        if self._cache_store is not None:
            try:
                # Set TTL to 1 hour (3600s) to keep cache clean
                await self._cache_store.set(self._get_cache_key(key), state, ttl_seconds=3600)
            except Exception:
                pass  # Ignore cache write exceptions to ensure reliability

    async def acquire_token(
        self,
        key: str,
        tokens: float = 1.0,
        capacity: float | None = None,
        refill_rate: float | None = None,
    ) -> bool:
        """Attempt to acquire tokens for a given rate limit key.

        Args:
            key: Rate limit identifier string.
            tokens: Number of tokens requested (default 1.0).
            capacity: Optional custom bucket capacity (defaults to limiter default).
            refill_rate: Optional custom refill rate per second.

        Returns:
            True if tokens were acquired, False if rate limit is exceeded.
        """
        cap = float(capacity if capacity is not None else self._default_capacity)
        rate = float(refill_rate if refill_rate is not None else self._default_refill_rate)
        tokens_requested = float(tokens)

        lock = await self._get_lock_for_key(key)
        async with lock:
            state = await self._read_bucket_state(key, cap, rate)
            now = time.monotonic()
            elapsed = max(0.0, now - state["last_refill"])

            # Replenish tokens over elapsed time
            replenished = elapsed * state["refill_rate"]
            current_tokens = min(state["capacity"], state["tokens"] + replenished)

            if current_tokens >= tokens_requested:
                state["tokens"] = current_tokens - tokens_requested
                state["last_refill"] = now
                await self._write_bucket_state(key, state)
                return True

            # Insufficient tokens
            state["tokens"] = current_tokens
            state["last_refill"] = now
            await self._write_bucket_state(key, state)
            return False

    async def release_token(self, key: str, tokens: float = 1.0) -> None:
        """Release tokens back to the specified rate limit key.

        Args:
            key: Rate limit identifier string.
            tokens: Number of tokens to release back (default 1.0).
        """
        tokens_released = float(tokens)
        lock = await self._get_lock_for_key(key)
        async with lock:
            state = await self._read_bucket_state(
                key, self._default_capacity, self._default_refill_rate
            )
            now = time.monotonic()
            elapsed = max(0.0, now - state["last_refill"])

            replenished = elapsed * state["refill_rate"]
            current_tokens = min(state["capacity"], state["tokens"] + replenished)

            state["tokens"] = min(state["capacity"], current_tokens + tokens_released)
            state["last_refill"] = now
            await self._write_bucket_state(key, state)


async def execute_with_retry(
    coro_func: Callable[..., Coroutine[Any, Any, Any]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple[type[BaseException], ...] = (Exception,),
    **kwargs: Any,
) -> Any:
    """Execute an async function with exponential backoff retries on failure.

    Args:
        coro_func: Async callable function.
        *args: Positional arguments for coro_func.
        max_retries: Maximum retry attempts (default 3).
        base_delay: Base delay in seconds (default 1.0).
        max_delay: Maximum delay cap in seconds (default 60.0).
        jitter: Whether to apply randomized jitter (default True).
        backoff_factor: Multiplicative factor for backoff scaling (default 2.0).
        retryable_exceptions: Tuple of exception types to catch and retry.
        **kwargs: Keyword arguments for coro_func.

    Returns:
        Return value from coro_func execution.

    Raises:
        The caught exception if max_retries attempts are exhausted.
    """
    attempt = 0
    while True:
        try:
            return await coro_func(*args, **kwargs)
        except retryable_exceptions as err:
            if attempt >= max_retries:
                raise err
            delay = calculate_backoff_delay(
                attempt=attempt,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
                backoff_factor=backoff_factor,
            )
            attempt += 1
            await asyncio.sleep(delay)


__all__ = [
    "TokenBucketRateLimiter",
    "calculate_backoff_delay",
    "execute_with_retry",
]
