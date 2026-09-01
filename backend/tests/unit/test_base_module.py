"""Unit tests for `kortex.core.base_module.BaseModule` (Finance-pilot planning
pass, "Module base contract" — `.kortex/roadmap.md` Phase 6).

Covers only the minimal lifecycle this slice implements (construction ->
initialize -> ACTIVE -> stop) -- the full 7-state `business_module_
architecture.md` machine (Unloaded/Installed/Disabled/Superseded/
Uninstalled) is deliberately deferred and has no tests here, per the
implementation boundary.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from kortex.core.base_module import BaseModule, ModuleState
from kortex.core.exceptions import EngineStateError


class _FakeModule(BaseModule):
    """Minimal concrete `BaseModule` for lifecycle testing, independent of
    any real Kernel/Storage dependency."""

    def __init__(self) -> None:
        super().__init__()
        self.initialize_called_with: Any = None
        self.registered = False

    @property
    def name(self) -> str:
        return "fake-module"

    @property
    def namespace(self) -> str:
        return "kortex.fake"

    async def initialize(self, kernel: Any) -> None:
        self.ensure_state(ModuleState.UNINITIALIZED)
        self._set_state(ModuleState.INITIALIZING)
        self.initialize_called_with = kernel
        self.registered = True
        self._set_state(ModuleState.ACTIVE)

    async def start(self) -> None:
        self.ensure_state(ModuleState.ACTIVE)

    async def stop(self) -> None:
        self.ensure_state(ModuleState.ACTIVE)
        self._set_state(ModuleState.STOPPING)
        self._set_state(ModuleState.STOPPED)

    async def health_check(self) -> Dict[str, Any]:
        return {"module": self.name, "status": "healthy" if self._state == ModuleState.ACTIVE else "unhealthy"}


def test_construction_starts_uninitialized() -> None:
    module = _FakeModule()
    assert module.state == ModuleState.UNINITIALIZED
    assert module.name == "fake-module"
    assert module.namespace == "kortex.fake"
    assert module.dependencies == []


@pytest.mark.asyncio
async def test_initialize_transitions_to_active_and_calls_registration() -> None:
    module = _FakeModule()
    sentinel_kernel = object()

    await module.initialize(sentinel_kernel)

    assert module.state == ModuleState.ACTIVE
    assert module.registered is True
    assert module.initialize_called_with is sentinel_kernel


@pytest.mark.asyncio
async def test_start_requires_active_state() -> None:
    module = _FakeModule()
    with pytest.raises(EngineStateError):
        await module.start()

    await module.initialize(object())
    await module.start()  # no-op, must not raise once ACTIVE


@pytest.mark.asyncio
async def test_stop_transitions_active_to_stopped() -> None:
    module = _FakeModule()
    await module.initialize(object())
    assert module.state == ModuleState.ACTIVE

    await module.stop()
    assert module.state == ModuleState.STOPPED


@pytest.mark.asyncio
async def test_stop_before_initialize_raises_engine_state_error() -> None:
    module = _FakeModule()
    with pytest.raises(EngineStateError):
        await module.stop()


@pytest.mark.asyncio
async def test_health_check_reflects_current_state() -> None:
    module = _FakeModule()
    await module.initialize(object())

    report = await module.health_check()
    assert report["status"] == "healthy"

    await module.stop()
    report = await module.health_check()
    assert report["status"] == "unhealthy"


def test_ensure_state_raises_engine_state_error_with_module_name() -> None:
    module = _FakeModule()
    with pytest.raises(EngineStateError, match="fake-module"):
        module.ensure_state(ModuleState.ACTIVE)
