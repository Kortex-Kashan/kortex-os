"""Unit tests for ConnectorDriverRegistry (Milestone 2).

Target: 100% pass rate, 100% line coverage for registry.py.
"""

from __future__ import annotations

import concurrent.futures
from typing import Any

import pytest

from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.exceptions import (
    ConnectorDriverError,
    ConnectorOperationError,
    ConnectorValidationError,
    DriverNotFoundError,
)
from kortex.engines.connector.interfaces import IConnectorDriverRegistry
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorCapability,
    ConnectorProfile,
    DriverMetadata,
)
from kortex.engines.connector.registry import (
    ConnectorDriverRegistry,
    MetadataDriverWrapper,
    parse_semver,
)


class SampleDriver(BaseConnectorDriver):
    """Sample concrete driver implementation for testing."""

    def __init__(self, driver_id: str = "drv-sample", version: str = "1.0.0") -> None:
        self._driver_id = driver_id
        self._version = version

    @property
    def metadata(self) -> DriverMetadata:
        return DriverMetadata(
            driver_id=self._driver_id,
            display_name=f"Sample Driver {self._driver_id}",
            vendor="KORTEX",
            author="Tester",
            version=self._version,
            description="Sample driver plugin for unit tests",
            supported_actions=[ConnectorActionType.SEND, ConnectorActionType.FETCH],
            supported_capabilities=[ConnectorCapability.SEND, ConnectorCapability.TEST_CONNECTION],
        )

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(request_id=request.request_id, status="SUCCESS")

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


class ActionOnlyDriver(BaseConnectorDriver):
    """Driver supporting ActionType.PUSH without advertising Capability.PUSH."""

    @property
    def metadata(self) -> DriverMetadata:
        return DriverMetadata(
            driver_id="drv-action-only",
            display_name="Action Only Driver",
            vendor="KORTEX",
            author="Tester",
            version="1.0.0",
            description="Driver supporting action without capability match",
            supported_actions=[ConnectorActionType.PUSH],
            supported_capabilities=[],
        )

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(request_id=request.request_id, status="SUCCESS")

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


class FaultyMetadataPropertyDriver(BaseConnectorDriver):
    """Driver whose metadata property raises an exception."""

    @property
    def metadata(self) -> DriverMetadata:
        raise RuntimeError("Metadata property access error")

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(request_id=request.request_id, status="SUCCESS")

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


class NonDriverMetadataDriver(BaseConnectorDriver):
    """Driver whose metadata property returns a non-DriverMetadata object."""

    @property
    def metadata(self) -> Any:
        return 12345

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        return ActionResult(request_id=request.request_id, status="SUCCESS")

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return True


def test_parse_semver() -> None:
    """Test parse_semver valid and invalid cases."""
    assert parse_semver("1.0.0") == (1, 0, 0, 1, "")
    assert parse_semver("2.1.3-beta") == (2, 1, 3, 0, "beta")

    with pytest.raises(ConnectorValidationError):
        parse_semver("invalid.version.str")


def test_registry_protocol_compliance() -> None:
    """Test that ConnectorDriverRegistry satisfies IConnectorDriverRegistry protocol."""
    reg = ConnectorDriverRegistry()
    assert isinstance(reg, IConnectorDriverRegistry)


def test_register_and_get_driver() -> None:
    """Test driver registration, retrieval, and metadata listing."""
    reg = ConnectorDriverRegistry()
    drv = SampleDriver("drv-1", "1.0.0")
    registered = reg.register_driver(drv)

    assert registered == drv
    assert reg.get_driver("drv-1") == drv
    assert reg.get_driver_by_id("drv-1", "1.0.0") == drv

    listed = reg.list_drivers()
    assert len(listed) == 1
    assert listed[0].driver_id == "drv-1"


def test_metadata_only_registration() -> None:
    """Test registering DriverMetadata directly creates a MetadataDriverWrapper."""
    reg = ConnectorDriverRegistry()
    meta = DriverMetadata(
        driver_id="meta-only",
        display_name="Metadata Only Driver",
        vendor="KORTEX",
        author="Tester",
        version="1.0.0",
        description="Metadata-only driver registration",
        supported_actions=[ConnectorActionType.PUSH],
    )
    drv = reg.register_driver(meta)
    assert isinstance(drv, MetadataDriverWrapper)
    assert drv.metadata.driver_id == "meta-only"

    import asyncio

    with pytest.raises(ConnectorOperationError):
        asyncio.run(
            drv.execute_action(ActionRequest(request_id="r1", profile_id="p1", action_type=ConnectorActionType.PUSH))
        )

    assert asyncio.run(drv.test_connection(ConnectorProfile(profile_id="p1", name="P", driver_id="meta-only"))) is False


