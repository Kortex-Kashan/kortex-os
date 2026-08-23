"""Tenant concurrency and rate throttling for KORTEX AI Orchestration Engine.

Governed by Milestone 12 architecture:
Enforces per-tenant concurrency limits across generation and multi-step agent workflows
to prevent resource starvation, noisy-neighbor degradation, and denial of service.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from kortex.engines.ai.exceptions import TenantQuotaExceededError
from kortex.engines.ai.memory import require_identifier

DEFAULT_MAX_CONCURRENT_GENERATIONS_PER_TENANT: Final[int] = 10
DEFAULT_MAX_CONCURRENT_AGENTS_PER_TENANT: Final[int] = 5


class TenantConcurrencyThrottler:
    """Manages active concurrent execution slots per tenant."""

    def __init__(
        self,
        max_concurrent_generations: int = DEFAULT_MAX_CONCURRENT_GENERATIONS_PER_TENANT,
        max_concurrent_agents: int = DEFAULT_MAX_CONCURRENT_AGENTS_PER_TENANT,
    ) -> None:
        self._max_generations = max(1, max_concurrent_generations)
        self._max_agents = max(1, max_concurrent_agents)
        self._lock = asyncio.Lock()
        self._active_generations: dict[str, int] = {}
        self._active_agents: dict[str, int] = {}

    @property
    def max_concurrent_generations(self) -> int:
        """Maximum concurrent response generation requests allowed per tenant."""
        return self._max_generations

    @property
    def max_concurrent_agents(self) -> int:
        """Maximum concurrent agent reasoning workflows allowed per tenant."""
        return self._max_agents

    def get_active_generations(self, tenant_id: str) -> int:
        """Return currently active generation slots for the given tenant."""
        return self._active_generations.get(tenant_id, 0)

    def get_active_agents(self, tenant_id: str) -> int:
        """Return currently active agent workflow slots for the given tenant."""
        return self._active_agents.get(tenant_id, 0)

    @asynccontextmanager
    async def acquire_generation_slot(self, tenant_id: str) -> AsyncIterator[None]:
        """Acquire an active generation slot or raise TenantQuotaExceededError."""
        require_identifier(tenant_id, "tenant_id")
        async with self._lock:
            current = self._active_generations.get(tenant_id, 0)
            if current >= self._max_generations:
                raise TenantQuotaExceededError(
                    tenant_id,
                    f"Active generation limit of {self._max_generations} concurrent requests reached.",
                )
            self._active_generations[tenant_id] = current + 1

        try:
            yield
        finally:
            async with self._lock:
                val = self._active_generations.get(tenant_id, 1) - 1
                if val <= 0:
                    self._active_generations.pop(tenant_id, None)
                else:
                    self._active_generations[tenant_id] = val

    @asynccontextmanager
    async def acquire_agent_slot(self, tenant_id: str) -> AsyncIterator[None]:
        """Acquire an active agent reasoning slot or raise TenantQuotaExceededError."""
        require_identifier(tenant_id, "tenant_id")
        async with self._lock:
            current = self._active_agents.get(tenant_id, 0)
            if current >= self._max_agents:
                raise TenantQuotaExceededError(
                    tenant_id,
                    f"Active agent workflow limit of {self._max_agents} concurrent tasks reached.",
                )
            self._active_agents[tenant_id] = current + 1

        try:
            yield
        finally:
            async with self._lock:
                val = self._active_agents.get(tenant_id, 1) - 1
                if val <= 0:
                    self._active_agents.pop(tenant_id, None)
                else:
                    self._active_agents[tenant_id] = val
