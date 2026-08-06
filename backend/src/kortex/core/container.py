"""
KORTEX Core Dependency Injection Container.

Provides thread-safe service registration, factory resolution, and inversion-of-control
management for the KORTEX Kernel and System Engines.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Type, TypeVar, cast

from kortex.core.exceptions import ResourceAlreadyExistsError, ResourceNotFoundError

T = TypeVar("T")

logger = logging.getLogger("kortex.core.container")


class Container:
    """Inversion-of-Control (IoC) Dependency Injection Container."""

    def __init__(self) -> None:
        self._instances: Dict[str, Any] = {}
        self._factories: Dict[str, Callable[['Container'], Any]] = {}
        self._types: Dict[Type[Any], Any] = {}

    def register_instance(self, key: str, instance: Any) -> None:
        """Register a singleton instance under a string key.

        Args:
            key: Service identifier string.
            instance: Instantiated object.
        """
        if key in self._instances or key in self._factories:
            raise ResourceAlreadyExistsError(f"Service '{key}' is already registered in DI container.")
        self._instances[key] = instance
        logger.debug("Registered DI singleton instance: '%s'", key)

    def register_type(self, interface: Type[T], instance: T) -> None:
        """Register a singleton instance under its Type/Interface class.

        Args:
            interface: The abstract or concrete class type key.
            instance: Instance satisfying the type.
        """
        self._types[interface] = instance
        logger.debug("Registered DI type binding: %s", interface.__name__)

    def register_factory(self, key: str, factory: Callable[[Container], Any]) -> None:
        """Register a factory function for lazy instantiation.

        Args:
            key: Service identifier string.
            factory: Callable accepting this container and returning the instance.
        """
        if key in self._instances or key in self._factories:
            raise ResourceAlreadyExistsError(f"Service '{key}' is already registered in DI container.")
        self._factories[key] = factory
        logger.debug("Registered DI factory: '%s'", key)

    def resolve(self, key: str) -> Any:
        """Resolve a service instance by key string.

        Args:
            key: Service identifier string.

        Returns:
            The resolved service instance.

        Raises:
            ResourceNotFoundError: If no instance or factory is found for the key.
        """
        if key in self._instances:
            return self._instances[key]

        if key in self._factories:
            instance = self._factories[key](self)
            self._instances[key] = instance  # Cache as singleton once created
            return instance

        raise ResourceNotFoundError(f"Service '{key}' not found in DI container.")

    def resolve_type(self, interface: Type[T]) -> T:
        """Resolve a service instance by type/interface class.

        Args:
            interface: The class type key.

        Returns:
            The bound instance.

        Raises:
            ResourceNotFoundError: If no binding is found for the type.
        """
        if interface in self._types:
            return cast(T, self._types[interface])
        raise ResourceNotFoundError(f"Type binding for '{interface.__name__}' not found in DI container.")

    def has(self, key: str) -> bool:
        """Check if a service key is registered in the container."""
        return key in self._instances or key in self._factories

    def has_type(self, interface: Type[Any]) -> bool:
        """Check if a type binding exists in the container."""
        return interface in self._types

    def clear(self) -> None:
        """Clear all registered instances, factories, and type bindings."""
        self._instances.clear()
        self._factories.clear()
        self._types.clear()
        logger.debug("Cleared DI container bindings.")
