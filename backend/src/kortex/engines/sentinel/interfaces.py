"""KORTEX Sentinel Engine — Interfaces and Protocols.

Defines the explicit contracts for heartbeat sources, probes, and diagnostic providers.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IHeartbeatSource(Protocol):
    """Protocol for active components that report liveness via periodic heartbeats."""

    @property
    def source_id(self) -> str:
        """Unique identifier of the heartbeat source."""
        ...

    @property
    def expected_interval_seconds(self) -> float:
        """Expected interval in seconds between consecutive heartbeats."""
        ...
