"""Pydantic v2 data models and enums for the KORTEX OS Document Engine.

This module contains the domain data models, enums, metadata wrappers, and configuration
schemas defined in the Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentLifecycleState(str, Enum):
    """Lifecycle state of a document version."""

    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"
    LOGICAL_DELETE = "LOGICAL_DELETE"


class DocumentOperationType(str, Enum):
    """High-level document operation codes."""

    GENERATE = "GENERATE"
    CONVERT = "CONVERT"
    TRANSFORM = "TRANSFORM"
    MERGE = "MERGE"
    SPLIT = "SPLIT"
    EXTRACT = "EXTRACT"
    OCR = "OCR"
    PREVIEW = "PREVIEW"
    PRINT = "PRINT"
    WATERMARK = "WATERMARK"
    SIGN = "SIGN"


class AdapterCapability(str, Enum):
    """Fine-grained adapter capabilities advertised by document adapters."""

    PREVIEW = "PREVIEW"
    GENERATE = "GENERATE"
    CONVERT = "CONVERT"
    TRANSFORM = "TRANSFORM"
    MERGE = "MERGE"
    SPLIT = "SPLIT"
    EXTRACT = "EXTRACT"
    OCR = "OCR"
    CHARTS = "CHARTS"
    PIVOT_TABLES = "PIVOT_TABLES"
    MACROS = "MACROS"
    DIGITAL_SIGNATURE = "DIGITAL_SIGNATURE"
    QR_CODE = "QR_CODE"
    BARCODE = "BARCODE"
    COMPRESSION = "COMPRESSION"
    ENCRYPTION = "ENCRYPTION"
    VALIDATION = "VALIDATION"
    PRINTING = "PRINTING"


class SecurityClassification(str, Enum):
    """Security classification levels for multi-tenant document protection."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class PipelineExecutionMode(str, Enum):
    """Execution modes for adapter pipelines."""

    SEQUENTIAL = "SEQUENTIAL"
    CONDITIONAL = "CONDITIONAL"
    PARALLEL_PREP = "PARALLEL_PREP"


class SecurityMetadata(BaseModel):
    """Security classification and label metadata attached to document assets."""

    model_config = ConfigDict(frozen=True)

    classification: SecurityClassification = SecurityClassification.INTERNAL
    labels: list[str] = Field(default_factory=list)
    owner_id: str | None = None
    tenant_id: str = "default"


class DocumentMetadata(BaseModel):
    """Descriptive metadata and storage references for a document version."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    parent_version_id: str | None = None
    lifecycle_state: DocumentLifecycleState = DocumentLifecycleState.DRAFT
    lineage_path: list[str] = Field(default_factory=list)
    title: str
    author_id: str
    is_immutable: bool = False
    security_metadata: SecurityMetadata = Field(default_factory=SecurityMetadata)
    file_size_bytes: int = 0
    sha256_hash: str | None = None
    storage_key: str | None = None
    bucket_name: str | None = None
    created_at: str
    published_at: str | None = None


class DocumentVersion(BaseModel):
    """Immutable document version snapshot in a version chain."""

    model_config = ConfigDict(frozen=True)

    version_id: str
    document_id: str
    parent_version_id: str | None = None
    version_number: str
    created_at: str
    created_by: str
    is_immutable: bool = False
    metadata: DocumentMetadata


class AdapterMetadata(BaseModel):
    """Immutable, Marketplace-ready metadata for a Document Adapter plugin."""

    model_config = ConfigDict(frozen=True)

    adapter_id: str
    display_name: str
    vendor: str
    author: str
    version: str
    license: str
    description: str
    homepage: str | None = None
    supported_capabilities: list[AdapterCapability] = Field(default_factory=list)
    supported_operations: list[DocumentOperationType] = Field(default_factory=list)
    supports_preview: bool = False
    supports_streaming: bool = False
    supports_macros: bool = False
    supports_security: bool = False
    supports_versioning: bool = False


class BindingContext(BaseModel):
    """Context payload passed to template binders and adapter execution stages."""

    model_config = ConfigDict(frozen=True)

    context_id: str
    tenant_id: str = "default"
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    computed_fields: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    """Report detailing template binding validation outcomes."""

    model_config = ConfigDict(frozen=True)

    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_placeholders: list[str] = Field(default_factory=list)
    type_mismatches: list[str] = Field(default_factory=list)
    computed_fields_resolved: list[str] = Field(default_factory=list)


class OperationRequest(BaseModel):
    """Request payload for executing a Document Operation Profile."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    profile_id: str
    tenant_id: str = "default"
    binding_context: BindingContext = Field(default_factory=BindingContext)
    options: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = None


