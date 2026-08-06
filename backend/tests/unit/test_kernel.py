"""
Unit tests for Kernel Runtime.
"""

import pytest
from typing import Dict, Any, List

from kortex.core.kernel import Kernel, KernelState
from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.exceptions import KernelStateError, ResourceAlreadyExistsError, ResourceNotFoundError
from kortex.engines.event.engine import Event


class MockCustomEngine(BaseEngine):
    def __init__(self, name: str = "custom_test") -> None:
        self._name = name
        super().__init__()
        self.boot_called = False

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self, kernel: Kernel) -> None:
        self._set_state(EngineState.READY)

    async def start(self) -> None:
        self.boot_called = True
        self._set_state(EngineState.RUNNING)

    async def health_check(self) -> Dict[str, Any]:
        return {"engine": self._name, "status": "healthy"}

    async def stop(self) -> None:
        self._set_state(EngineState.STOPPED)


@pytest.mark.asyncio
async def test_kernel_initialization() -> None:
    kernel = Kernel()
    assert kernel.state == KernelState.CREATED
    assert kernel.get_engine("configuration") is not None
    assert kernel.get_engine("registry") is not None
    assert kernel.get_engine("event") is not None
    assert kernel.get_engine("boot") is not None


@pytest.mark.asyncio
async def test_kernel_custom_engine_registration() -> None:
    kernel = Kernel()
    custom = MockCustomEngine("analytics")
    kernel.register_engine(custom)

    assert kernel.get_engine("analytics") is custom
    assert kernel.container.has("engine.analytics") is True


@pytest.mark.asyncio
async def test_kernel_duplicate_engine_registration_raises() -> None:
    kernel = Kernel()
    custom = MockCustomEngine("analytics")
    kernel.register_engine(custom)

    with pytest.raises(ResourceAlreadyExistsError):
        kernel.register_engine(MockCustomEngine("analytics"))


@pytest.mark.asyncio
async def test_kernel_get_nonexistent_engine_raises() -> None:
    kernel = Kernel()
    with pytest.raises(ResourceNotFoundError):
        kernel.get_engine("nonexistent")


@pytest.mark.asyncio
async def test_kernel_boot_and_shutdown_lifecycle() -> None:
    kernel = Kernel()
    custom = MockCustomEngine("worker")
    kernel.register_engine(custom)

    events_received: List[str] = []

    def on_startup(event: Event) -> None:
        events_received.append(event.topic)

    kernel.subscribe_event("system.started", on_startup)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    assert custom.boot_called is True
    assert "system.started" in events_received

    # Double boot should raise KernelStateError
    with pytest.raises(KernelStateError):
        await kernel.boot()

    health = await kernel.health_check()
    assert health["kernel_state"] == "RUNNING"
    assert health["db_connected"] is True

    await kernel.shutdown()
    assert kernel.state == KernelState.STOPPED


@pytest.mark.asyncio
async def test_kernel_capability_registration_and_lookup() -> None:
    kernel = Kernel()

    def handle_calc(data: dict) -> dict:
        return {"result": 42}

    kernel.register_capability(
        name="finance.tax_calc",
        description="Calculate income tax",
        provider="finance_module",
        handler=handle_calc,
    )

    cap = kernel.get_capability("finance.tax_calc")
    assert cap.name == "finance.tax_calc"
    assert cap.provider == "finance_module"

    caps = kernel.list_capabilities()
    assert len(caps) == 1
    assert caps[0].name == "finance.tax_calc"


@pytest.mark.asyncio
async def test_kernel_delegation_methods() -> None:
    kernel = Kernel()

    # Configuration delegation
    kernel.set_config("custom_key", "custom_val")
    assert kernel.get_config("custom_key") == "custom_val"

    # Module delegation
    mod = {"type": "hr"}
    kernel.register_module("hr", mod)
    assert kernel.get_module("hr") == mod

    # Connector delegation
    conn = {"type": "stripe"}
    kernel.register_connector("stripe", conn)
    assert kernel.get_connector("stripe") == conn

    # Recipe delegation
    rec = {"steps": []}
    kernel.register_recipe("payroll_recipe", rec)
    assert kernel.get_recipe("payroll_recipe") == rec

    # Template delegation
    tmpl = "<html>Receipt</html>"
    kernel.register_template("receipt_tmpl", tmpl)
    assert kernel.get_template("receipt_tmpl") == tmpl

    # Event unsubscribe delegation
    sub_id = kernel.subscribe_event("test.topic", lambda e: None)
    assert kernel.unsubscribe_event(sub_id) is True
