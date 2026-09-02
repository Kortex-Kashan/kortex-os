"""
Unit tests for KORTEX MemoryCacheStore (Milestone 6).
"""

from __future__ import annotations

import asyncio

import pytest

from kortex.engines.storage.interfaces import ICacheStore
from kortex.engines.storage.stores.cache_store import MemoryCacheStore


@pytest.mark.asyncio
async def test_cache_store_protocol_compliance() -> None:
    """Test MemoryCacheStore satisfies ICacheStore protocol."""
    cache = MemoryCacheStore()
    assert isinstance(cache, ICacheStore)


@pytest.mark.asyncio
async def test_set_and_get() -> None:
    """Test setting and retrieving key-value entries."""
    cache = MemoryCacheStore()
    assert await cache.get("user:1") is None

    await cache.set("user:1", {"name": "Alice"})
    val = await cache.get("user:1")
    assert val == {"name": "Alice"}


@pytest.mark.asyncio
async def test_ttl_expiration() -> None:
    """Test key expiration with TTL."""
    cache = MemoryCacheStore()
    await cache.set("temp_token", "abc123secret", ttl_seconds=1)

    assert await cache.get("temp_token") == "abc123secret"
    await asyncio.sleep(1.1)
    assert await cache.get("temp_token") is None


@pytest.mark.asyncio
async def test_delete_and_clear() -> None:
    """Test deleting keys and clearing entire cache."""
    cache = MemoryCacheStore()
    await cache.set("k1", "v1")
    await cache.set("k2", "v2")

    assert await cache.delete("k1")
    assert await cache.get("k1") is None
    assert not await cache.delete("k1")

    assert await cache.clear()
    assert await cache.get("k2") is None
