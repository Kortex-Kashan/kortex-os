"""
Unit tests for KORTEX Core Container (Dependency Injection).
"""

import pytest

from kortex.core.container import Container
from kortex.core.exceptions import ResourceAlreadyExistsError, ResourceNotFoundError


class DummyService:
    def __init__(self, name: str = "default") -> None:
        self.name = name


def test_container_register_and_resolve_instance() -> None:
    container = Container()
    service = DummyService("auth")
    container.register_instance("auth_service", service)

    assert container.has("auth_service") is True
    resolved = container.resolve("auth_service")
    assert resolved is service
    assert resolved.name == "auth"


def test_container_duplicate_registration_raises_error() -> None:
    container = Container()
    service = DummyService()
    container.register_instance("service", service)

    with pytest.raises(ResourceAlreadyExistsError):
        container.register_instance("service", service)


def test_container_resolve_nonexistent_raises_error() -> None:
    container = Container()
    with pytest.raises(ResourceNotFoundError):
        container.resolve("nonexistent")


def test_container_register_type() -> None:
    container = Container()
    service = DummyService("typed")
    container.register_type(DummyService, service)

    assert container.has_type(DummyService) is True
    resolved = container.resolve_type(DummyService)
    assert resolved is service
    assert resolved.name == "typed"


def test_container_register_factory() -> None:
    container = Container()
    counter = 0

    def factory(c: Container) -> DummyService:
        nonlocal counter
        counter += 1
        return DummyService(f"created_{counter}")

    container.register_factory("lazy_service", factory)
    assert container.has("lazy_service") is True
    assert counter == 0

    resolved1 = container.resolve("lazy_service")
    assert resolved1.name == "created_1"
    assert counter == 1

    # Singleton caching
    resolved2 = container.resolve("lazy_service")
    assert resolved2 is resolved1
    assert counter == 1


def test_container_clear() -> None:
    container = Container()
    container.register_instance("key", DummyService())
    container.register_type(DummyService, DummyService())

    container.clear()
    assert container.has("key") is False
    assert container.has_type(DummyService) is False
