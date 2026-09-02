"""Connector Driver Registry for KORTEX OS Connector Engine.

This module implements ConnectorDriverRegistry, which manages registration, unregistration,
lookup, capability discovery, SemVer version resolution, and thread-safe driver access
in accordance with the Connector Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import re
import threading

from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.exceptions import (
    ConnectorDriverError,
    ConnectorOperationError,
    ConnectorValidationError,
    DriverNotFoundError,
)
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorCapability,
    ConnectorProfile,
    DriverMetadata,
)

# Regular expression for SemVer 2.0.0 validation
SEMVER_REGEX = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


def parse_semver(version_str: str) -> tuple[int, int, int, int, str]:
    """Parse a Semantic Version string into a comparable tuple.

    Args:
        version_str: SemVer 2.0.0 string (e.g. '1.0.0' or '2.1.0-alpha').

    Returns:
        Tuple of (major, minor, patch, is_release, prerelease).

    Raises:
        ConnectorValidationError: If version_str is not valid SemVer format.
    """
    match = SEMVER_REGEX.match(version_str.strip())
    if not match:
        raise ConnectorValidationError(
            f"Invalid semantic version format: '{version_str}'. Must follow SemVer 2.0.0 (MAJOR.MINOR.PATCH)."
        )
    groups = match.groupdict()
    major = int(groups["major"])
    minor = int(groups["minor"])
    patch = int(groups["patch"])
    prerelease = groups["prerelease"]
    is_release = 1 if prerelease is None else 0
    return (major, minor, patch, is_release, prerelease or "")


class MetadataDriverWrapper(BaseConnectorDriver):
    """Internal lightweight driver wrapper used when registering DriverMetadata alone."""

    def __init__(self, metadata_obj: DriverMetadata) -> None:
        self._metadata = metadata_obj

    @property
    def metadata(self) -> DriverMetadata:
        return self._metadata

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        raise ConnectorOperationError("Metadata-only driver registration cannot execute actions.")

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        return False


class ConnectorDriverRegistry:
    """Thread-safe registry for managing BaseConnectorDriver implementations and metadata.

    Responsibilities:
    1. Registering BaseConnectorDriver implementations and DriverMetadata models.
    2. Validating driver contract and metadata completeness prior to registration.
    3. SemVer 2.0.0 resolution for exact and latest version lookup.
    4. Action and fine-grained capability based driver discovery.
    5. Rejection of duplicate driver registrations.
    6. Thread-safe, deterministic, offline-first driver resolution.
    """

    def __init__(self) -> None:
        """Initialize empty in-memory driver catalog and reentrant thread lock."""
        # Map: driver_id -> dict of (version_str -> BaseConnectorDriver)
        self._drivers: dict[str, dict[str, BaseConnectorDriver]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def validate_driver_metadata(metadata: DriverMetadata) -> None:
        """Validate DriverMetadata completeness and SemVer format.

        Args:
            metadata: DriverMetadata instance to validate.

        Raises:
            ConnectorValidationError: If any metadata validation rule is violated.
        """
        if not metadata.driver_id or not metadata.driver_id.strip():
            raise ConnectorValidationError("Missing required metadata field: 'driver_id' cannot be empty.")

        if not metadata.display_name or not metadata.display_name.strip():
            raise ConnectorValidationError("Missing required metadata field: 'display_name' cannot be empty.")

        if not metadata.vendor or not metadata.vendor.strip():
            raise ConnectorValidationError("Missing required metadata field: 'vendor' cannot be empty.")

        if not metadata.author or not metadata.author.strip():
            raise ConnectorValidationError("Missing required metadata field: 'author' cannot be empty.")

        if not metadata.license or not metadata.license.strip():
            raise ConnectorValidationError("Missing required metadata field: 'license' cannot be empty.")

        if not metadata.description or not metadata.description.strip():
            raise ConnectorValidationError("Missing required metadata field: 'description' cannot be empty.")

        # Validate SemVer version format
        parse_semver(metadata.version)

    def register_driver(self, driver: BaseConnectorDriver | DriverMetadata) -> BaseConnectorDriver:
        """Register a BaseConnectorDriver or DriverMetadata instance.

        Args:
            driver: BaseConnectorDriver subclass instance or DriverMetadata.

        Returns:
            The registered BaseConnectorDriver instance.

        Raises:
            ConnectorValidationError: If contract validation fails.
            ConnectorDriverError: If duplicate version registration occurs.
        """
        with self._lock:
            if isinstance(driver, DriverMetadata):
                driver_obj: BaseConnectorDriver = MetadataDriverWrapper(driver)
            elif isinstance(driver, BaseConnectorDriver):
                driver_obj = driver
            else:
                raise ConnectorValidationError(
                    "Invalid driver object: must inherit from BaseConnectorDriver or be DriverMetadata."
                )

            try:
                meta = driver_obj.metadata
            except Exception as err:
                raise ConnectorValidationError(f"Failed to access driver metadata: {err}") from err

            if not isinstance(meta, DriverMetadata):
                raise ConnectorValidationError("Driver metadata property must return a DriverMetadata instance.")

            self.validate_driver_metadata(meta)

            driver_id = meta.driver_id.strip()
            version = meta.version.strip()

            if driver_id in self._drivers and version in self._drivers[driver_id]:
                raise ConnectorDriverError(
                    f"Duplicate driver registration: '{driver_id}' version '{version}' is already registered."
                )

            if driver_id not in self._drivers:
                self._drivers[driver_id] = {}

            self._drivers[driver_id][version] = driver_obj
            return driver_obj

    def unregister_driver(self, driver_id: str, version: str | None = None) -> bool:
        """Unregister a driver or specific driver version from the registry.

        Args:
            driver_id: Canonical driver identifier string.
            version: Optional SemVer string. If None, unregisters all versions.

        Returns:
            True if unregistration succeeded, False if driver_id/version was not found.
        """
        with self._lock:
            driver_id = driver_id.strip()
            if driver_id not in self._drivers or not self._drivers[driver_id]:
                return False

            if version is not None:
                version = version.strip()
                if version not in self._drivers[driver_id]:
                    return False
                del self._drivers[driver_id][version]
                if not self._drivers[driver_id]:
                    del self._drivers[driver_id]
                return True

            del self._drivers[driver_id]
            return True

    def get_driver(
        self,
        identifier_or_capability: str | ConnectorCapability | ConnectorActionType,
        version: str | None = None,
    ) -> BaseConnectorDriver:
        """Retrieve a registered driver by driver_id, action type, or capability.

        Args:
            identifier_or_capability: Driver ID, ConnectorCapability, or ConnectorActionType.
            version: Optional SemVer string.

        Returns:
            BaseConnectorDriver instance.

        Raises:
            DriverNotFoundError: If no matching driver is found.
        """
        with self._lock:
            if isinstance(identifier_or_capability, ConnectorCapability):
                return self.get_driver_by_capability(identifier_or_capability)

            if isinstance(identifier_or_capability, ConnectorActionType):
                return self.get_driver_by_action(identifier_or_capability)

            if isinstance(identifier_or_capability, str):
                target = identifier_or_capability.strip()
                # Check capability match
                try:
                    cap_enum = ConnectorCapability(target)
                    matching_cap = self.find_drivers_by_capability(cap_enum)
                    if matching_cap:
                        return self.get_driver_by_id(matching_cap[0].driver_id)
                except ValueError:
                    pass

                # Check action match
                try:
                    act_enum = ConnectorActionType(target)
                    matching_act = self.find_drivers_for_action(act_enum)
                    if matching_act:
                        return self.get_driver_by_id(matching_act[0].driver_id)
                except ValueError:
                    pass

                return self.get_driver_by_id(target, version=version)

            raise DriverNotFoundError(f"Invalid driver lookup target: {identifier_or_capability}")

    def get_driver_by_id(self, driver_id: str, version: str | None = None) -> BaseConnectorDriver:
        """Retrieve a registered driver by driver_id and optional version.

        Args:
            driver_id: Canonical driver identifier string.
            version: Optional SemVer string. Resolves latest version if None.

        Returns:
            BaseConnectorDriver instance.

        Raises:
            DriverNotFoundError: If driver_id or specified version is not found.
        """
        with self._lock:
            driver_id = driver_id.strip()
            if driver_id not in self._drivers or not self._drivers[driver_id]:
                raise DriverNotFoundError(f"Driver '{driver_id}' not found in registry.")

            if version is not None:
                version = version.strip()
                if version not in self._drivers[driver_id]:
                    raise DriverNotFoundError(f"Driver '{driver_id}' version '{version}' not found in registry.")
                return self._drivers[driver_id][version]

            return self.get_latest_version(driver_id)

    def get_latest_version(self, driver_id: str) -> BaseConnectorDriver:
        """Resolve latest SemVer registered driver for a given driver_id.

        Args:
            driver_id: Canonical driver identifier string.

        Returns:
            BaseConnectorDriver instance.

        Raises:
            DriverNotFoundError: If driver_id has no registered versions.
        """
        with self._lock:
            driver_id = driver_id.strip()
            if driver_id not in self._drivers or not self._drivers[driver_id]:
                raise DriverNotFoundError(f"Driver '{driver_id}' not found in registry.")

            versions = list(self._drivers[driver_id].keys())
            sorted_versions = sorted(versions, key=parse_semver, reverse=True)
            latest_version = sorted_versions[0]
            return self._drivers[driver_id][latest_version]

    def get_driver_by_action(self, action_type: ConnectorActionType | str) -> BaseConnectorDriver:
        """Retrieve latest registered driver advertising support for an action type.

        Args:
            action_type: ConnectorActionType enum or string.

        Returns:
            BaseConnectorDriver instance.

        Raises:
            DriverNotFoundError: If no registered driver supports the action.
        """
        with self._lock:
            matching = self.find_drivers_for_action(action_type)
            if not matching:
                raise DriverNotFoundError(f"No registered driver found supporting action '{action_type}'.")
            return self.get_driver_by_id(matching[0].driver_id)

    def get_driver_by_capability(self, capability: ConnectorCapability | str) -> BaseConnectorDriver:
        """Retrieve latest registered driver advertising support for a capability.

        Args:
            capability: ConnectorCapability enum or string.

        Returns:
            BaseConnectorDriver instance.

        Raises:
            DriverNotFoundError: If no registered driver supports the capability.
        """
        with self._lock:
            matching = self.find_drivers_by_capability(capability)
            if not matching:
                raise DriverNotFoundError(f"No registered driver found supporting capability '{capability}'.")
            return self.get_driver_by_id(matching[0].driver_id)

    def list_drivers(self) -> list[DriverMetadata]:
        """Return list of metadata for all registered drivers (latest version per driver).

        Returns:
            List of DriverMetadata objects.
        """
        with self._lock:
            results: list[DriverMetadata] = []
            for driver_id in self._drivers:
                if self._drivers[driver_id]:
                    latest = self.get_latest_version(driver_id)
                    results.append(latest.metadata)
            return results

    def find_drivers_for_action(self, action_type: ConnectorActionType | str) -> list[DriverMetadata]:
        """Find all registered drivers supporting a specific action type.

        Args:
            action_type: ConnectorActionType enum or string.

        Returns:
            List of DriverMetadata objects.
        """
        with self._lock:
            if isinstance(action_type, ConnectorActionType):
                action_enum = action_type
            elif isinstance(action_type, str):
                try:
                    action_enum = ConnectorActionType(action_type.strip())
                except ValueError:
                    return []
            else:
                return []

            results: list[DriverMetadata] = []
            for driver_id in self._drivers:
                if self._drivers[driver_id]:
                    latest = self.get_latest_version(driver_id)
                    if action_enum in latest.supported_actions:
                        results.append(latest.metadata)
            return results

    def find_drivers_by_capability(self, capability: ConnectorCapability | str) -> list[DriverMetadata]:
        """Find all registered drivers supporting a specific fine-grained capability.

        Args:
            capability: ConnectorCapability enum or string.

        Returns:
            List of DriverMetadata objects.
        """
        with self._lock:
            if isinstance(capability, ConnectorCapability):
                cap_enum = capability
            elif isinstance(capability, str):
                try:
                    cap_enum = ConnectorCapability(capability.strip())
                except ValueError:
                    return []
            else:
                return []

            results: list[DriverMetadata] = []
            for driver_id in self._drivers:
                if self._drivers[driver_id]:
                    latest = self.get_latest_version(driver_id)
                    if cap_enum in latest.metadata.supported_capabilities:
                        results.append(latest.metadata)
            return results

    def clear(self) -> None:
        """Clear all driver entries from the registry."""
        with self._lock:
            self._drivers.clear()


__all__ = ["ConnectorDriverRegistry", "MetadataDriverWrapper", "parse_semver"]
