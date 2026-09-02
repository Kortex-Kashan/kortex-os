"""
Unit tests for Registry Engine.
"""

import pytest

from kortex.core.exceptions import CapabilityNotFoundError, ResourceAlreadyExistsError, ResourceNotFoundError
from kortex.engines.registry.engine import RegistryCategory, RegistryEngine


class DummyModule:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.mark.asyncio
async def test_registry_register_and_get_resource() -> None:
    registry = RegistryEngine()
    dummy = DummyModule("finance")

    registry.register_resource(
        "finance", RegistryCategory.MODULE, dummy, description="Finance Module", provider="kortex"
    )

    meta = registry.get_resource("finance", RegistryCategory.MODULE)
    assert meta.name == "finance"
    assert meta.category == RegistryCategory.MODULE
    assert meta.provider == "kortex"

    target = registry.get_target_object("finance", RegistryCategory.MODULE)
    assert target is dummy


@pytest.mark.asyncio
async def test_registry_duplicate_registration_raises() -> None:
    registry = RegistryEngine()
    registry.register_resource("hr", RegistryCategory.MODULE)

    with pytest.raises(ResourceAlreadyExistsError):
        registry.register_resource("hr", RegistryCategory.MODULE)


@pytest.mark.asyncio
async def test_registry_nonexistent_resource_raises() -> None:
    registry = RegistryEngine()
    with pytest.raises(ResourceNotFoundError):
        registry.get_resource("unknown", RegistryCategory.MODULE)


@pytest.mark.asyncio
async def test_registry_convenience_methods() -> None:
    registry = RegistryEngine()

    mod = DummyModule("payroll")
    registry.register_module("payroll", mod, description="Payroll Module")
    assert registry.get_module("payroll") is mod

    recipe = {"steps": ["calc", "approve"]}
    registry.register_recipe("calc_payroll", recipe)
    assert registry.get_recipe("calc_payroll") == recipe

    tmpl = "<html>Receipt</html>"
    registry.register_template("receipt", tmpl)
    assert registry.get_template("receipt") == tmpl

    conn = {"type": "slack"}
    registry.register_connector("slack", conn)
    assert registry.get_connector("slack") == conn

    svc = {"type": "auth"}
    registry.register_service("auth", svc)
    assert registry.get_service("auth") == svc


@pytest.mark.asyncio
async def test_registry_capability_registration_and_lookup() -> None:
    registry = RegistryEngine()

    def dummy_handler(args: dict) -> dict:
        return {"status": "ok"}

    descriptor = registry.register_capability(
        name="payroll.calculate",
        description="Calculate employee net salary",
        provider="payroll_module",
        handler=dummy_handler,
        parameters_schema={"type": "object"},
    )

    assert descriptor.name == "payroll.calculate"
    assert descriptor.provider == "payroll_module"

    fetched = registry.get_capability("payroll.calculate")
    assert fetched.name == "payroll.calculate"

    all_caps = registry.list_capabilities()
    assert len(all_caps) == 1
    assert all_caps[0].name == "payroll.calculate"


@pytest.mark.asyncio
async def test_registry_nonexistent_capability_raises() -> None:
    registry = RegistryEngine()
    with pytest.raises(CapabilityNotFoundError):
        registry.get_capability("nonexistent.capability")
