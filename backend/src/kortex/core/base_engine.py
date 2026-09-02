"""
KORTEX Core Base Engine Contract.

Defines the lifecycle states and abstract base class for all 21 System Engines.
"""

from __future__ import annotations

import abc
import enum
import logging
from typing import TYPE_CHECKING, Any

from kortex.core.exceptions import EngineStateError

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel


class EngineState(str, enum.Enum):
    """Lifecycle states for a KORTEX System Engine."""

    UNINITIALIZED = "UNINITIALIZED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class BaseEngine(abc.ABC):
    """Abstract base class for all KORTEX System Engines.

    Enforces Clean Architecture, standard lifecycle management (initialize,
    start, health_check, stop), and dependency declarations.
    """

    def __init__(self) -> None:
        self._state: EngineState = EngineState.UNINITIALIZED
        self._logger: logging.Logger = logging.getLogger(f"kortex.engine.{self.name}")

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique identifier name for this engine."""

    @property
    def dependencies(self) -> list[str]:
        """Names of other engines this engine depends on for startup ordering."""
        return []

    @property
    def state(self) -> EngineState:
        """Current operational state of the engine."""
        return self._state

    @property
    def logger(self) -> logging.Logger:
        """Logger instance dedicated to this engine."""
        return self._logger

    def _set_state(self, new_state: EngineState) -> None:
        """Transition the engine to a new state with logging."""
        self._logger.debug("Engine '%s' state transition: %s -> %s", self.name, self._state, new_state)
        self._state = new_state

    @abc.abstractmethod
    async def initialize(self, kernel: Kernel) -> None:
        """Initialize engine resources and register capabilities with the Kernel.

        Args:
            kernel: The running Kernel instance providing core services.
        """

    @abc.abstractmethod
    async def start(self) -> None:
        """Start active background services, listeners, or loops."""

    @abc.abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Return diagnostic health information about the engine status.

        Returns:
            Dictionary containing health metrics and status flags.
        """

    @abc.abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down active background tasks and release resources."""

    def ensure_state(self, *expected_states: EngineState) -> None:
        """Validate that the engine is currently in one of the expected states.

        Raises:
            EngineStateError: If the current state is not in expected_states.
        """
        if self._state not in expected_states:
            expected_names = [s.name for s in expected_states]
            raise EngineStateError(
                f"Engine '{self.name}' is in state {self._state.name}, expected one of: {expected_names}"
            )
