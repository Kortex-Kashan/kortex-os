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


class DocumentContent(BaseModel):
    """Value object encapsulating storage coordinates and checksum of document binary content."""

    model_config = ConfigDict(frozen=True)

    storage_key: str
    bucket_name: str = "documents"
    mime_type: str = "application/octet-stream"
    file_size_bytes: int = 0
    sha256_hash: str | None = None


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
    content: DocumentContent | None = None


class Document(BaseModel):
    """Canonical root aggregate domain model representing a logical document entity."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    tenant_id: str = "default"
    current_version_id: str | None = None
    title: str = "Untitled Document"
    document_type: str = "GENERIC"
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentExtractionResult(BaseModel):
    """Structured extraction result representing text, tables, and domain concepts."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str | None = None
    raw_text: str = ""
    structured_tables: list[dict[str, Any]] = Field(default_factory=list)
    metadata_fields: dict[str, Any] = Field(default_factory=dict)
    extracted_concepts: dict[str, Any] = Field(default_factory=dict)
    confidence_scores: dict[str, float] = Field(default_factory=dict)
    mime_type: str = "text/plain"
    page_count: int = 1
    language: str | None = None


ExtractionResult = DocumentExtractionResult


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


# -- SQLAlchemy ORM Models for IDataStore Persistence -------------------------

import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel as SQLAlchemyBaseModel


class DocumentRecord(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model representing the logical root document aggregate."""

    __tablename__ = "documents"

    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="default")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False, default="GENERIC")
    current_version_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class DocumentVersionRecord(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model representing an immutable document revision snapshot."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id", "version_number", name="uq_tenant_doc_ver_num"),
    )

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="default")
    parent_version_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    version_number: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", index=True)
    is_immutable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    author_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # IObjectStore Content Coordinates (Zero binary payload in database)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bucket_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default="documents")
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    # Security Metadata
    security_classification: Mapped[str] = mapped_column(String(32), nullable=False, default="INTERNAL")
    security_labels_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    security_owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Lineage & Timestamp Metadata
    lineage_path_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentOperationHistoryRecord(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model for auditing document pipeline executions."""

    __tablename__ = "document_operation_history"

    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="default")
    profile_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    version_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_time_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    output_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    validation_report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    errors_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class DocumentOperationProfileRecord(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model for persisting declarative document operation profiles."""

    __tablename__ = "document_operation_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "profile_id", "version", name="uq_tenant_profile_version"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="default")
    profile_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    namespace: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    business_operation: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    required_template_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_bucket: Mapped[str] = mapped_column(String(128), default="documents", nullable=False)
    pipeline_definition_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions_json: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "AdapterCapability",
    "AdapterMetadata",
    "AdapterPipelineDefinition",
    "AdapterSandboxConfig",
    "BindingContext",
    "Document",
    "DocumentContent",
    "DocumentExtractionResult",
    "DocumentLifecycleState",
    "DocumentMetadata",
    "DocumentOperationHistoryRecord",
    "DocumentOperationProfile",
    "DocumentOperationProfileRecord",
    "DocumentOperationType",
    "DocumentRecord",
    "DocumentVersion",
    "DocumentVersionRecord",
    "ExtractionResult",
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