def test_duplicate_registration_rejection() -> None:
    """Test rejecting duplicate driver registration with same ID and version."""
    reg = ConnectorDriverRegistry()
    drv1 = SampleDriver("drv-dup", "1.0.0")
    drv2 = SampleDriver("drv-dup", "1.0.0")

    reg.register_driver(drv1)
    with pytest.raises(ConnectorDriverError):
        reg.register_driver(drv2)


def test_semver_latest_version_resolution() -> None:
    """Test resolving latest SemVer when multiple versions of a driver exist."""
    reg = ConnectorDriverRegistry()
    reg.register_driver(SampleDriver("drv-ver", "1.0.0"))
    reg.register_driver(SampleDriver("drv-ver", "2.1.0"))
    reg.register_driver(SampleDriver("drv-ver", "1.5.0"))

    latest = reg.get_driver("drv-ver")
    assert latest.metadata.version == "2.1.0"
    assert reg.get_driver_by_id("drv-ver", "1.0.0").metadata.version == "1.0.0"


def test_unregister_driver() -> None:
    """Test unregistering all versions or specific version of a driver."""
    reg = ConnectorDriverRegistry()
    reg.register_driver(SampleDriver("drv-unreg", "1.0.0"))
    reg.register_driver(SampleDriver("drv-unreg", "2.0.0"))

    # Unregister missing version returns False
    assert reg.unregister_driver("drv-unreg", "9.9.9") is False

    # Unregister specific version until empty
    assert reg.unregister_driver("drv-unreg", "1.0.0") is True
    with pytest.raises(DriverNotFoundError):
        reg.get_driver_by_id("drv-unreg", "1.0.0")

    assert reg.get_driver("drv-unreg").metadata.version == "2.0.0"

    # Unregister remaining specific version (triggers cleanup)
    assert reg.unregister_driver("drv-unreg", "2.0.0") is True

    # Unregister all versions on fresh driver
    reg.register_driver(SampleDriver("drv-unreg-all", "1.0.0"))
    assert reg.unregister_driver("drv-unreg-all", version=None) is True
    assert reg.unregister_driver("drv-unreg-all") is False

    with pytest.raises(DriverNotFoundError):
        reg.get_driver("drv-unreg-all")


def test_action_and_capability_discovery() -> None:
    """Test finding drivers by action type or fine-grained capability."""
    reg = ConnectorDriverRegistry()
    drv = SampleDriver("drv-disc", "1.0.0")
    reg.register_driver(drv)

    act_drv = ActionOnlyDriver()
    reg.register_driver(act_drv)

    # Action discovery (enum branch and string)
    action_matches = reg.find_drivers_for_action(ConnectorActionType.SEND)
    assert len(action_matches) == 1
    assert action_matches[0].driver_id == "drv-disc"

    str_action_matches = reg.find_drivers_for_action("SEND")
    assert len(str_action_matches) == 1

    assert reg.find_drivers_for_action(ConnectorActionType.RECEIVE) == []
    assert reg.find_drivers_for_action("INVALID") == []
    assert reg.find_drivers_for_action(12345) == []  # type: ignore[arg-type]

    # Capability discovery (enum branch and string)
    cap_matches = reg.find_drivers_by_capability(ConnectorCapability.TEST_CONNECTION)
    assert len(cap_matches) == 1
    assert cap_matches[0].driver_id == "drv-disc"

    str_cap_matches = reg.find_drivers_by_capability("TEST_CONNECTION")
    assert len(str_cap_matches) == 1

    assert reg.find_drivers_by_capability(ConnectorCapability.WEBHOOK) == []
    assert reg.find_drivers_by_capability("INVALID") == []
    assert reg.find_drivers_by_capability(12345) == []  # type: ignore[arg-type]

    # Lookup by action enum or string
    found_by_action = reg.get_driver(ConnectorActionType.SEND)
    assert found_by_action.driver_id == "drv-disc"

    found_by_str_action = reg.get_driver("SEND")
    assert found_by_str_action.driver_id == "drv-disc"

    found_by_cap = reg.get_driver(ConnectorCapability.TEST_CONNECTION)
    assert found_by_cap.driver_id == "drv-disc"

    found_by_str_cap = reg.get_driver("TEST_CONNECTION")
    assert found_by_str_cap.driver_id == "drv-disc"

    # Lookup by string matching action type but NOT matching any capability
    found_action_only = reg.get_driver("PUSH")
    assert found_action_only.driver_id == "drv-action-only"


