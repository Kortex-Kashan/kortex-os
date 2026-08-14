"""Public abstract interfaces and protocol declarations for the KORTEX OS Document Engine.

This module defines all formal Protocol interfaces exposed by the Document Engine core,
enforcing Clean Architecture, Dependency Inversion, and strict type checking.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    BindingContext,
    Document,
    DocumentContent,
    DocumentExtractionResult,
    DocumentLifecycleState,
    DocumentMetadata,
    DocumentOperationProfile,
    DocumentVersion,
    OperationRequest,
    OperationResult,
    PreviewOptions,
    PreviewResult,
    TemplateSchema,
    ValidationReport,
)


@runtime_checkable
class IDocumentEngine(Protocol):
    """Primary facade interface exposed by the Document Engine."""

    async def execute_profile(
        self, profile_id: str, request: OperationRequest
    ) -> OperationResult:
        """Execute a Document Operation Profile via configured Adapter Pipeline."""
        ...

    async def transition_lifecycle(
        self,
        document_id: str,
        version_id: str,
        target_state: DocumentLifecycleState,
    ) -> DocumentMetadata:
        """Transition document version to a new lifecycle state."""
        ...

    async def bind_template(
        self, template_id: str, context: BindingContext
    ) -> ValidationReport:
        """Validate and bind context data against a declarative Template Schema."""
        ...

    async def generate_preview(
        self, request_id: str, options: PreviewOptions
    ) -> PreviewResult:
        """Generate a preview thumbnail for a document operation page."""
        ...

    def list_adapters(self) -> list[AdapterMetadata]:
        """Return list of metadata objects for all registered document adapters."""
        ...


@runtime_checkable
class IDocumentLifecycleManager(Protocol):
    """Interface for managing document lifecycle state machine and version lineage."""

    async def transition_state(
        self,
        document_id: str,
        version_id: str,
        target_state: DocumentLifecycleState,
    ) -> DocumentMetadata:
        """Transition document state adhering to state machine transition rules."""
        ...

    async def get_lineage(self, document_id: str) -> list[DocumentMetadata]:
        """Retrieve complete version lineage chain for a document entity."""
        ...

    async def is_immutable(self, document_id: str, version_id: str) -> bool:
        """Check whether a document version is locked against edits."""
        ...


@runtime_checkable
class ITemplateLibrary(Protocol):
    """Interface for indexing, searching, loading, and managing declarative templates."""

    async def get_template(self, template_id: str) -> TemplateSchema:
        """Retrieve declarative TemplateSchema by template ID."""
        ...

    async def search_templates(
        self, query: str, tags: list[str] | None = None
    ) -> list[TemplateSchema]:
        """Search template library by keyword query and filtering tags."""
        ...

    async def install_template(self, schema: TemplateSchema) -> bool:
        """Install a new declarative template into the local Template Library."""
        ...


@runtime_checkable
class ITemplateBinder(Protocol):
    """Interface for binding context data against declarative Template Schemas."""

    async def bind(
        self, schema: TemplateSchema, context: BindingContext
    ) -> ValidationReport:
        """Validate context data against template placeholders and compute fields."""
        ...


@runtime_checkable
class IDocumentAdapterRegistry(Protocol):
    """Thread-safe registry protocol for registering and looking up document adapters."""

    def register_adapter(self, adapter_metadata: AdapterMetadata) -> None:
        """Register adapter metadata in the registry."""
        ...

    def unregister_adapter(self, adapter_id: str) -> bool:
        """Unregister document adapter by adapter ID."""
        ...

    def get_adapter(self, capability: AdapterCapability) -> AdapterMetadata:
        """Retrieve registered adapter advertising the specified capability."""
        ...

    def list_adapters(self) -> list[AdapterMetadata]:
        """Return metadata for all registered document adapters."""
        ...


@runtime_checkable
class IAdapterSandbox(Protocol):
    """Interface for executing document adapters inside sandboxed environments."""

    async def execute_sandboxed(
        self,
        adapter_id: str,
        operation_type: str,
        context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        """Execute adapter operation in sandboxed process context."""
        ...


@runtime_checkable
class IDocumentOperationProfileManager(Protocol):
    """Interface for registering and resolving Document Operation Profiles."""

    async def get_profile(self, profile_id: str) -> DocumentOperationProfile:
        """Retrieve DocumentOperationProfile by profile ID."""
        ...

    async def register_profile(self, profile: DocumentOperationProfile) -> None:
        """Register a DocumentOperationProfile in the profile catalog."""
        ...


@runtime_checkable
class IAdapterPipelineExecutor(Protocol):
    """Interface for executing multi-stage Adapter Pipelines."""

    async def execute_pipeline(
        self, profile_id: str, request: OperationRequest
    ) -> OperationResult:
        """Coordinate and execute adapter pipeline stages for an operation profile."""
        ...


@runtime_checkable
class IDocumentIntelligenceProvider(Protocol):
    """Interface for declarative document intelligence analysis and concept extraction."""

    async def extract_concepts(
        self, document_id: str, version_id: str
    ) -> dict[str, Any]:
        """Extract semantic concepts and structural relationships from document."""
        ...


@runtime_checkable
class IDocumentRecommendationProvider(Protocol):
    """Interface for AI-driven template, profile, and pipeline recommendations."""

    async def recommend_template(
        self, user_intent: str, data_schema: dict[str, Any]
    ) -> list[str]:
        """Recommend template schema IDs based on user intent and available data."""
        ...

    async def recommend_operation_profile(
        self, business_operation: str, user_context: dict[str, Any]
    ) -> str:
        """Recommend optimal DocumentOperationProfile ID."""
        ...

    async def recommend_adapter_pipeline(
        self, profile_id: str, installed_adapters: list[AdapterMetadata]
    ) -> list[str]:
        """Recommend optimal adapter pipeline stage configuration."""
        ...


@runtime_checkable
class IDocumentRecoveryProvider(Protocol):
    """Interface for document operation checkpointing, retries, and rollback stacks."""

    async def checkpoint(self, request_id: str, stage_id: str, state_data: bytes) -> str:
        """Save operational checkpoint state for recovery."""
        ...

    async def rollback(self, request_id: str) -> bool:
        """Execute rollback stack to clean up failed operation artifacts."""
        ...


@runtime_checkable
class IDocumentParser(Protocol):
    """Protocol for extracting clean text, tables, and structured metadata from document payloads."""

    def supports_mime_type(self, mime_type: str) -> bool:
        """Check if parser supports the given MIME type."""
        ...

    async def parse(
        self,
        content: bytes,
        mime_type: str,
        options: dict[str, Any] | None = None,
    ) -> DocumentExtractionResult:
        """Parse raw content bytes into a structured extraction result."""
        ...


@runtime_checkable
class IDocumentRepository(Protocol):
    """Protocol defining relational persistence operations for Document Engine entities via IDataStore."""

    async def create_document(self, document: Document) -> Document:
        """Persist a new root Document entity."""
        ...

    async def get_document(
        self, document_id: str, tenant_id: str = "default", include_deleted: bool = False
    ) -> Document | None:
        """Retrieve root Document entity by ID and tenant."""
        ...

    async def update_document(self, document: Document) -> Document:
        """Update existing root Document entity attributes."""
        ...

    async def soft_delete_document(self, document_id: str, tenant_id: str = "default") -> bool:
        """Logically soft-delete a document (is_deleted = True)."""
        ...

    async def hard_delete_document(self, document_id: str, tenant_id: str = "default") -> bool:
        """Physically delete document record and cascade delete all child versions."""
        ...

    async def list_documents(
        self,
        tenant_id: str = "default",
        document_type: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Document]:
        """List root documents matching tenant and optional type filter."""
        ...

    async def create_version(
        self, version: DocumentVersion, tenant_id: str = "default"
    ) -> DocumentVersion:
        """Persist an immutable DocumentVersion snapshot."""
        ...

    async def get_version(
        self, document_id: str, version_id: str, tenant_id: str = "default"
    ) -> DocumentVersion | None:
        """Retrieve specific DocumentVersion snapshot."""
        ...

    async def get_latest_version(
        self, document_id: str, tenant_id: str = "default"
    ) -> DocumentVersion | None:
        """Retrieve most recently created version snapshot for a document."""
        ...

    async def list_versions(
        self, document_id: str, tenant_id: str = "default"
    ) -> list[DocumentVersion]:
        """List all version snapshots for a document in creation order."""
        ...

    async def update_version_state(
        self,
        document_id: str,
        version_id: str,
        target_state: DocumentLifecycleState,
        is_immutable: bool,
        published_at: str | None = None,
        tenant_id: str = "default",
    ) -> DocumentVersion:
        """Update lifecycle state and immutability lock for a version snapshot."""
        ...

    async def record_operation_history(
        self,
        request_id: str,
        profile_id: str,
        status: str,
        tenant_id: str = "default",
        document_id: str | None = None,
        version_id: str | None = None,
        user_id: str | None = None,
        execution_time_ms: float = 0.0,
        output_storage_key: str | None = None,
        validation_report: ValidationReport | None = None,
        errors: list[str] | None = None,
    ) -> None:
        """Record sanitized document operation execution history."""
        ...

    async def get_operation_history(
        self, request_id: str, tenant_id: str = "default"
    ) -> dict[str, Any] | None:
        """Retrieve operation execution history entry by request ID."""
        ...

    async def list_operation_history(
        self,
        tenant_id: str = "default",
        profile_id: str | None = None,
        document_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List operation history entries matching criteria."""
        ...

    async def save_operation_profile(
        self, profile: DocumentOperationProfile, tenant_id: str = "default"
    ) -> DocumentOperationProfile:
        """Persist or update a DocumentOperationProfile definition."""
        ...

    async def get_operation_profile(
        self, profile_id: str, version: str | None = None, tenant_id: str = "default"
    ) -> DocumentOperationProfile | None:
        """Retrieve DocumentOperationProfile by profile ID and optional version."""
        ...

    async def list_operation_profiles(
        self,
        tenant_id: str = "default",
        business_operation: str | None = None,
        namespace: str | None = None,
    ) -> list[DocumentOperationProfile]:
        """List registered operation profiles matching criteria."""
        ...

    async def delete_operation_profile(
        self, profile_id: str, version: str, tenant_id: str = "default"
    ) -> bool:
        """Delete an operation profile version record."""
        ...


__all__ = [
    "IAdapterPipelineExecutor",
    "IAdapterSandbox",
    "IDocumentAdapterRegistry",
    "IDocumentEngine",
    "IDocumentIntelligenceProvider",
    "IDocumentLifecycleManager",
    "IDocumentOperationProfileManager",
    "IDocumentParser",
    "IDocumentRecommendationProvider",
    "IDocumentRecoveryProvider",
    "IDocumentRepository",
    "ITemplateBinder",
    "ITemplateLibrary",
]
