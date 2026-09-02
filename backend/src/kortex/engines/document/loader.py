"""Dynamic Document Adapter Loader for KORTEX OS Document Engine.

This module implements DocumentAdapterLoader, which discovers concrete BaseDocumentAdapter
subclasses within an in-package adapter module (default: kortex.engines.document.adapters)
and registers them into a DocumentAdapterRegistry, in accordance with Milestone 4 of the
Document Engine Implementation Specification (Version 3.0.0).

Discovery is intentionally scoped to a fixed, developer-authored, in-repository package —
never an arbitrary filesystem path or externally-supplied plugin directory. Marketplace-style
external adapter installation is out of scope for this loader.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.exceptions import DocumentAdapterError

logger = logging.getLogger(__name__)

DEFAULT_ADAPTER_PACKAGE = "kortex.engines.document.adapters"


class DocumentAdapterLoader:
    """Discovers and registers BaseDocumentAdapter subclasses from an in-package adapter module.

    A broken adapter module, a class that fails to instantiate, or a duplicate registration
    never aborts the overall discovery/registration pass — each failure is logged and the
    remaining adapters are still discovered/registered.
    """

    def __init__(self, registry: DocumentAdapterRegistry) -> None:
        """Initialize the loader with the registry discovered adapters will be registered into.

        Args:
            registry: DocumentAdapterRegistry instance to register discovered adapters into.
        """
        self._registry = registry

    def discover_adapters(self, package: str = DEFAULT_ADAPTER_PACKAGE) -> list[type[BaseDocumentAdapter]]:
        """Discover concrete BaseDocumentAdapter subclasses defined within `package`.

        Only classes defined directly in one of the scanned modules are returned — classes
        merely imported/re-exported into a module (e.g. BaseDocumentAdapter itself) and
        abstract subclasses are excluded.

        Args:
            package: Dotted module path of the package to scan.

        Returns:
            List of concrete BaseDocumentAdapter subclasses found.
        """
        discovered: list[type[BaseDocumentAdapter]] = []

        try:
            pkg = importlib.import_module(package)
        except Exception as exc:
            logger.debug("Adapter package '%s' could not be imported: %s", package, exc)
            return discovered

        pkg_path = getattr(pkg, "__path__", None)
        if not pkg_path:
            return discovered

        for module_info in pkgutil.iter_modules(pkg_path, prefix=f"{package}."):
            module_name = module_info.name
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                logger.warning("Skipping adapter module '%s': failed to import: %s", module_name, exc)
                continue

            for _, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ != module_name:
                    continue
                if not issubclass(obj, BaseDocumentAdapter):
                    continue
                if inspect.isabstract(obj):
                    continue
                discovered.append(obj)

        return discovered

    def load_and_register_all(self, package: str = DEFAULT_ADAPTER_PACKAGE) -> list[BaseDocumentAdapter]:
        """Discover, instantiate, and register every adapter found in `package`.

        Duplicate registrations (an adapter_id+version already present in the registry) and
        per-adapter instantiation/registration failures are logged and skipped individually;
        neither aborts the overall pass.

        Args:
            package: Dotted module path of the package to scan.

        Returns:
            List of BaseDocumentAdapter instances newly registered during this call
            (already-registered duplicates are not included).
        """
        registered: list[BaseDocumentAdapter] = []

        for adapter_cls in self.discover_adapters(package=package):
            try:
                instance = adapter_cls()
            except Exception as exc:
                logger.warning(
                    "Skipping adapter class '%s': failed to instantiate: %s",
                    adapter_cls.__name__,
                    exc,
                )
                continue

            try:
                self._registry.register_adapter(instance)
            except DocumentAdapterError as exc:
                logger.debug(
                    "Adapter '%s' not registered by loader (already present or invalid): %s",
                    adapter_cls.__name__,
                    exc,
                )
                continue

            registered.append(instance)

        return registered


__all__ = ["DEFAULT_ADAPTER_PACKAGE", "DocumentAdapterLoader"]
