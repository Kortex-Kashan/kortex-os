"""Unit tests for DocumentAdapterRegistry (Milestone 5).

Target: 100% pass rate, 100% line coverage for adapter_registry.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from kortex.engines.document.adapter_registry import DocumentAdapterRegistry, MetadataAdapterWrapper
from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.exceptions import AdapterNotFoundError, DocumentAdapterError
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    BindingContext,
    DocumentOperationType,
    TemplateSchema,
)


class DummyPdfAdapter(BaseDocumentAdapter):
    """Dummy concrete implementation of BaseDocumentAdapter for testing."""

    def __init__(self, version: str = "1.0.0", adapter_id: str = "kortex.adapter.pdf") -> None:
        self.execute_called = False
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="PDF Adapter",
            vendor="Kortex",
            author="Dev",
            version=version,
            license="MIT",
            description="Test PDF adapter",
            supported_capabilities=[AdapterCapability.GENERATE, AdapterCapability.CONVERT],
            supported_operations=[DocumentOperationType.GENERATE, DocumentOperationType.CONVERT],
        )

    @property
    def metadata(self) -> AdapterMetadata:
        return self._meta

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        self.execute_called = True
        return b"%PDF-dummy-bytes"

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


class DummyOcrAdapter(BaseDocumentAdapter):
    """Second dummy adapter implementation for capability testing."""

    def __init__(self) -> None:
        self._meta = AdapterMetadata(
            adapter_id="kortex.adapter.ocr",
            display_name="OCR Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Test OCR adapter",
            supported_capabilities=[AdapterCapability.OCR, AdapterCapability.GENERATE],
            supported_operations=[DocumentOperationType.OCR],
        )

    @property
    def metadata(self) -> AdapterMetadata:
        return self._meta

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        return b"OCR Text"

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


class FaultyAdapter(BaseDocumentAdapter):
    """Adapter with faulty metadata property for error testing."""

    @property
    def metadata(self) -> AdapterMetadata:
        raise RuntimeError("Metadata access error!")

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        return b""

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return False


def test_register_valid_adapter() -> None:
    """1. Register valid adapter."""
    registry = DocumentAdapterRegistry()
    adapter = DummyPdfAdapter()

    registered = registry.register_adapter(adapter)
    assert registered is adapter
    assert registered.adapter_id == "kortex.adapter.pdf"


def test_retrieve_adapter_by_adapter_id() -> None:
    """2. Retrieve adapter by adapter_id."""
    registry = DocumentAdapterRegistry()
    adapter = DummyPdfAdapter()
    registry.register_adapter(adapter)

    retrieved = registry.get_adapter_by_id("kortex.adapter.pdf")
    assert retrieved is adapter

    retrieved_gen = registry.get_adapter("kortex.adapter.pdf")
    assert retrieved_gen is adapter


def test_retrieve_exact_adapter_version() -> None:
    """3. Retrieve exact adapter version."""
    registry = DocumentAdapterRegistry()
    v1 = DummyPdfAdapter(version="1.0.0")
    v2 = DummyPdfAdapter(version="2.0.0")
    registry.register_adapter(v1)
    registry.register_adapter(v2)

    retrieved_v1 = registry.get_specific_version("kortex.adapter.pdf", "1.0.0")
    assert retrieved_v1 is v1

    retrieved_v2 = registry.get_adapter("kortex.adapter.pdf", version="2.0.0")
    assert retrieved_v2 is v2


def test_retrieve_latest_adapter_version() -> None:
    """4. Retrieve latest adapter version."""
    registry = DocumentAdapterRegistry()
    v1 = DummyPdfAdapter(version="1.0.0")
    v2 = DummyPdfAdapter(version="1.5.0")
    v3 = DummyPdfAdapter(version="2.1.0")

    registry.register_adapter(v1)
    registry.register_adapter(v2)
    registry.register_adapter(v3)

    latest = registry.get_latest_version("kortex.adapter.pdf")
    assert latest is v3
    assert latest.metadata.version == "2.1.0"


def test_register_multiple_versions() -> None:
    """5. Register multiple versions of the same adapter."""
    registry = DocumentAdapterRegistry()
    v1 = DummyPdfAdapter(version="1.0.0")
    v2 = DummyPdfAdapter(version="1.1.0")

    registry.register_adapter(v1)
    registry.register_adapter(v2)

    all_meta = registry.list_all_adapter_versions()
    assert len(all_meta) == 2
    assert {m.version for m in all_meta} == {"1.0.0", "1.1.0"}


def test_semver_resolution() -> None:
    """6. SemVer resolution for versions out of order."""
    registry = DocumentAdapterRegistry()
    v1 = DummyPdfAdapter(version="1.0.0")
    v3 = DummyPdfAdapter(version="3.0.0")
    v2 = DummyPdfAdapter(version="2.5.0")

    registry.register_adapter(v1)
    registry.register_adapter(v3)
    registry.register_adapter(v2)

    latest = registry.get_latest_version("kortex.adapter.pdf")
    assert latest.metadata.version == "3.0.0"


def test_prerelease_semver_behavior() -> None:
    """7. Prerelease behavior handling."""
    registry = DocumentAdapterRegistry()
    v1 = DummyPdfAdapter(version="1.0.0-alpha")
    v2 = DummyPdfAdapter(version="1.0.0")

    registry.register_adapter(v1)
    registry.register_adapter(v2)

    latest = registry.get_latest_version("kortex.adapter.pdf")
    assert latest.metadata.version == "1.0.0"


def test_duplicate_adapter_version_rejection() -> None:
    """8. Duplicate adapter_id + version rejection."""
    registry = DocumentAdapterRegistry()
    v1 = DummyPdfAdapter(version="1.0.0")
    registry.register_adapter(v1)

    v1_dup = DummyPdfAdapter(version="1.0.0")
    with pytest.raises(DocumentAdapterError, match="Duplicate adapter registration"):
        registry.register_adapter(v1_dup)


def test_invalid_metadata_rejection() -> None:
    """9. Invalid metadata rejection (empty fields or bad version)."""
    registry = DocumentAdapterRegistry()

    # Empty adapter_id
    bad_meta1 = AdapterMetadata(
        adapter_id="",
        display_name="PDF",
        vendor="Kortex",
        author="Dev",
        version="1.0.0",
        license="MIT",
        description="Desc",
    )
    with pytest.raises(DocumentAdapterError, match="adapter_id"):
        registry.register_adapter(bad_meta1)

    # Empty display_name
    bad_meta2 = AdapterMetadata(
        adapter_id="id1",
        display_name="",
        vendor="Kortex",
        author="Dev",
        version="1.0.0",
        license="MIT",
        description="Desc",
    )
    with pytest.raises(DocumentAdapterError, match="display_name"):
        registry.register_adapter(bad_meta2)

    # Empty vendor
    bad_meta3 = AdapterMetadata(
        adapter_id="id1",
        display_name="PDF",
        vendor="",
        author="Dev",
        version="1.0.0",
        license="MIT",
        description="Desc",
    )
    with pytest.raises(DocumentAdapterError, match="vendor"):
        registry.register_adapter(bad_meta3)

    # Empty author
    bad_meta4 = AdapterMetadata(
        adapter_id="id1",
        display_name="PDF",
        vendor="Kortex",
        author="",
        version="1.0.0",
        license="MIT",
        description="Desc",
    )
    with pytest.raises(DocumentAdapterError, match="author"):
        registry.register_adapter(bad_meta4)

    # Empty license
    bad_meta5 = AdapterMetadata(
        adapter_id="id1",
        display_name="PDF",
        vendor="Kortex",
        author="Dev",
        version="1.0.0",
        license="",
        description="Desc",
    )
    with pytest.raises(DocumentAdapterError, match="license"):
        registry.register_adapter(bad_meta5)

    # Empty description
    bad_meta6 = AdapterMetadata(
        adapter_id="id1",
        display_name="PDF",
        vendor="Kortex",
        author="Dev",
        version="1.0.0",
        license="MIT",
        description="",
    )
    with pytest.raises(DocumentAdapterError, match="description"):
        registry.register_adapter(bad_meta6)

    # Bad SemVer version
    bad_meta7 = AdapterMetadata(
        adapter_id="id1",
        display_name="PDF",
        vendor="Kortex",
        author="Dev",
        version="invalid_ver",
        license="MIT",
        description="Desc",
    )
    with pytest.raises(DocumentAdapterError, match="Invalid adapter version"):
        registry.register_adapter(bad_meta7)


def test_invalid_adapter_implementation_rejection() -> None:
    """10. Invalid adapter implementation rejection."""
    registry = DocumentAdapterRegistry()

    with pytest.raises(DocumentAdapterError, match="Invalid adapter object"):
        registry.register_adapter("not_an_adapter")  # type: ignore[arg-type]


def test_missing_metadata_rejection() -> None:
    """11. Missing metadata property rejection."""
    registry = DocumentAdapterRegistry()
    faulty = FaultyAdapter()

    with pytest.raises(DocumentAdapterError, match="Failed to access adapter metadata"):
        registry.register_adapter(faulty)


def test_capability_discovery() -> None:
    """12. Capability discovery using find_by_capability and get_adapter."""
    registry = DocumentAdapterRegistry()
    pdf_adapter = DummyPdfAdapter()
    ocr_adapter = DummyOcrAdapter()
    registry.register_adapter(pdf_adapter)
    registry.register_adapter(ocr_adapter)

    # Find by capability enum
    ocr_matches = registry.find_by_capability(AdapterCapability.OCR)
    assert len(ocr_matches) == 1
    assert ocr_matches[0] is ocr_adapter

    # Find by capability string
    gen_by_str = registry.get_adapter("OCR")
    assert gen_by_str is ocr_adapter

    # Find by operation enum
    ops_matches = registry.find_by_operation(DocumentOperationType.CONVERT)
    assert len(ops_matches) == 1
    assert ops_matches[0] is pdf_adapter


def test_multiple_adapters_sharing_same_capability() -> None:
    """13. Multiple adapters sharing the same capability."""
    registry = DocumentAdapterRegistry()
    pdf_adapter = DummyPdfAdapter()
    ocr_adapter = DummyOcrAdapter()
    registry.register_adapter(pdf_adapter)
    registry.register_adapter(ocr_adapter)

    gen_matches = registry.find_by_capability(AdapterCapability.GENERATE)
    assert len(gen_matches) == 2


def test_unsupported_capability_returns_no_matching_adapter() -> None:
    """14. Unsupported capability returns empty list / raises AdapterNotFoundError."""
    registry = DocumentAdapterRegistry()
    pdf_adapter = DummyPdfAdapter()
    registry.register_adapter(pdf_adapter)

    matches = registry.find_by_capability(AdapterCapability.MACROS)
    assert len(matches) == 0

    with pytest.raises(AdapterNotFoundError, match="No registered adapter supports capability"):
        registry.get_adapter_by_capability(AdapterCapability.MACROS)


def test_adapter_listing() -> None:
    """15. Adapter listing (list_adapters)."""
    registry = DocumentAdapterRegistry()
    pdf_adapter = DummyPdfAdapter()
    ocr_adapter = DummyOcrAdapter()
    registry.register_adapter(pdf_adapter)
    registry.register_adapter(ocr_adapter)

    listed = registry.list_adapters()
    assert len(listed) == 2
    assert {m.adapter_id for m in listed} == {"kortex.adapter.pdf", "kortex.adapter.ocr"}


def test_adapter_metadata_retrieval() -> None:
    """16. Adapter metadata retrieval (get_adapter_metadata)."""
    registry = DocumentAdapterRegistry()
    pdf_adapter = DummyPdfAdapter()
    registry.register_adapter(pdf_adapter)

    meta = registry.get_adapter_metadata("kortex.adapter.pdf")
    assert meta.display_name == "PDF Adapter"
    assert meta.vendor == "Kortex"


def test_unregister_remove_behavior() -> None:
    """17. Unregister/remove behavior for specific version vs all versions."""
    registry = DocumentAdapterRegistry()
    v1 = DummyPdfAdapter(version="1.0.0")
    v2 = DummyPdfAdapter(version="2.0.0")
    registry.register_adapter(v1)
    registry.register_adapter(v2)

    # Unregister specific version
    res_v1 = registry.unregister_adapter("kortex.adapter.pdf", version="1.0.0")
    assert res_v1 is True

    # Remaining version should be 2.0.0
    latest = registry.get_latest_version("kortex.adapter.pdf")
    assert latest.metadata.version == "2.0.0"

    # Remove all remaining versions
    res_all = registry.remove_adapter("kortex.adapter.pdf")
    assert res_all is True

    # Verify adapter is gone
    with pytest.raises(AdapterNotFoundError, match="not found in registry"):
        registry.get_adapter("kortex.adapter.pdf")

    # Unregister non-existent adapter returns False
    assert registry.unregister_adapter("non_existent") is False
    assert registry.unregister_adapter("non_existent", version="1.0.0") is False


def test_immutable_registered_version() -> None:
    """18. Immutable registered version prevents mutation."""
    registry = DocumentAdapterRegistry()
    v1 = DummyPdfAdapter(version="1.0.0")
    registry.register_adapter(v1)

    v1_remod = DummyPdfAdapter(version="1.0.0")
    with pytest.raises(DocumentAdapterError, match="Duplicate adapter registration"):
        registry.register_adapter(v1_remod)


def test_new_version_registration() -> None:
    """19. New version registration for existing adapter ID."""
    registry = DocumentAdapterRegistry()
    v1 = DummyPdfAdapter(version="1.0.0")
    v2 = DummyPdfAdapter(version="2.0.0")

    registry.register_adapter(v1)
    registry.register_adapter(v2)

    latest = registry.get_latest_version("kortex.adapter.pdf")
    assert latest.metadata.version == "2.0.0"


def test_deterministic_resolution() -> None:
    """20. Deterministic resolution of adapter lookup."""
    registry = DocumentAdapterRegistry()
    adapter = DummyPdfAdapter()
    registry.register_adapter(adapter)

    res1 = registry.get_adapter("kortex.adapter.pdf")
    res2 = registry.get_adapter("kortex.adapter.pdf")
    assert res1 is res2


@pytest.mark.asyncio
async def test_security_registration_does_not_execute_logic() -> None:
    """21. Security test proving registration does not execute adapter logic."""
    registry = DocumentAdapterRegistry()
    adapter = DummyPdfAdapter()

    registry.register_adapter(adapter)
    # Verify execute() was never invoked during registration
    assert adapter.execute_called is False


def test_regression_compatibility_with_base_document_adapter() -> None:
    """22. Regression compatibility with BaseDocumentAdapter subclass."""
    registry = DocumentAdapterRegistry()
    adapter = DummyPdfAdapter()

    assert isinstance(adapter, BaseDocumentAdapter)
    registry.register_adapter(adapter)

    meta = registry.get_adapter_metadata(adapter.adapter_id)
    assert meta.adapter_id == "kortex.adapter.pdf"


def test_multiple_independent_adapter_registrations() -> None:
    """23. Multiple independent adapter registrations."""
    registry = DocumentAdapterRegistry()
    pdf = DummyPdfAdapter()
    ocr = DummyOcrAdapter()

    registry.register_adapter(pdf)
    registry.register_adapter(ocr)

    assert len(registry.list_adapters()) == 2


def test_invalid_capability_declaration_handling() -> None:
    """24. Invalid capability string handling returns empty list."""
    registry = DocumentAdapterRegistry()
    pdf = DummyPdfAdapter()
    registry.register_adapter(pdf)

    res = registry.find_by_capability("INVALID_CAPABILITY_STRING")
    assert len(res) == 0

    res_op = registry.find_by_operation("INVALID_OPERATION_STRING")
    assert len(res_op) == 0


def test_empty_registry_behavior() -> None:
    """25. Empty registry behavior handling."""
    registry = DocumentAdapterRegistry()

    assert len(registry.list_adapters()) == 0
    assert len(registry.list_all_adapter_versions()) == 0
    assert len(registry.find_by_capability(AdapterCapability.GENERATE)) == 0

    with pytest.raises(AdapterNotFoundError, match="not found in registry"):
        registry.get_adapter("missing.adapter")

    with pytest.raises(AdapterNotFoundError, match="not found in registry"):
        registry.get_latest_version("missing.adapter")


def test_metadata_only_adapter_wrapper() -> None:
    """Test MetadataAdapterWrapper handling when registering AdapterMetadata directly."""
    registry = DocumentAdapterRegistry()
    meta = AdapterMetadata(
        adapter_id="meta.only.adapter",
        display_name="Meta Only",
        vendor="Kortex",
        author="Dev",
        version="1.0.0",
        license="MIT",
        description="Meta description",
    )

    registered = registry.register_adapter(meta)
    assert isinstance(registered, MetadataAdapterWrapper)
    assert registered.adapter_id == "meta.only.adapter"
    assert registered.validate_schema(TemplateSchema(
        template_id="t1", name="N", namespace="kortex.n", version="1.0.0", description="D"
    )) is True

    # Execute should raise NotImplementedError for metadata-only wrapper
    with pytest.raises(NotImplementedError):
        import asyncio
        asyncio.run(registered.execute(DocumentOperationType.GENERATE, BindingContext(context_id="c1"), {}))


def test_edge_case_validations_and_lookups() -> None:
    """Test additional edge cases to ensure 100% coverage."""
    registry = DocumentAdapterRegistry()
    pdf = DummyPdfAdapter()
    registry.register_adapter(pdf)

    # 1. get_adapter with Enum directly
    by_enum = registry.get_adapter(AdapterCapability.GENERATE)
    assert by_enum is pdf

    # 2. get_adapter with invalid argument type
    with pytest.raises(AdapterNotFoundError, match="Invalid lookup target"):
        registry.get_adapter(12345)  # type: ignore[arg-type]

    # 3. get_adapter_by_id with non-existent version
    with pytest.raises(AdapterNotFoundError, match="version '9.9.9' not found"):
        registry.get_adapter_by_id("kortex.adapter.pdf", version="9.9.9")

    # 4. get_adapter_by_capability with string
    by_cap_str = registry.get_adapter_by_capability("GENERATE")
    assert by_cap_str is pdf

    # 5. unregister non-existent version of existing adapter ID
    assert registry.unregister_adapter("kortex.adapter.pdf", version="9.9.9") is False

    # 6. unregister single version removes adapter entry
    assert registry.unregister_adapter("kortex.adapter.pdf", version="1.0.0") is True
    with pytest.raises(AdapterNotFoundError):
        registry.get_adapter("kortex.adapter.pdf")

    # 7. find_by_capability and find_by_operation with non-enum non-string
    assert len(registry.find_by_capability(123)) == 0  # type: ignore[arg-type]
    assert len(registry.find_by_operation(123)) == 0  # type: ignore[arg-type]


class BadMetadataTypeAdapter(BaseDocumentAdapter):
    """Adapter whose metadata property returns a non-AdapterMetadata object."""

    @property
    def metadata(self) -> Any:
        return "not_an_adapter_metadata_object"

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        return b""

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


def test_bad_metadata_type_rejection() -> None:
    """Test rejection when adapter metadata property returns wrong type."""
    registry = DocumentAdapterRegistry()
    bad_type_adapter = BadMetadataTypeAdapter()

    with pytest.raises(DocumentAdapterError, match="Adapter metadata property must return an AdapterMetadata instance"):
        registry.register_adapter(bad_type_adapter)

