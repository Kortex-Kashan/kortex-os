"""
Unit tests for Boot Engine.
"""

import pytest
from typing import List, Dict, Any

from kortex.engines.boot.engine import BootEngine
from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.exceptions import KernelBootError
from kortex.core.kernel import Kernel


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
