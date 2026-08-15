"""Public abstract interfaces and protocol declarations for the KORTEX OS Document Engine.

This module defines all formal Protocol interfaces exposed by the Document Engine core,
enforcing Clean Architecture, Dependency Inversion, and strict type checking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from kortex.engines.document.base_adapter import BaseDocumentAdapter
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

if TYPE_CHECKING:
    from kortex.engines.document.intelligence import DocumentIntelligenceModel


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
        payload: bytes | None = None,
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

    async def register_adapter(
        self, adapter: BaseDocumentAdapter | AdapterMetadata
    ) -> BaseDocumentAdapter:
        """Register a new document adapter into the Document Adapter Registry."""
        ...

    async def analyze_document_intelligence(
        self,
        document_id: str,
        version_id: str,
        ontology: dict[str, Any] | None = None,
    ) -> "DocumentIntelligenceModel":
        """Trigger intelligence analysis via IDocumentIntelligenceProvider."""
        ...

    async def get_recommendation(self, recommendation_type: str, **kwargs: Any) -> Any:
        """Query AI recommendations via IDocumentRecommendationProvider."""
        ...


@runtime_checkable
class IDocumentLifecycleManager(Protocol):
    """Interface for managing document lifecycle state machine, version lineage, and immutability."""

    def validate_transition(
        self, current_state: DocumentLifecycleState, target_state: DocumentLifecycleState
    ) -> bool:
        """Validate whether a lifecycle transition from current_state to target_state is permitted."""
        ...

    async def transition_state(
        self,
        document_id: str,
        version_id: str,
        target_state: DocumentLifecycleState,
        published_at: str | None = None,
        tenant_id: str = "default",
        payload: bytes | None = None,
    ) -> DocumentMetadata:
        """Transition document state adhering to state machine transition rules."""
        ...

    async def get_version(
        self, document_id: str, version_id: str, tenant_id: str = "default"
    ) -> DocumentMetadata:
        """Retrieve metadata for a specific document version."""
        ...

    async def get_latest_version(
        self, document_id: str, tenant_id: str = "default"
    ) -> DocumentMetadata:
        """Retrieve newest active version metadata for a document entity."""
        ...

    async def get_lineage(
        self, document_id: str, tenant_id: str = "default"
    ) -> list[DocumentMetadata]:
        """Retrieve complete version lineage chain for a document entity."""
        ...

    async def is_immutable(
        self, document_id: str, version_id: str, tenant_id: str = "default"
    ) -> bool:
        """Check whether a document version is locked against edits."""
        ...

    async def create_version(
        self,
        document_id: str | None = None,
        title: str = "Untitled Document",
        author_id: str = "system",
        parent_version_id: str | None = None,
        version_number: str | None = None,
        version_id: str | None = None,
        security_metadata: SecurityMetadata | None = None,
        created_at: str | None = None,
        tenant_id: str = "default",
    ) -> DocumentVersion:
        """Create a new document version snapshot."""
        ...

    async def create_child_version(
        self,
        parent_version_id: str,
        document_id: str | None = None,
        title: str | None = None,
        author_id: str = "system",
        version_number: str | None = None,
        version_id: str | None = None,
        security_metadata: SecurityMetadata | None = None,
        tenant_id: str = "default",
    ) -> DocumentVersion:
        """Create a new child version derived from a parent version."""
        ...


@runtime_checkable
class ITemplateLibrary(Protocol):
    """Interface for indexing, searching, loading, and managing declarative templates."""

    async def get_template(
        self, template_id: str, tenant_id: str | None = None
    ) -> TemplateSchema:
        """Retrieve declarative TemplateSchema by template ID."""
        ...

    async def search_templates(
        self,
        query: str,
        tags: list[str] | None = None,
        tenant_id: str | None = None,
    ) -> list[TemplateSchema]:
        """Search template library by keyword query and filtering tags."""
        ...

    async def install_template(
        self, schema: TemplateSchema, tenant_id: str | None = None
    ) -> bool:
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

    def register_adapter(
        self, adapter: BaseDocumentAdapter | AdapterMetadata
    ) -> BaseDocumentAdapter:
        """Register a document adapter instance, or bare adapter metadata, in the registry."""
        ...

    def unregister_adapter(self, adapter_id: str, version: str | None = None) -> bool:
        """Unregister a document adapter by adapter ID and optional specific version."""
        ...

    def get_adapter(
        self, identifier_or_capability: str | AdapterCapability, version: str | None = None
    ) -> BaseDocumentAdapter:
        """Retrieve a registered document adapter by adapter ID or by advertised capability."""
        ...

    def list_adapters(self) -> list[AdapterMetadata]:
        """Return metadata for all registered document adapters (latest version of each)."""
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

    async def analyze_document(
        self,
        document_id: str,
        version_id: str,
        ontology: dict[str, Any] | None = None,
    ) -> Any:
        """Perform comprehensive deterministic intelligence analysis on a document version."""
        ...

    async def update_intelligence_incrementally(
        self, document_id: str, delta_context: dict[str, Any]
    ) -> dict[str, Any]:
        """Update document intelligence state with delta context changes."""
        ...

    async def extract_knowledge_references(self, document_id: str) -> list[str]:
        """Identify and link entity references to the KORTEX Knowledge Engine."""
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

    async def retry_stage(
        self,
        request_id: str,
        stage_id: str,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
    ) -> bool:
        """Calculate retry backoff and determine if a failed pipeline stage retry attempt is permitted."""
        ...

    async def resume(self, request_id: str) -> Any:
        """Resume pipeline execution from the last valid stage checkpoint."""
        ...

    async def record_failure(
        self,
        request_id: str,
        stage_id: str,
        adapter_id: str,
        error_code: str,
        stack_trace_snippet: str,
    ) -> Any:
        """Record detailed failure context for telemetry and administrative inspection."""
        ...

    async def get_checkpoints(self, request_id: str) -> list[Any]:
        """Return all checkpoints for a request ID."""
        ...

    async def get_failures(self, request_id: str) -> list[Any]:
        """Return all failure metadata records for a request ID."""
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

    async def publish_version(
        self,
        document_id: str,
        version_id: str,
        parent_version_id: str | None = None,
        published_at: str | None = None,
        tenant_id: str = "default",
        sha256_hash: str | None = None,
    ) -> tuple[DocumentVersion, DocumentVersion | None]:
        """Atomically transition a document version to PUBLISHED, supersede its predecessor, and update the document pointer.

        Uses an atomic compare-and-swap (CAS) update on DocumentRecord.current_version_id to guarantee that exactly
        one transaction succeeds in concurrent publication races.
        """
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


@runtime_checkable
class ITemplateRepository(Protocol):
    """Protocol defining relational persistence operations for Template Schemas via IDataStore."""

    async def save_template(
        self, schema: TemplateSchema, tenant_id: str = "default"
    ) -> TemplateSchema:
        """Persist a new declarative TemplateSchema version."""
        ...

    async def get_template(
        self, template_id: str, version: str | None = None, tenant_id: str = "default"
    ) -> TemplateSchema | None:
        """Retrieve a TemplateSchema by template_id and optional version (latest if omitted)."""
        ...

    async def list_templates(
        self, tenant_id: str = "default", namespace: str | None = None
    ) -> list[TemplateSchema]:
        """List persisted template versions matching tenant and optional namespace filter."""
        ...

    async def delete_template(
        self, template_id: str, version: str, tenant_id: str = "default"
    ) -> bool:
        """Delete a specific persisted template version."""
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
    "ITemplateRepository",
]
