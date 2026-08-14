"""Unit tests for DocumentAdapterLoader (Milestone 4).

Target: 100% pass rate, >=90% line coverage for loader.py.
"""

from __future__ import annotations

import textwrap

import pytest

from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.adapters.dummy_adapter import ADAPTER_ID, DummyDocumentAdapter
from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.loader import DocumentAdapterLoader


def _write_package(tmp_path, package_name: str, modules: dict[str, str]) -> None:
    """Write a minimal importable package under tmp_path with the given module sources."""
    pkg_dir = tmp_path / package_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    for module_name, source in modules.items():
        (pkg_dir / f"{module_name}.py").write_text(textwrap.dedent(source), encoding="utf-8")


def test_discovers_dummy_adapter_from_real_adapters_package() -> None:
    """Discovery against the real, in-repository adapters package finds DummyDocumentAdapter."""
    loader = DocumentAdapterLoader(registry=DocumentAdapterRegistry())
    discovered = loader.discover_adapters()

    assert DummyDocumentAdapter in discovered


def test_discover_ignores_reexported_and_imported_classes(tmp_path, monkeypatch) -> None:
    """Classes merely imported into a module (not defined there) are excluded from discovery."""
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_package(
        tmp_path,
        "kortex_test_pkg_ignore",
        {
            "reexport_module": """
                from kortex.engines.document.base_adapter import BaseDocumentAdapter
                from kortex.engines.document.adapters.dummy_adapter import DummyDocumentAdapter
                # DummyDocumentAdapter and BaseDocumentAdapter are merely imported here, not
                # defined here, so discovery must not surface them from this module.
            """,
        },
    )

    loader = DocumentAdapterLoader(registry=DocumentAdapterRegistry())
    discovered = loader.discover_adapters(package="kortex_test_pkg_ignore")

    assert BaseDocumentAdapter not in discovered
    assert DummyDocumentAdapter not in discovered
    assert discovered == []


def test_discover_ignores_abstract_subclass_defined_in_scanned_module(tmp_path, monkeypatch) -> None:
    """An abstract (incomplete) BaseDocumentAdapter subclass defined in-package is excluded."""
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_package(
        tmp_path,
        "kortex_test_pkg_abstract",
        {
            "partial_adapter": """
                from kortex.engines.document.base_adapter import BaseDocumentAdapter

                class PartialAdapter(BaseDocumentAdapter):
                    # Does not implement metadata/execute/validate_schema — remains abstract.
                    pass
            """,
        },
    )

    loader = DocumentAdapterLoader(registry=DocumentAdapterRegistry())
    discovered = loader.discover_adapters(package="kortex_test_pkg_abstract")

    assert discovered == []


def test_discover_finds_multiple_valid_adapters(tmp_path, monkeypatch) -> None:
    """Discovery finds every concrete BaseDocumentAdapter subclass across multiple modules."""
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_package(
        tmp_path,
        "kortex_test_pkg_multi",
        {
            "adapter_one": """
                from kortex.engines.document.base_adapter import BaseDocumentAdapter
                from kortex.engines.document.models import AdapterMetadata

                class AdapterOne(BaseDocumentAdapter):
                    @property
                    def metadata(self) -> AdapterMetadata:
                        return AdapterMetadata(
                            adapter_id="test.adapter.one", display_name="One", vendor="V",
                            author="A", version="1.0.0", license="MIT", description="D",
                        )

                    async def execute(self, operation_type, binding_context, options) -> bytes:
                        return b"one"

                    def validate_schema(self, schema) -> bool:
                        return True
            """,
            "adapter_two": """
                from kortex.engines.document.base_adapter import BaseDocumentAdapter
                from kortex.engines.document.models import AdapterMetadata

                class AdapterTwo(BaseDocumentAdapter):
                    @property
                    def metadata(self) -> AdapterMetadata:
                        return AdapterMetadata(
                            adapter_id="test.adapter.two", display_name="Two", vendor="V",
                            author="A", version="1.0.0", license="MIT", description="D",
                        )

                    async def execute(self, operation_type, binding_context, options) -> bytes:
                        return b"two"

                    def validate_schema(self, schema) -> bool:
                        return True
            """,
        },
    )

    loader = DocumentAdapterLoader(registry=DocumentAdapterRegistry())
    discovered = loader.discover_adapters(package="kortex_test_pkg_multi")

    assert len(discovered) == 2
    assert {cls.__name__ for cls in discovered} == {"AdapterOne", "AdapterTwo"}


def test_discover_handles_empty_package_safely(tmp_path, monkeypatch) -> None:
    """A package with no adapter modules yields an empty list without error."""
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_package(tmp_path, "kortex_test_pkg_empty", {})

    loader = DocumentAdapterLoader(registry=DocumentAdapterRegistry())
    discovered = loader.discover_adapters(package="kortex_test_pkg_empty")

    assert discovered == []


def test_discover_handles_non_package_module_safely() -> None:
    """A plain module (no __path__, not a package) yields an empty list, not an exception."""
    loader = DocumentAdapterLoader(registry=DocumentAdapterRegistry())
    discovered = loader.discover_adapters(package="kortex.engines.document.exceptions")

    assert discovered == []


