"""
Unit tests for Boot Engine.
"""

from typing import Any, Dict, List, Optional

import pytest

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.exceptions import KernelBootError
from kortex.core.kernel import Kernel
from kortex.engines.boot.engine import BootEngine
from kortex.engines.security.exceptions import MasterKeyError


class MockEngine(BaseEngine):
    def __init__(self, name: str, deps: List[str] = None) -> None:
        self._name = name
        self._deps = deps or []
        super().__init__()
        self.initialized = False
        self.started = False
        self.stopped = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def dependencies(self) -> List[str]:
        return self._deps

    async def initialize(self, kernel: Kernel) -> None:
        self.initialized = True
        self._set_state(EngineState.READY)

    async def start(self) -> None:
        self.started = True
        self._set_state(EngineState.RUNNING)

    async def health_check(self) -> Dict[str, Any]:
        return {"engine": self._name, "status": "healthy"}

    async def stop(self) -> None:
        self.stopped = True
        self._set_state(EngineState.STOPPED)


def test_boot_engine_topological_sort() -> None:
    boot = BootEngine()

    engine_a = MockEngine("a")
    engine_b = MockEngine("b", deps=["a"])
    engine_c = MockEngine("c", deps=["b"])

    engines = {"c": engine_c, "a": engine_a, "b": engine_b}
    order = boot.resolve_dependency_order(engines)

    assert order == ["a", "b", "c"]


def test_boot_engine_cyclic_dependency_raises() -> None:
    boot = BootEngine()

    engine_a = MockEngine("a", deps=["b"])
    engine_b = MockEngine("b", deps=["a"])

    engines = {"a": engine_a, "b": engine_b}
    with pytest.raises(KernelBootError):
        boot.resolve_dependency_order(engines)


def test_boot_engine_missing_dependency_raises() -> None:
    boot = BootEngine()

    engine_a = MockEngine("a", deps=["missing_engine"])
    engines = {"a": engine_a}

    with pytest.raises(KernelBootError):
        boot.resolve_dependency_order(engines)


class _RaisingInitializeEngine(BaseEngine):
    """Minimal engine whose `initialize()` always raises a supplied exception.

    Used to regression-test BootEngine's exception-handling/logging path in
    `boot_system()` — specifically that logging the failure never raises a
    secondary exception that would mask the original error.
    """

    def __init__(self, name: str, error: Exception, deps: Optional[List[str]] = None) -> None:
        self._name = name
        self._deps = deps or []
        self._error = error
        super().__init__()

    @property
    def name(self) -> str:
        return self._name

    @property
    def dependencies(self) -> List[str]:
        return self._deps

    async def initialize(self, kernel: Kernel) -> None:
        raise self._error

    async def start(self) -> None:
        self._set_state(EngineState.RUNNING)

    async def health_check(self) -> Dict[str, Any]:
        return {"engine": self._name, "status": "healthy"}

    async def stop(self) -> None:
        self._set_state(EngineState.STOPPED)


@pytest.mark.asyncio
async def test_boot_system_preserves_master_key_error_as_boot_error_cause() -> None:
    """Regression test for the fixed `%e`/`%s` logging format-string defect.

    When an engine's `initialize()` raises `MasterKeyError`, `boot_system()`
    must:
      1. raise `KernelBootError` (boot fails),
      2. with the original `MasterKeyError` preserved and inspectable as
         `__cause__` (failure propagation is not lost or replaced),
      3. without the `self.logger.critical(...)` call itself raising a
         secondary `TypeError` (which would previously mask both of the above
         with an unrelated logging-layer crash instead of the intended
         `KernelBootError`).
    """
    kernel = Kernel()
    boot = BootEngine()
    original_error = MasterKeyError("KORTEX_MASTER_KEY is missing or empty.")
    failing_engine = _RaisingInitializeEngine("failing_security_like_engine", error=original_error)
    kernel.register_engine(failing_engine)

    with pytest.raises(KernelBootError) as exc_info:
        await boot.boot_system(kernel)

    assert exc_info.value.__cause__ is original_error
    assert isinstance(exc_info.value.__cause__, MasterKeyError)
