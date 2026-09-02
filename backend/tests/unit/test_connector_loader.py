"""Unit tests for ConnectorDriverLoader (Milestone 2).

Target: 100% pass rate, 100% line coverage for loader.py.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.exceptions import DriverLoadError
from kortex.engines.connector.interfaces import IConnectorDriverLoader
from kortex.engines.connector.loader import ConnectorDriverLoader
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorProfile,
    DriverMetadata,
)


class ValidDynamicDriver(BaseConnectorDriver):
    """Concrete driver class for dynamic loader testing."""

    @property
    def metadata(self) -> DriverMetadata:
        return DriverMetadata(
            driver_id="drv-dynamic-valid",
            display_name="Valid Dynamic Driver",
            vendor="KORTEX",
            author="Tester",
            version="1.0.0",
            description="Dynamic driver for loader unit test",
        )

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(request_id=request.request_id, status="SUCCESS")

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


class BrokenInitDriver(BaseConnectorDriver):
    """Driver whose __init__ raises an error."""

    def __init__(self) -> None:
        raise ValueError("Initialization deliberate error")

    @property
    def metadata(self) -> DriverMetadata:
        return DriverMetadata(
            driver_id="drv-broken",
            display_name="Broken Init Driver",
            vendor="KORTEX",
            author="Tester",
            version="1.0.0",
            description="Desc",
        )

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(request_id=request.request_id, status="SUCCESS")

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


class FaultyMetadataLoaderDriver(BaseConnectorDriver):
    """Driver whose metadata property raises an exception."""

    @property
    def metadata(self) -> DriverMetadata:
        raise RuntimeError("Metadata error in loader")

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(request_id=request.request_id, status="SUCCESS")

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


class InvalidMetadataDriver(BaseConnectorDriver):
    """Driver with invalid metadata."""

    @property
    def metadata(self) -> Any:
        return "Not a DriverMetadata object"

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(request_id=request.request_id, status="SUCCESS")

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


class IncompleteMetadataDriver(BaseConnectorDriver):
    """Driver with empty metadata fields."""

    @property
    def metadata(self) -> DriverMetadata:
        return DriverMetadata(
            driver_id="   ",
            display_name="D",
            vendor="V",
            author="A",
            version="1.0.0",
            description="Desc",
        )

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(request_id=request.request_id, status="SUCCESS")

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


class NonDriverClass:
    """Class not inheriting from BaseConnectorDriver."""

    pass


# Attribute in module that is not a class
NON_CLASS_ATTRIBUTE = 42


def test_loader_protocol_compliance() -> None:
    """Test that ConnectorDriverLoader implements IConnectorDriverLoader."""
    loader = ConnectorDriverLoader()
    assert isinstance(loader, IConnectorDriverLoader)


def test_load_valid_driver() -> None:
    """Test dynamically loading a valid driver from this module."""
    loader = ConnectorDriverLoader()
    driver = loader.load_driver(
        module_path="tests.unit.test_connector_loader",
        class_name="ValidDynamicDriver",
    )
    assert isinstance(driver, BaseConnectorDriver)
    assert driver.driver_id == "drv-dynamic-valid"


def test_load_driver_empty_parameters() -> None:
    """Test loading with empty module path or class name."""
    loader = ConnectorDriverLoader()
    with pytest.raises(DriverLoadError):
        loader.load_driver("", "ValidDynamicDriver")

    with pytest.raises(DriverLoadError):
        loader.load_driver("tests.unit.test_connector_loader", "  ")


def test_load_missing_module() -> None:
    """Test loading from a non-existent module path raises DriverLoadError."""
    loader = ConnectorDriverLoader()
    with pytest.raises(DriverLoadError) as exc_info:
        loader.load_driver("kortex.non_existent_module_path", "SomeClass")

    assert "Failed to import driver module" in exc_info.value.message


def test_load_missing_class_in_module() -> None:
    """Test loading a non-existent class from a valid module raises DriverLoadError."""
    loader = ConnectorDriverLoader()
    with pytest.raises(DriverLoadError) as exc_info:
        loader.load_driver("tests.unit.test_connector_loader", "NonExistentClass")

    assert "not found in driver module" in exc_info.value.message


def test_load_non_class_attribute() -> None:
    """Test loading a module attribute that is not a class."""
    loader = ConnectorDriverLoader()
    with pytest.raises(DriverLoadError) as exc_info:
        loader.load_driver("tests.unit.test_connector_loader", "NON_CLASS_ATTRIBUTE")

    assert "is not a class" in exc_info.value.message


def test_load_non_driver_class() -> None:
    """Test loading a class that does not inherit from BaseConnectorDriver."""
    loader = ConnectorDriverLoader()

    # Non-driver class
    with pytest.raises(DriverLoadError) as exc_info:
        loader.load_driver("tests.unit.test_connector_loader", "NonDriverClass")
    assert "must be a concrete subclass of BaseConnectorDriver" in exc_info.value.message

    # BaseConnectorDriver itself
    with pytest.raises(DriverLoadError):
        loader.load_driver("kortex.engines.connector.base_driver", "BaseConnectorDriver")


def test_load_abstract_driver_class() -> None:
    """Test loading an uninstantiable abstract driver subclass."""

    class AbstractDriver(BaseConnectorDriver):
        pass

    current_mod = sys.modules[__name__]
    current_mod.AbstractDriver = AbstractDriver

    loader = ConnectorDriverLoader()
    with pytest.raises(DriverLoadError) as exc_info:
        loader.load_driver("tests.unit.test_connector_loader", "AbstractDriver")

    assert "abstract methods" in exc_info.value.message


def test_load_broken_init_driver() -> None:
    """Test loading a driver whose instantiation raises an error."""
    loader = ConnectorDriverLoader()
    with pytest.raises(DriverLoadError) as exc_info:
        loader.load_driver("tests.unit.test_connector_loader", "BrokenInitDriver")

    assert "Failed to instantiate driver class" in exc_info.value.message


def test_load_faulty_metadata_property_driver() -> None:
    """Test loading a driver whose metadata property raises an exception."""
    loader = ConnectorDriverLoader()
    with pytest.raises(DriverLoadError) as exc_info:
        loader.load_driver("tests.unit.test_connector_loader", "FaultyMetadataLoaderDriver")

    assert "Failed to access metadata" in exc_info.value.message


def test_load_invalid_metadata_driver() -> None:
    """Test loading a driver with non-DriverMetadata object or invalid metadata fields."""
    loader = ConnectorDriverLoader()

    # Non DriverMetadata return
    with pytest.raises(DriverLoadError) as exc_info1:
        loader.load_driver("tests.unit.test_connector_loader", "InvalidMetadataDriver")
    assert "must return a DriverMetadata instance" in exc_info1.value.message

    # Incomplete metadata fields
    with pytest.raises(DriverLoadError) as exc_info2:
        loader.load_driver("tests.unit.test_connector_loader", "IncompleteMetadataDriver")
    assert "metadata validation failed" in exc_info2.value.message


def test_discover_drivers_valid(tmp_path: Path) -> None:
    """Test package driver discovery using module import paths and directory paths."""
    loader = ConnectorDriverLoader()

    # Discover in single module import path
    metadata_list = loader.discover_drivers("tests.unit.test_connector_loader")
    assert isinstance(metadata_list, list)
    driver_ids = [m.driver_id for m in metadata_list]
    assert "drv-dynamic-valid" in driver_ids

    # Discover in package import path
    pkg_metadata = loader.discover_drivers("kortex.engines.connector")
    assert isinstance(pkg_metadata, list)

    # Discover in directory path containing python files
    dummy_dir = tmp_path / "drivers_dir"
    dummy_dir.mkdir()
    (dummy_dir / "sample.py").write_text("# dummy py file")
    (dummy_dir / "__init__.py").write_text("")

    dir_metadata = loader.discover_drivers(str(dummy_dir))
    assert isinstance(dir_metadata, list)


def test_discover_drivers_handles_import_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test discover_drivers gracefully skips submodules that fail to import (lines 173-174)."""
    loader = ConnectorDriverLoader()
    original_import = importlib.import_module

    def mock_import(name: str, package: str | None = None) -> Any:
        if name == "kortex.engines.connector.bad_submodule":
            raise ImportError("Simulated submodule import error")
        return original_import(name, package)

    monkeypatch.setattr(importlib, "import_module", mock_import)
    monkeypatch.setattr(
        "pkgutil.iter_modules",
        lambda path: [(None, "bad_submodule", False), (None, "base_driver", False)],
    )

    results = loader.discover_drivers("kortex.engines.connector")
    assert isinstance(results, list)


def test_discover_drivers_invalid_package() -> None:
    """Test package discovery with invalid package path."""
    loader = ConnectorDriverLoader()
    with pytest.raises(DriverLoadError):
        loader.discover_drivers("   ")

    with pytest.raises(DriverLoadError):
        loader.discover_drivers("completely_invalid_package_path_12345")
