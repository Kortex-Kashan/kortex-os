"""Dynamic Connector Driver Loader for KORTEX OS Connector Engine.

This module implements ConnectorDriverLoader, which dynamically discovers, inspects,
validates, and instantiates BaseConnectorDriver plugin classes using standard importlib
in accordance with the Connector Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path

from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.exceptions import DriverLoadError
from kortex.engines.connector.models import DriverMetadata
from kortex.engines.connector.registry import ConnectorDriverRegistry

logger = logging.getLogger("kortex.engines.connector.loader")


class ConnectorDriverLoader:
    """Dynamic module inspector and driver loader for connector driver plugins.

    Responsibilities:
    1. Dynamically importing driver modules via standard importlib.
    2. Validating driver class contracts (subclassing BaseConnectorDriver).
    3. Safe instantiation and metadata completeness checks.
    4. Package discovery and inspection across specified package paths.
    5. Raising DriverLoadError with context on load or validation failures.
    """

    def load_driver(self, module_path: str, class_name: str) -> BaseConnectorDriver:
        """Dynamically load, instantiate, and validate a BaseConnectorDriver class.

        Args:
            module_path: Python import path for the driver module
                (e.g. 'kortex.engines.connector.drivers.dummy_driver').
            class_name: Target class name within the module (e.g. 'DummyConnectorDriver').

        Returns:
            Instantiated BaseConnectorDriver plugin object.

        Raises:
            DriverLoadError: If module cannot be imported, class is invalid, instantiation fails, or metadata is
            incomplete.
        """
        module_path = module_path.strip()
        class_name = class_name.strip()

        if not module_path:
            raise DriverLoadError("Invalid loader parameters: 'module_path' cannot be empty.")

        if not class_name:
            raise DriverLoadError("Invalid loader parameters: 'class_name' cannot be empty.")

        # 1. Import module safely
        try:
            module = importlib.import_module(module_path)
        except Exception as err:
            raise DriverLoadError(
                f"Failed to import driver module '{module_path}': {err}",
                details={"module_path": module_path, "class_name": class_name},
            ) from err

        # 2. Resolve target class attribute
        if not hasattr(module, class_name):
            raise DriverLoadError(
                f"Class '{class_name}' not found in driver module '{module_path}'.",
                details={"module_path": module_path, "class_name": class_name},
            )

        driver_cls = getattr(module, class_name)

        # 3. Validate class type and subclassing contract
        if not inspect.isclass(driver_cls):
            raise DriverLoadError(
                f"Attribute '{class_name}' in module '{module_path}' is not a class.",
                details={"module_path": module_path, "class_name": class_name},
            )

        if not issubclass(driver_cls, BaseConnectorDriver) or driver_cls is BaseConnectorDriver:
            raise DriverLoadError(
                f"Class '{class_name}' in module '{module_path}' must be a concrete subclass of BaseConnectorDriver.",
                details={"module_path": module_path, "class_name": class_name},
            )

        # Check abstract methods
        if getattr(driver_cls, "__abstractmethods__", None):
            abstract_methods = list(driver_cls.__abstractmethods__)
            raise DriverLoadError(
                f"Class '{class_name}' cannot be instantiated because it has abstract methods: {abstract_methods}",
                details={"module_path": module_path, "class_name": class_name},
            )

        # 4. Instantiate driver class
        try:
            driver_instance = driver_cls()
        except Exception as err:
            raise DriverLoadError(
                f"Failed to instantiate driver class '{class_name}' from '{module_path}': {err}",
                details={"module_path": module_path, "class_name": class_name},
            ) from err

        # 5. Access and validate metadata
        try:
            meta = driver_instance.metadata
        except Exception as err:
            raise DriverLoadError(
                f"Failed to access metadata on driver instance '{class_name}': {err}",
                details={"module_path": module_path, "class_name": class_name},
            ) from err

        if not isinstance(meta, DriverMetadata):
            raise DriverLoadError(
                f"Driver instance '{class_name}' metadata property must return a DriverMetadata instance.",
                details={"module_path": module_path, "class_name": class_name},
            )

        try:
            ConnectorDriverRegistry.validate_driver_metadata(meta)
        except Exception as err:
            raise DriverLoadError(
                f"Driver instance '{class_name}' metadata validation failed: {err}",
                details={"module_path": module_path, "class_name": class_name},
            ) from err

        return driver_instance

    def discover_drivers(self, package_path: str) -> list[DriverMetadata]:
        """Discover and inspect driver packages inside a Python package import path or file directory.

        Args:
            package_path: Dotted Python import path (e.g. 'kortex.engines.connector.drivers')
                          or filesystem directory path string.

        Returns:
            List of DriverMetadata objects for all discovered valid driver plugins.

        Raises:
            DriverLoadError: If package_path is invalid or empty.
        """
        package_path = package_path.strip()
        if not package_path:
            raise DriverLoadError("Invalid loader parameters: 'package_path' cannot be empty.")

        discovered_metadata: list[DriverMetadata] = []
        modules_to_inspect: list[str] = []

        # Check if package_path is a dotted import path
        try:
            pkg = importlib.import_module(package_path)
            if hasattr(pkg, "__path__"):
                for _, modname, _ in pkgutil.iter_modules(pkg.__path__):
                    modules_to_inspect.append(f"{package_path}.{modname}")
            else:
                modules_to_inspect.append(package_path)
        except ImportError as exc:
            # Check if package_path is a filesystem directory
            path_obj = Path(package_path)
            if path_obj.exists() and path_obj.is_dir():
                for py_file in path_obj.glob("*.py"):
                    if not py_file.name.startswith("__"):
                        modname = py_file.stem
                        # Not an importable dotted path directly without pythonpath, skip or attempt
                        pass
            else:
                raise DriverLoadError(
                    f"Target package path '{package_path}' could not be imported or resolved.",
                    details={"package_path": package_path},
                ) from exc

        for mod_path in modules_to_inspect:
            try:
                mod = importlib.import_module(mod_path)
            except Exception as exc:
                logger.debug("Skipping unimportable module '%s' during driver discovery: %s", mod_path, exc)
                continue

            for attr_name in dir(mod):
                if attr_name.startswith("_"):
                    continue
                attr = getattr(mod, attr_name, None)
                if (
                    inspect.isclass(attr)
                    and issubclass(attr, BaseConnectorDriver)
                    and attr is not BaseConnectorDriver
                    and not getattr(attr, "__abstractmethods__", None)
                ):
                    try:
                        instance = attr()
                        meta = instance.metadata
                        if isinstance(meta, DriverMetadata):
                            ConnectorDriverRegistry.validate_driver_metadata(meta)
                            discovered_metadata.append(meta)
                    except Exception as exc:
                        logger.debug(
                            "Skipping attribute '%s' in '%s' during driver discovery: %s",
                            attr_name,
                            mod_path,
                            exc,
                        )
                        continue

        return discovered_metadata


__all__ = ["ConnectorDriverLoader"]
