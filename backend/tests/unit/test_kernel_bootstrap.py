"""Unit tests for `kortex.api.kernel_bootstrap.build_and_boot_kernel`.

M3 shipped this module with only Storage + Security registered (confirmed
during the M5 preflight audit — no test previously exercised it directly).
M5 adds the Connector Engine to this same bootstrap; these tests prove that
wiring lands on the real production boot path, not only on the hand-built
test kernels `test_capability_dispatch.py` / `test_connector_engine.py`
construct for themselves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.api.kernel_bootstrap import build_and_boot_kernel
from kortex.core.kernel import KernelState
from kortex.engines.connector.engine import ConnectorEngine


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(tmp_path / "kernel_bootstrap_storage"))


@pytest.mark.asyncio
async def test_connector_engine_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        assert kernel.state == KernelState.RUNNING
        connector_engine = kernel.get_engine("connector")
        assert isinstance(connector_engine, ConnectorEngine)
        assert connector_engine.status() == "RUNNING"
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_connector_driver_list_capability_registers_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        descriptor = kernel.get_capability("kortex.connector.driver.list")
        assert descriptor.provider == "connector"
        assert descriptor.required_permissions == ["connector:read"]
        assert descriptor.requires_authentication is True
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_connector_registry_starts_empty_on_production_boot_path() -> None:
    kernel = await build_and_boot_kernel()
    try:
        connector_engine = kernel.get_engine("connector")
        assert isinstance(connector_engine, ConnectorEngine)
        assert connector_engine.list_drivers() == []
    finally:
        await kernel.shutdown()