class OperationResult(BaseModel):
    """Result payload returned by document operation execution."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    status: str
    document_id: str | None = None
    version_id: str | None = None
    output_bytes: bytes | None = None
    storage_key: str | None = None
    execution_time_ms: float = 0.0
    validation_report: ValidationReport | None = None
    errors: list[str] = Field(default_factory=list)


class PreviewOptions(BaseModel):
    """Configuration options for page preview generation."""

    model_config = ConfigDict(frozen=True)

    page_number: int = 1
    width_px: int = 800
    height_px: int = 600
    format: str = "PNG"
    dpi: int = 150


class PreviewResult(BaseModel):
    """Result payload returned by page preview stub generation."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    image_bytes: bytes | None = None
    storage_key: str | None = None
    page_count: int = 1
    width_px: int = 800
    height_px: int = 600
    format: str = "PNG"


class PipelineStage(BaseModel):
    """Specification of an individual execution stage within an Adapter Pipeline."""

    model_config = ConfigDict(frozen=True)

    stage_id: str
    adapter_id: str
    required_capability: AdapterCapability
    execution_condition: str | None = None
    is_optional: bool = False
    stage_options: dict[str, Any] = Field(default_factory=dict)


class AdapterPipelineDefinition(BaseModel):
    """Definition of a multi-stage Adapter Pipeline."""

    model_config = ConfigDict(frozen=True)

    pipeline_id: str
    profile_id: str
    stages: list[PipelineStage] = Field(default_factory=list)
    execution_mode: PipelineExecutionMode = PipelineExecutionMode.SEQUENTIAL
    allow_fallback: bool = False


class DocumentOperationProfile(BaseModel):
    """Executable technology-independent profile for a business document operation."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    namespace: str
    version: str
    description: str
    business_operation: str
    required_template_id: str | None = None
    adapter_pipeline: AdapterPipelineDefinition | None = None
    permissions: list[str] = Field(default_factory=list)
    output_bucket: str = "documents"


class AdapterSandboxConfig(BaseModel):
    """Sandbox execution configuration for document adapter plugins."""

    model_config = ConfigDict(frozen=True)

    permissions: list[str] = Field(default_factory=list)
    allowed_capabilities: list[AdapterCapability] = Field(default_factory=list)
    temporary_workspace: str = "/tmp/sandbox"
    timeout_seconds: int = 30
    memory_limit_mb: int = 512


class TemplateSchema(BaseModel):
    """Declarative schema for a reusable business document template."""

    model_config = ConfigDict(frozen=True)

    template_id: str
    name: str
    namespace: str
    version: str
    description: str
    placeholders: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    schema_definition: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AdapterCapability",
    "AdapterMetadata",
    "AdapterPipelineDefinition",
    "AdapterSandboxConfig",
    "BindingContext",
    "DocumentLifecycleState",
    "DocumentMetadata",
    "DocumentOperationProfile",
    "DocumentOperationType",
    "DocumentVersion",
    "OperationRequest",
    "OperationResult",
    "PipelineExecutionMode",
    "PipelineStage",
    "PreviewOptions",
    "PreviewResult",
    "SecurityClassification",
    "SecurityMetadata",
    "TemplateSchema",
    "ValidationReport",
]