def test_discover_ignores_unrelated_classes_defined_in_scanned_module(tmp_path, monkeypatch) -> None:
    """A class defined in a scanned module that isn't a BaseDocumentAdapter subclass is skipped."""
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_package(
        tmp_path,
        "kortex_test_pkg_unrelated",
        {
            "mixed_module": """
                class UnrelatedHelper:
                    pass
            """,
        },
    )

    loader = DocumentAdapterLoader(registry=DocumentAdapterRegistry())
    discovered = loader.discover_adapters(package="kortex_test_pkg_unrelated")

    assert discovered == []


def test_discover_handles_nonexistent_package_safely() -> None:
    """A package that cannot be imported at all yields an empty list, not an exception."""
    loader = DocumentAdapterLoader(registry=DocumentAdapterRegistry())
    discovered = loader.discover_adapters(package="kortex_test_pkg_does_not_exist_at_all")

    assert discovered == []


def test_discover_skips_broken_module_without_aborting_discovery(tmp_path, monkeypatch) -> None:
    """A module that raises on import is skipped; sibling adapters are still discovered."""
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_package(
        tmp_path,
        "kortex_test_pkg_broken",
        {
            "broken_module": """
                raise RuntimeError("this module is intentionally broken for testing")
            """,
            "good_module": """
                from kortex.engines.document.base_adapter import BaseDocumentAdapter
                from kortex.engines.document.models import AdapterMetadata

                class GoodAdapter(BaseDocumentAdapter):
                    @property
                    def metadata(self) -> AdapterMetadata:
                        return AdapterMetadata(
                            adapter_id="test.adapter.good", display_name="Good", vendor="V",
                            author="A", version="1.0.0", license="MIT", description="D",
                        )

                    async def execute(self, operation_type, binding_context, options) -> bytes:
                        return b"good"

                    def validate_schema(self, schema) -> bool:
                        return True
            """,
        },
    )

    loader = DocumentAdapterLoader(registry=DocumentAdapterRegistry())
    discovered = loader.discover_adapters(package="kortex_test_pkg_broken")

    assert len(discovered) == 1
    assert discovered[0].__name__ == "GoodAdapter"


@pytest.mark.asyncio
async def test_load_and_register_all_registers_dummy_adapter() -> None:
    """load_and_register_all() registers DummyDocumentAdapter into a real registry."""
    registry = DocumentAdapterRegistry()
    loader = DocumentAdapterLoader(registry=registry)

    registered = loader.load_and_register_all()

    assert any(a.adapter_id == ADAPTER_ID for a in registered)
    fetched = registry.get_adapter_by_id(ADAPTER_ID)
    assert fetched.adapter_id == ADAPTER_ID


@pytest.mark.asyncio
async def test_load_and_register_all_is_idempotent_on_duplicate() -> None:
    """Calling load_and_register_all() twice against the same registry does not raise."""
    registry = DocumentAdapterRegistry()
    loader = DocumentAdapterLoader(registry=registry)

    first_pass = loader.load_and_register_all()
    second_pass = loader.load_and_register_all()

    assert any(a.adapter_id == ADAPTER_ID for a in first_pass)
    # Second pass finds the same adapter_id+version already registered, so it is skipped —
    # not re-registered, and no exception is raised.
    assert not any(a.adapter_id == ADAPTER_ID for a in second_pass)
    fetched = registry.get_adapter_by_id(ADAPTER_ID)
    assert fetched.adapter_id == ADAPTER_ID


@pytest.mark.asyncio
async def test_load_and_register_all_does_not_reregister_manually_injected_adapter() -> None:
    """An adapter already registered via explicit construction is not duplicated by the loader."""
    registry = DocumentAdapterRegistry()
    manual_instance = DummyDocumentAdapter()
    registry.register_adapter(manual_instance)

    loader = DocumentAdapterLoader(registry=registry)
    registered = loader.load_and_register_all()

    assert not any(a.adapter_id == ADAPTER_ID for a in registered)
    fetched = registry.get_adapter_by_id(ADAPTER_ID)
    assert fetched is manual_instance


def test_load_and_register_all_skips_class_that_fails_to_instantiate(tmp_path, monkeypatch) -> None:
    """A discovered adapter class that raises on construction is skipped, not fatal."""
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_package(
        tmp_path,
        "kortex_test_pkg_bad_init",
        {
            "bad_init_adapter": """
                from kortex.engines.document.base_adapter import BaseDocumentAdapter
                from kortex.engines.document.models import AdapterMetadata

                class BadInitAdapter(BaseDocumentAdapter):
                    def __init__(self):
                        raise ValueError("intentionally fails to construct")

                    @property
                    def metadata(self) -> AdapterMetadata:
                        return AdapterMetadata(
                            adapter_id="test.adapter.badinit", display_name="Bad", vendor="V",
                            author="A", version="1.0.0", license="MIT", description="D",
                        )

                    async def execute(self, operation_type, binding_context, options) -> bytes:
                        return b"bad"

                    def validate_schema(self, schema) -> bool:
                        return True
            """,
        },
    )

    registry = DocumentAdapterRegistry()
    loader = DocumentAdapterLoader(registry=registry)
    registered = loader.load_and_register_all(package="kortex_test_pkg_bad_init")

    assert registered == []
