"""
KORTEX Memory CacheStore Implementation.

Implements the ICacheStore protocol for in-memory ephemeral key-value caching with
Time-To-Live (TTL) expiration support and atomic cache invalidation operations.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from kortex.engines.storage.interfaces import ICacheStore

logger = logging.getLogger("kortex.engines.storage.stores.cache_store")


class MemoryCacheStore(ICacheStore):
    """In-memory key-value cache store implementing ICacheStore."""

    def __init__(self) -> None:
        """Initialize MemoryCacheStore dictionary index."""
        # Maps key -> (value, expire_timestamp_or_none)
        self._cache: dict[str, tuple[Any, float | None]] = {}
        logger.debug("Initialized MemoryCacheStore")

    async def get(self, key: str) -> Any | None:
        """Retrieve a cached value by key.

        Args:
            key: Cache key string.

        Returns:
            Cached value if present and not expired, else None.
        """
        if key not in self._cache:
            return None

        value, expire_at = self._cache[key]
        if expire_at is not None and time.time() >= expire_at:
            logger.debug("Cache key expired: '%s'", key)
            del self._cache[key]
            return None

        return value

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        """Store a value in cache with an optional Time-To-Live (TTL) in seconds.

        Args:
            key: Cache key string.
            value: Value to store.
            ttl_seconds: Optional TTL expiration in seconds.

        Returns:
            True on successful caching.
        """
        expire_at = (time.time() + ttl_seconds) if ttl_seconds is not None else None
        self._cache[key] = (value, expire_at)
        logger.debug("Cached key '%s' (TTL: %s)", key, ttl_seconds)
        return True

    async def delete(self, key: str) -> bool:
        """Delete a key from the cache.

        Args:
            key: Cache key string.

        Returns:
            True if key was removed, False if key did not exist.
        """
        if key in self._cache:
            del self._cache[key]
            logger.debug("Deleted cache key: '%s'", key)
            return True
        return False

    async def clear(self) -> bool:
        """Clear all cached key-value entries."""
        count = len(self._cache)
        self._cache.clear()
        logger.debug("Cleared %d entries from MemoryCacheStore", count)
        return True
