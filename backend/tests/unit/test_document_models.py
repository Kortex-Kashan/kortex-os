"""Unit tests for Document Engine models, exceptions, interfaces, and base adapter (Milestone 1).

Target: 100% pass rate, >= 90% code coverage across Milestone 1 files.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.exceptions import (
    AdapterExecutionError,
    AdapterNotFoundError,
    DocumentAdapterError,
    DocumentEngineError,
    DocumentExtractionError,
    DocumentIngestionError,
    DocumentLifecycleError,
    DocumentOperationError,
    DocumentProfileNotFoundError,
    DocumentRecoveryError,
    DocumentSecurityError,
    DocumentTemplateError,
    DocumentValidationError,
)
from kortex.engines.document.interfaces import (
    IAdapterPipelineExecutor,
    IAdapterSandbox,
    IDocumentAdapterRegistry,
    IDocumentEngine,
    IDocumentIntelligenceProvider,
    IDocumentLifecycleManager,
    IDocumentOperationProfileManager,
    IDocumentParser,
    IDocumentRecommendationProvider,
    IDocumentRecoveryProvider,
    ITemplateBinder,
    ITemplateLibrary,
)
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    AdapterPipelineDefinition,
    AdapterSandboxConfig,
    BindingContext,
    Document,
    DocumentContent,
    DocumentExtractionResult,
    DocumentLifecycleState,
    DocumentMetadata,
    DocumentOperationProfile,
    DocumentOperationType,
    DocumentVersion,
    ExtractionResult,
    OperationRequest,
    OperationResult,
    PipelineExecutionMode,
    PipelineStage,
    PreviewOptions,
    PreviewResult,
    SecurityClassification,
    SecurityMetadata,
    TemplateSchema,
    ValidationReport,
)


# --- Test Enums ---


def test_enums_values() -> None:
    """Verify enum string values."""
    assert DocumentLifecycleState.DRAFT.value == "DRAFT"
    assert DocumentLifecycleState.PUBLISHED.value == "PUBLISHED"
    assert DocumentOperationType.GENERATE.value == "GENERATE"
    assert AdapterCapability.PREVIEW.value == "PREVIEW"
    assert SecurityClassification.CONFIDENTIAL.value == "CONFIDENTIAL"
    assert PipelineExecutionMode.SEQUENTIAL.value == "SEQUENTIAL"


# --- Test Pydantic Models ---


def test_security_metadata_defaults() -> None:
    """Test SecurityMetadata model defaults and immutability."""
    sec = SecurityMetadata()
    assert sec.classification == SecurityClassification.INTERNAL
    assert sec.labels == []
    assert sec.owner_id is None
    assert sec.tenant_id == "default"

    with pytest.raises(ValidationError):
        # Frozen model should not allow mutation
        sec.classification = SecurityClassification.PUBLIC  # type: ignore[misc]


def test_document_metadata_model() -> None:
    """Test DocumentMetadata creation and serialization."""
    metadata = DocumentMetadata(
        document_id="doc-123",
        version_id="ver-456",
        title="Test Invoice",
        author_id="user-789",
        created_at="2026-08-08T00:00:00Z",
    )
    assert metadata.document_id == "doc-123"
    assert metadata.lifecycle_state == DocumentLifecycleState.DRAFT
    assert metadata.is_immutable is False
    assert metadata.security_metadata.classification == SecurityClassification.INTERNAL

    data = metadata.model_dump()
    assert data["document_id"] == "doc-123"
    restored = DocumentMetadata.model_validate(data)
    assert restored == metadata


def test_document_version_model() -> None:
    """Test DocumentVersion model."""
    doc_meta = DocumentMetadata(
        document_id="doc-123",
        version_id="ver-456",
        title="Payslip",
        author_id="hr-user",
        created_at="2026-08-08T00:00:00Z",
    )
    version = DocumentVersion(
        version_id="ver-456",
        document_id="doc-123",
        version_number="1.0.0",
        created_at="2026-08-08T00:00:00Z",
        created_by="hr-user",
        metadata=doc_meta,
    )
    assert version.version_number == "1.0.0"
    assert version.metadata.title == "Payslip"


def test_adapter_metadata_model() -> None:
    """Test AdapterMetadata creation and properties."""
    meta = AdapterMetadata(
        adapter_id="adapter-dummy",
        display_name="Dummy Adapter",
        vendor="KORTEX",
        author="Engineering Team",
        version="1.0.0",
        license="MIT",
        description="Reference dummy adapter plugin",
        supported_capabilities=[AdapterCapability.PREVIEW, AdapterCapability.GENERATE],
        supported_operations=[DocumentOperationType.PREVIEW, DocumentOperationType.GENERATE],
        supports_preview=True,
    )
    assert meta.adapter_id == "adapter-dummy"
    assert AdapterCapability.PREVIEW in meta.supported_capabilities
    assert meta.supports_preview is True


def test_binding_context_model() -> None:
    """Test BindingContext model data encapsulation."""
    context = BindingContext(
        context_id="ctx-100",
        data={"employee_name": "Alice Smith", "net_salary": 5000.0},
        computed_fields={"formatted_salary": "$5,000.00"},
    )
    assert context.context_id == "ctx-100"
    assert context.data["employee_name"] == "Alice Smith"
    assert context.computed_fields["formatted_salary"] == "$5,000.00"


def test_validation_report_model() -> None:
    """Test ValidationReport validation status tracking."""
    report = ValidationReport(
        is_valid=True,
        warnings=["Non-fatal placeholder missing default"],
        computed_fields_resolved=["formatted_salary"],
    )
    assert report.is_valid is True
    assert len(report.errors) == 0
    assert len(report.warnings) == 1


def test_operation_request_and_result() -> None:
    """Test OperationRequest and OperationResult models."""
    ctx = BindingContext(context_id="ctx-1")
    req = OperationRequest(
        request_id="req-123",
        profile_id="profile-payslip",
        binding_context=ctx,
        options={"dpi": 300},
    )
    assert req.request_id == "req-123"
    assert req.profile_id == "profile-payslip"
    assert req.options["dpi"] == 300

    res = OperationResult(
        request_id="req-123",
        status="SUCCESS",
        document_id="doc-123",
        version_id="ver-1",
        output_bytes=b"PDF_OUTPUT_BYTES",
        execution_time_ms=12.5,
    )
    assert res.status == "SUCCESS"
    assert res.output_bytes == b"PDF_OUTPUT_BYTES"
    assert res.execution_time_ms == 12.5


def test_preview_options_and_result() -> None:
    """Test PreviewOptions and PreviewResult models."""
    options = PreviewOptions(page_number=2, width_px=1024, height_px=768)
    assert options.page_number == 2
    assert options.width_px == 1024

    res = PreviewResult(request_id="req-preview-1", image_bytes=b"PNG_BYTES", page_count=5)
    assert res.request_id == "req-preview-1"
    assert res.page_count == 5
    assert res.image_bytes == b"PNG_BYTES"


def test_pipeline_stage_and_definition() -> None:
    """Test PipelineStage and AdapterPipelineDefinition models."""
    stage1 = PipelineStage(
        stage_id="stage-1",
        adapter_id="adapter-pdf",
        required_capability=AdapterCapability.GENERATE,
    )
    stage2 = PipelineStage(
        stage_id="stage-2",
        adapter_id="adapter-sign",
        required_capability=AdapterCapability.DIGITAL_SIGNATURE,
        is_optional=True,
    )
    pipeline = AdapterPipelineDefinition(
        pipeline_id="pipe-100",
        profile_id="profile-100",
        stages=[stage1, stage2],
        execution_mode=PipelineExecutionMode.SEQUENTIAL,
    )
    assert len(pipeline.stages) == 2
    assert pipeline.stages[0].stage_id == "stage-1"
    assert pipeline.stages[1].is_optional is True


def test_document_operation_profile_model() -> None:
    """Test DocumentOperationProfile model."""
    profile = DocumentOperationProfile(
        id="profile-payslip",
        name="Employee Payslip Profile",
        namespace="kortex.hr.payroll",
        version="1.0.0",
        description="Generates monthly payslips",
        business_operation="GENERATE_PAYSLIP",
        permissions=["hr:payroll:read"],
    )
    assert profile.id == "profile-payslip"
    assert profile.namespace == "kortex.hr.payroll"
    assert profile.output_bucket == "documents"


def test_adapter_sandbox_config_model() -> None:
    """Test AdapterSandboxConfig model."""
    cfg = AdapterSandboxConfig(
        permissions=["file:read"],
        allowed_capabilities=[AdapterCapability.PREVIEW],
        timeout_seconds=15,
        memory_limit_mb=256,
    )
    assert cfg.timeout_seconds == 15
    assert cfg.memory_limit_mb == 256
    assert AdapterCapability.PREVIEW in cfg.allowed_capabilities


def test_template_schema_model() -> None:
    """Test TemplateSchema model."""
    schema = TemplateSchema(
        template_id="tmpl-invoice-v1",
        name="Invoice Template v1",
        namespace="kortex.finance.invoice",
        version="1.0.0",
        description="Standard invoice template",
        placeholders=["customer_name", "total_amount"],
        required_fields=["customer_name"],
    )
    assert schema.template_id == "tmpl-invoice-v1"
    assert "customer_name" in schema.required_fields


# --- Test Exceptions ---


def test_exception_hierarchy() -> None:
    """Test Document Engine custom exception hierarchy and properties."""
    err = DocumentEngineError("Base engine error", details={"code": 500})
    assert isinstance(err, Exception)
    assert str(err) == "Base engine error"
    assert err.details["code"] == 500

    op_err = DocumentOperationError("Operation failed")
    assert isinstance(op_err, DocumentEngineError)

    life_err = DocumentLifecycleError("Invalid state transition")
    assert isinstance(life_err, DocumentEngineError)

    tmpl_err = DocumentTemplateError("Template missing")
    assert isinstance(tmpl_err, DocumentEngineError)

    adapter_err = DocumentAdapterError("Adapter failure")
    assert isinstance(adapter_err, DocumentEngineError)

    not_found = AdapterNotFoundError("Adapter not found")
    assert isinstance(not_found, DocumentAdapterError)

    exec_err = AdapterExecutionError("Sandbox timeout")
    assert isinstance(exec_err, DocumentAdapterError)

    prof_err = DocumentProfileNotFoundError("Profile missing")
    assert isinstance(prof_err, DocumentEngineError)

    sec_err = DocumentSecurityError("Permission denied")
    assert isinstance(sec_err, DocumentEngineError)

    rec_err = DocumentRecoveryError("Checkpoint failed")
    assert isinstance(rec_err, DocumentEngineError)


# --- Test BaseDocumentAdapter Concrete Subclass ---


class ConcreteTestAdapter(BaseDocumentAdapter):
    """Concrete dummy implementation of BaseDocumentAdapter for testing."""

    @property
    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            adapter_id="adapter-concrete-test",
            display_name="Concrete Test Adapter",
            vendor="KORTEX",
            author="Unit Tester",
            version="1.0.0",
            license="MIT",
            description="Testing adapter",
            supported_capabilities=[AdapterCapability.PREVIEW, AdapterCapability.GENERATE],
            supported_operations=[DocumentOperationType.PREVIEW, DocumentOperationType.GENERATE],
        )

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        return f"OUTPUT_{operation_type.value}".encode("utf-8")

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


@pytest.mark.asyncio
async def test_base_document_adapter_execution() -> None:
    """Test BaseDocumentAdapter subclass properties and methods."""
    adapter = ConcreteTestAdapter()
    assert adapter.adapter_id == "adapter-concrete-test"
    assert len(adapter.supported_capabilities) == 2
    assert adapter.supports_capability(AdapterCapability.PREVIEW) is True
    assert adapter.supports_capability(AdapterCapability.OCR) is False

    schema = TemplateSchema(
        template_id="tmpl-1",
        name="Test",
        namespace="kortex.test",
        version="1.0.0",
        description="Test",
    )
    assert adapter.validate_schema(schema) is True

    ctx = BindingContext(context_id="ctx-1")
    output = await adapter.execute(DocumentOperationType.GENERATE, ctx, {})
    assert output == b"OUTPUT_GENERATE"


# --- Test Interface Protocols ---


def test_interface_protocols_defined() -> None:
    """Verify runtime checkable protocol interfaces."""
    protocols = [
        IDocumentEngine,
        IDocumentLifecycleManager,
        ITemplateLibrary,
        ITemplateBinder,
        IDocumentAdapterRegistry,
        IAdapterSandbox,
        IDocumentOperationProfileManager,
        IAdapterPipelineExecutor,
        IDocumentIntelligenceProvider,
        IDocumentRecommendationProvider,
        IDocumentRecoveryProvider,
        IDocumentParser,
    ]
    for proto in protocols:
        assert hasattr(proto, "__protocol_attrs__") or hasattr(proto, "_is_protocol")


def test_document_root_model() -> None:
    """Test Document root entity model creation, defaults, immutability, and serialization."""
    doc = Document(
        document_id="doc-root-001",
        tenant_id="tenant-acme",
        title="Acme Corporation Master Agreement",
        document_type="CONTRACT",
        metadata={"category": "legal", "department": "compliance"},
    )
    assert doc.document_id == "doc-root-001"
    assert doc.tenant_id == "tenant-acme"
    assert doc.title == "Acme Corporation Master Agreement"
    assert doc.document_type == "CONTRACT"
    assert doc.current_version_id is None
    assert doc.metadata["category"] == "legal"

    # Immutability verification
    with pytest.raises(ValidationError):
        doc.title = "Modified Title"  # type: ignore[misc]

    # Serialization roundtrip
    dumped = doc.model_dump()
    assert dumped["document_id"] == "doc-root-001"
    assert dumped["tenant_id"] == "tenant-acme"
    restored = Document.model_validate(dumped)
    assert restored == doc


def test_document_content_model() -> None:
    """Test DocumentContent value object creation, defaults, immutability, and checksum verification."""
    content = DocumentContent(
        storage_key="tenant-acme/doc-001/v1.pdf",
        bucket_name="contracts",
        mime_type="application/pdf",
        file_size_bytes=1048576,
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    assert content.storage_key == "tenant-acme/doc-001/v1.pdf"
    assert content.bucket_name == "contracts"
    assert content.mime_type == "application/pdf"
    assert content.file_size_bytes == 1048576
    assert content.sha256_hash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    with pytest.raises(ValidationError):
        content.file_size_bytes = 2000  # type: ignore[misc]


def test_document_version_with_content() -> None:
    """Test DocumentVersion model integrating DocumentContent value object."""
    doc_meta = DocumentMetadata(
        document_id="doc-500",
        version_id="ver-500-1",
        title="Q3 Financial Report",
        author_id="finance_lead",
        created_at="2026-08-14T00:00:00Z",
    )
    content = DocumentContent(
        storage_key="default/doc-500/ver-500-1.pdf",
        bucket_name="documents",
        mime_type="application/pdf",
        file_size_bytes=45200,
        sha256_hash="a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e",
    )
    ver = DocumentVersion(
        version_id="ver-500-1",
        document_id="doc-500",
        version_number="1.0.0",
        created_at="2026-08-14T00:00:00Z",
        created_by="finance_lead",
        metadata=doc_meta,
        content=content,
    )
    assert ver.version_id == "ver-500-1"
    assert ver.content is not None
    assert ver.content.file_size_bytes == 45200
    assert ver.content.mime_type == "application/pdf"

    # Serialization roundtrip
    data = ver.model_dump()
    restored = DocumentVersion.model_validate(data)
    assert restored == ver
    assert restored.content == content


def test_document_extraction_result_and_alias() -> None:
    """Test DocumentExtractionResult model and ExtractionResult alias."""
    extraction = DocumentExtractionResult(
        document_id="doc-700",
        version_id="ver-700-1",
        raw_text="Employee Name: John Doe\nNet Pay: $5,000.00",
        structured_tables=[{"headers": ["Item", "Amount"], "rows": [["Base Salary", "$5,000.00"]]}],
        metadata_fields={"page_count": 1, "author": "HR"},
        extracted_concepts={"entity": "Payslip", "employee_name": "John Doe"},
        confidence_scores={"employee_name": 0.99, "net_pay": 0.98},
        mime_type="application/pdf",
        page_count=1,
        language="en",
    )
    assert extraction.document_id == "doc-700"
    assert extraction.version_id == "ver-700-1"
    assert "John Doe" in extraction.raw_text
    assert len(extraction.structured_tables) == 1
    assert extraction.confidence_scores["employee_name"] == 0.99
    assert extraction.page_count == 1

    # Verify alias
    assert ExtractionResult is DocumentExtractionResult

    # Immutability
    with pytest.raises(ValidationError):
        extraction.page_count = 2  # type: ignore[misc]


def test_additional_exception_subclasses() -> None:
    """Test DocumentValidationError, DocumentExtractionError, and DocumentIngestionError inheritance and safety."""
    val_err = DocumentValidationError("Invalid document MIME signature.", details={"code": "ERR_MIME_MISMATCH"})
    assert isinstance(val_err, DocumentEngineError)
    assert str(val_err) == "Invalid document MIME signature."
    assert val_err.details["code"] == "ERR_MIME_MISMATCH"

    extract_err = DocumentExtractionError("Failed to extract text from corrupted PDF payload.")
    assert isinstance(extract_err, DocumentEngineError)
    assert str(extract_err) == "Failed to extract text from corrupted PDF payload."

    ingest_err = DocumentIngestionError("Document size exceeds tenant quota limit.", details={"quota_mb": 100})
    assert isinstance(ingest_err, DocumentEngineError)
    assert ingest_err.details["quota_mb"] == 100


class DummyMockParser:
    """Mock implementation of IDocumentParser for runtime check verification."""

    def supports_mime_type(self, mime_type: str) -> bool:
        return mime_type == "text/plain"

    async def parse(
        self,
        content: bytes,
        mime_type: str,
        options: dict[str, Any] | None = None,
    ) -> DocumentExtractionResult:
        text = content.decode("utf-8")
        return DocumentExtractionResult(
            document_id="doc-mock",
            raw_text=text,
            mime_type=mime_type,
        )


@pytest.mark.asyncio
async def test_document_parser_protocol_runtime_check() -> None:
    """Verify IDocumentParser runtime protocol checks and async parsing."""
    parser = DummyMockParser()
    assert isinstance(parser, IDocumentParser)
    assert parser.supports_mime_type("text/plain") is True
    assert parser.supports_mime_type("application/pdf") is False

    res = await parser.parse(b"Hello World", "text/plain")
    assert isinstance(res, DocumentExtractionResult)
    assert res.raw_text == "Hello World"
