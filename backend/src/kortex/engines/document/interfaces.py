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
    DocumentLifecycleState,
    DocumentMetadata,
    DocumentOperationProfile,
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


__all__ = [
    "IAdapterPipelineExecutor",
    "IAdapterSandbox",
    "IDocumentAdapterRegistry",
    "IDocumentEngine",
    "IDocumentIntelligenceProvider",
    "IDocumentLifecycleManager",
    "IDocumentOperationProfileManager",
    "IDocumentRecommendationProvider",
    "IDocumentRecoveryProvider",
    "ITemplateBinder",
    "ITemplateLibrary",
]