def test_driver_not_found_errors() -> None:
    """Test DriverNotFoundError on missing driver or action lookup."""
    reg = ConnectorDriverRegistry()
    with pytest.raises(DriverNotFoundError):
        reg.get_driver_by_id("non-existent")

    with pytest.raises(DriverNotFoundError):
        reg.get_latest_version("non-existent")

    with pytest.raises(DriverNotFoundError):
        reg.get_driver_by_action(ConnectorActionType.VERIFY)

    with pytest.raises(DriverNotFoundError):
        reg.get_driver_by_capability(ConnectorCapability.STREAMING)

    with pytest.raises(DriverNotFoundError):
        reg.get_driver(123)  # type: ignore[arg-type]


def test_metadata_validation_rules() -> None:
    """Test metadata validation rules in ConnectorDriverRegistry."""
    reg = ConnectorDriverRegistry()

    # Driver whose metadata property raises exception
    with pytest.raises(ConnectorValidationError):
        reg.register_driver(FaultyMetadataPropertyDriver())

    # Driver whose metadata property returns non-DriverMetadata object
    with pytest.raises(ConnectorValidationError):
        reg.register_driver(NonDriverMetadataDriver())

    # Invalid object
    with pytest.raises(ConnectorValidationError):
        reg.register_driver("not a driver")  # type: ignore[arg-type]

    # Missing driver_id
    meta_bad_id = DriverMetadata(
        driver_id="   ",
        display_name="D",
        vendor="V",
        author="A",
        version="1.0.0",
        description="Desc",
    )
    with pytest.raises(ConnectorValidationError):
        reg.register_driver(meta_bad_id)

    # Missing display_name
    meta_bad_name = DriverMetadata(
        driver_id="id1",
        display_name="  ",
        vendor="V",
        author="A",
        version="1.0.0",
        description="Desc",
    )
    with pytest.raises(ConnectorValidationError):
        reg.register_driver(meta_bad_name)

    # Missing vendor
    meta_bad_vendor = DriverMetadata(
        driver_id="id1",
        display_name="D",
        vendor="  ",
        author="A",
        version="1.0.0",
        description="Desc",
    )
    with pytest.raises(ConnectorValidationError):
        reg.register_driver(meta_bad_vendor)

    # Missing author
    meta_bad_author = DriverMetadata(
        driver_id="id1",
        display_name="D",
        vendor="V",
        author="  ",
        version="1.0.0",
        description="Desc",
    )
    with pytest.raises(ConnectorValidationError):
        reg.register_driver(meta_bad_author)

    # Missing license
    meta_bad_license = DriverMetadata(
        driver_id="id1",
        display_name="D",
        vendor="V",
        author="A",
        version="1.0.0",
        description="Desc",
        license="  ",
    )
    with pytest.raises(ConnectorValidationError):
        reg.register_driver(meta_bad_license)

    # Missing description
    meta_bad_desc = DriverMetadata(
        driver_id="id1",
        display_name="D",
        vendor="V",
        author="A",
        version="1.0.0",
        description="  ",
    )
    with pytest.raises(ConnectorValidationError):
        reg.register_driver(meta_bad_desc)


def test_concurrent_thread_safe_registration() -> None:
    """Test concurrent thread safety when registering drivers."""
    reg = ConnectorDriverRegistry()

    def register_worker(index: int) -> None:
        drv = SampleDriver(f"drv-thread-{index}", "1.0.0")
        reg.register_driver(drv)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(register_worker, i) for i in range(20)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    listed = reg.list_drivers()
    assert len(listed) == 20


def test_clear_registry() -> None:
    """Test clearing the registry."""
    reg = ConnectorDriverRegistry()
    reg.register_driver(SampleDriver("drv-1", "1.0.0"))
    assert len(reg.list_drivers()) == 1

    reg.clear()
    assert len(reg.list_drivers()) == 0
