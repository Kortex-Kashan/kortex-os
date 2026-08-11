"""Unit tests for TokenBucketRateLimiter and Exponential Backoff (Milestone 4).

Target: 100% pass rate, 100% line coverage for rate_limiter.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kortex.engines.connector.exceptions import RateLimitExceededError
from kortex.engines.connector.interfaces import IRateLimiter
from kortex.engines.connector.rate_limiter import (
    TokenBucketRateLimiter,
    calculate_backoff_delay,
    execute_with_retry,
)
from kortex.engines.storage.stores.cache_store import MemoryCacheStore


def test_protocol_compliance() -> None:
    """Test that TokenBucketRateLimiter satisfies IRateLimiter protocol."""
    limiter = TokenBucketRateLimiter()
    assert isinstance(limiter, IRateLimiter)


def test_calculate_backoff_delay() -> None:
    """Test calculate_backoff_delay formula, max delay cap, negative attempt, and jitter."""
    # Jitter False
    assert calculate_backoff_delay(0, base_delay=1.0, jitter=False) == 1.0
    assert calculate_backoff_delay(1, base_delay=1.0, jitter=False) == 2.0
    assert calculate_backoff_delay(2, base_delay=1.0, jitter=False) == 4.0

    # Negative attempt normalization
    assert calculate_backoff_delay(-2, base_delay=1.0, jitter=False) == 1.0

    # Max delay cap
    assert calculate_backoff_delay(10, base_delay=1.0, max_delay=10.0, jitter=False) == 10.0

    # Jitter True returns value between 0 and capped delay
    delay_jitter = calculate_backoff_delay(2, base_delay=2.0, jitter=True)
    assert 0.0 <= delay_jitter <= 8.0


@pytest.mark.asyncio
async def test_acquire_and_release_token_in_memory() -> None:
    """Test acquire_token and release_token using local in-memory fallback."""
    limiter = TokenBucketRateLimiter(default_capacity=5.0, default_refill_rate=1.0)

    # Acquire 3 tokens out of 5
    assert await limiter.acquire_token("key-1", tokens=3.0) is True

    # Acquire another 2 tokens
    assert await limiter.acquire_token("key-1", tokens=2.0) is True

    # Depleted: acquire 1 token fails
    assert await limiter.acquire_token("key-1", tokens=1.0) is False

    # Release 2 tokens back
    await limiter.release_token("key-1", tokens=2.0)

    # Now acquiring 1 token succeeds
    assert await limiter.acquire_token("key-1", tokens=1.0) is True


@pytest.mark.asyncio
async def test_token_replenishment_over_time() -> None:
    """Test that tokens replenish over time based on refill rate."""
    limiter = TokenBucketRateLimiter(default_capacity=2.0, default_refill_rate=10.0)

    # Drain bucket
    assert await limiter.acquire_token("key-time", tokens=2.0) is True
    assert await limiter.acquire_token("key-time", tokens=1.0) is False

    # Sleep briefly to replenish tokens
    await asyncio.sleep(0.15)  # Replenishes ~1.5 tokens

    # Now acquiring 1 token succeeds
    assert await limiter.acquire_token("key-time", tokens=1.0) is True


@pytest.mark.asyncio
async def test_custom_capacity_and_refill_override() -> None:
    """Test custom capacity and refill_rate overrides during acquire_token."""
    limiter = TokenBucketRateLimiter(default_capacity=10.0, default_refill_rate=10.0)

    # Custom capacity 2.0
    assert await limiter.acquire_token("key-custom", tokens=2.0, capacity=2.0, refill_rate=1.0) is True
    assert await limiter.acquire_token("key-custom", tokens=1.0, capacity=2.0, refill_rate=1.0) is False


@pytest.mark.asyncio
async def test_cache_store_integration() -> None:
    """Test TokenBucketRateLimiter with MemoryCacheStore backend."""
    cache = MemoryCacheStore()
    limiter = TokenBucketRateLimiter(cache_store=cache, default_capacity=5.0, default_refill_rate=5.0)

    assert await limiter.acquire_token("cache-key", tokens=3.0) is True

    # Verify state was written to cache store
    cached_val = await cache.get("connector:ratelimit:cache-key")
    assert isinstance(cached_val, dict)
    assert cached_val["tokens"] == 2.0

    # Acquire again using cached state
    assert await limiter.acquire_token("cache-key", tokens=2.0) is True
    assert await limiter.acquire_token("cache-key", tokens=1.0) is False

    # Release token updates cache store
    await limiter.release_token("cache-key", tokens=1.0)
    cached_after_rel = await cache.get("connector:ratelimit:cache-key")
    assert cached_after_rel["tokens"] >= 1.0


@pytest.mark.asyncio
async def test_cache_store_exception_resilience() -> None:
    """Test resilience when ICacheStore raises exceptions during read or write."""

    class BrokenCacheStore:
        async def get(self, key: str) -> Any:
            raise RuntimeError("Cache connection error on get")

        async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
            raise RuntimeError("Cache connection error on set")

        async def delete(self, key: str) -> bool:
            return True

        async def clear(self) -> bool:
            return True

    broken_cache = BrokenCacheStore()
    limiter = TokenBucketRateLimiter(cache_store=broken_cache, default_capacity=3.0, default_refill_rate=1.0)  # type: ignore[arg-type]

    # Acquire succeeds despite broken cache store via local memory fallback
    assert await limiter.acquire_token("broken-key", tokens=2.0) is True
    assert await limiter.acquire_token("broken-key", tokens=1.0) is True
    assert await limiter.acquire_token("broken-key", tokens=1.0) is False

    # Release succeeds despite broken cache
    await limiter.release_token("broken-key", tokens=1.0)
    assert await limiter.acquire_token("broken-key", tokens=1.0) is True


@pytest.mark.asyncio
async def test_concurrency_race_condition_prevention() -> None:
    """Test concurrent acquire_token calls using asyncio.gather."""
    limiter = TokenBucketRateLimiter(default_capacity=10.0, default_refill_rate=0.0)

    # 15 concurrent tasks acquiring 1 token each from a bucket of capacity 10
    tasks = [limiter.acquire_token("concurrent-key", tokens=1.0) for _ in range(15)]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r is True)
    fail_count = sum(1 for r in results if r is False)

    assert success_count == 10
    assert fail_count == 5


@pytest.mark.asyncio
async def test_execute_with_retry_success() -> None:
    """Test execute_with_retry succeeding on first attempt or after retries."""
    attempts = 0

    async def sample_func(value: int) -> int:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("Transient error")
        return value * 2

    res = await execute_with_retry(
        sample_func, 21, max_retries=3, base_delay=0.01, jitter=False, retryable_exceptions=(ValueError,)
    )
    assert res == 42
    assert attempts == 3


@pytest.mark.asyncio
async def test_execute_with_retry_exhausted() -> None:
    """Test execute_with_retry raising exception when max retries are exhausted."""
    attempts = 0

    async def always_fails() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("Persistent failure")

    with pytest.raises(RuntimeError) as exc_info:
        await execute_with_retry(
            always_fails, max_retries=2, base_delay=0.01, jitter=False, retryable_exceptions=(RuntimeError,)
        )

    assert "Persistent failure" in str(exc_info.value)
    assert attempts == 3  # 1 initial attempt + 2 retries
