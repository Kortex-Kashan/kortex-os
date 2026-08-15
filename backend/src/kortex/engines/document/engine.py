"""Document Engine Facade for KORTEX OS.

This module implements DocumentEngine, the public entry point and primary facade
coordinating Document Lifecycle, Template Library, Template Binder, Adapter Registry,
Adapter Pipeline, Adapter Sandbox, Operation Profile Manager, Intelligence, Recovery,
Security, and Diagnostic Telemetry in accordance with Milestone 8 of the Document Engine
Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import time
from typing import Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.document.adapter_pipeline import AdapterPipelineExecutor
from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.adapter_sandbox import AdapterSandbox
from kortex.engines.document.diagnostics import DocumentDiagnostics
from kortex.engines.document.events import (
    DocumentBaseEvent,
    DocumentLifecycleTransitionedEvent,
    DocumentOperationCompletedEvent,
    DocumentOperationFailedEvent,
    DocumentOperationStartedEvent,
)
from kortex.engines.document.exceptions import (
    AdapterNotFoundError,
    DocumentEngineError,
    DocumentOperationError,
)
from kortex.engines.document.intelligence import (
    DefaultDocumentIntelligenceProvider,
    DefaultDocumentRecommendationProvider,
)
from kortex.engines.document.interfaces import (
    IDocumentIntelligenceProvider,
    IDocumentRecommendationProvider,
)
from kortex.engines.document.lifecycle import DocumentLifecycleManager
from kortex.engines.document.loader import DocumentAdapterLoader
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    BindingContext,
    DocumentLifecycleState,
    DocumentMetadata,
    OperationRequest,
    OperationResult,
    PreviewOptions,
    PreviewResult,
    ValidationReport,
)
from kortex.engines.document.operation_profile import DocumentOperationProfileManager
from kortex.engines.document.persistence import DocumentRepository, TemplateRepository
from kortex.engines.document.recovery import DocumentRecoveryManager
from kortex.engines.document.security import DocumentSecurityVerifier, DocumentStorageBinder
from kortex.engines.document.template_binder import TemplateBinder
from kortex.engines.document.template_library import TemplateLibrary
from kortex.engines.storage.interfaces import IDataStore, IEngineDiagnostics


class DocumentEngine(BaseEngine, IEngineDiagnostics):
    """Primary Facade orchestrator for KORTEX OS Document Engine.

    Extends BaseEngine and implements IEngineDiagnostics and IDocumentEngine.
    """

    def __init__(
        self,
        lifecycle_manager: DocumentLifecycleManager | None = None,
        template_library: TemplateLibrary | None = None,
        template_binder: TemplateBinder | None = None,
        adapter_registry: DocumentAdapterRegistry | None = None,
        sandbox: AdapterSandbox | None = None,
        pipeline_executor: AdapterPipelineExecutor | None = None,
        profile_manager: DocumentOperationProfileManager | None = None,
        recovery_manager: DocumentRecoveryManager | None = None,
        security_verifier: DocumentSecurityVerifier | None = None,
        storage_binder: DocumentStorageBinder | None = None,
        intelligence_provider: IDocumentIntelligenceProvider | None = None,
        recommendation_provider: IDocumentRecommendationProvider | None = None,
    ) -> None:
        """Initialize DocumentEngine facade using Dependency Injection."""
        super().__init__()
        self._adapter_registry = (
            adapter_registry if adapter_registry is not None else DocumentAdapterRegistry()
        )
        self._sandbox = (
            sandbox if sandbox is not None else AdapterSandbox(registry=self._adapter_registry)
        )
        self._template_library = (
            template_library if template_library is not None else TemplateLibrary(load_defaults=True)
        )
        self._template_binder = (
            template_binder if template_binder is not None else TemplateBinder()
        )
        self._lifecycle_manager = (
            lifecycle_manager if lifecycle_manager is not None else DocumentLifecycleManager()
        )
        self._data_store: IDataStore | None = None
        self._profile_manager = (
            profile_manager
            if profile_manager is not None
            else DocumentOperationProfileManager(
                template_library=self._template_library,
                adapter_registry=self._adapter_registry,
            )
        )
        self._recovery_manager = (
            recovery_manager if recovery_manager is not None else DocumentRecoveryManager()
        )
        if pipeline_executor is not None:
            self._pipeline_executor = pipeline_executor
            if self._pipeline_executor.recovery_manager is None:
                self._pipeline_executor._recovery_manager = self._recovery_manager
        else:
            self._pipeline_executor = AdapterPipelineExecutor(
                registry=self._adapter_registry,
                sandbox=self._sandbox,
                profile_manager=self._profile_manager,
                recovery_manager=self._recovery_manager,
            )
        self._security_verifier = (
            security_verifier if security_verifier is not None else DocumentSecurityVerifier()
        )
        self._storage_binder = (
            storage_binder if storage_binder is not None else DocumentStorageBinder()
        )
        self._intelligence_provider = (
            intelligence_provider
            if intelligence_provider is not None
            else DefaultDocumentIntelligenceProvider()
        )
        self._recommendation_provider = (
            recommendation_provider
            if recommendation_provider is not None
            else DefaultDocumentRecommendationProvider()
        )

        self._diagnostics = DocumentDiagnostics(
            registry=self._adapter_registry,
            template_library=self._template_library,
            profile_manager=self._profile_manager,
            lifecycle_manager=self._lifecycle_manager,
        )
        self._emitted_events: list[DocumentBaseEvent] = []

    @property
    def name(self) -> str:
        """Return unique engine identifier (BaseEngine abstract property)."""
        return "document"

    @property
    def dependencies(self) -> list[str]:
        """Return engine dependencies for Kernel startup ordering."""
        return ["storage"]

    @property
    def lifecycle_manager(self) -> DocumentLifecycleManager:
        """Return configured DocumentLifecycleManager."""
        return self._lifecycle_manager

    @property
    def template_library(self) -> TemplateLibrary:
        """Return configured TemplateLibrary."""
        return self._template_library

    @property
    def template_binder(self) -> TemplateBinder:
        """Return configured TemplateBinder."""
        return self._template_binder

    @property
    def adapter_registry(self) -> DocumentAdapterRegistry:
        """Return configured DocumentAdapterRegistry."""
        return self._adapter_registry

    @property
    def sandbox(self) -> AdapterSandbox:
        """Return configured AdapterSandbox."""
        return self._sandbox

    @property
    def pipeline_executor(self) -> AdapterPipelineExecutor:
        """Return configured AdapterPipelineExecutor."""
        return self._pipeline_executor

    @property
    def profile_manager(self) -> DocumentOperationProfileManager:
        """Return configured DocumentOperationProfileManager."""
        return self._profile_manager

    @property
    def recovery_manager(self) -> DocumentRecoveryManager:
        """Return configured DocumentRecoveryManager."""
        return self._recovery_manager

    @property
    def security_verifier(self) -> DocumentSecurityVerifier:
        """Return configured DocumentSecurityVerifier."""
        return self._security_verifier

    @property
    def storage_binder(self) -> DocumentStorageBinder:
        """Return configured DocumentStorageBinder."""
        return self._storage_binder

    @property
    def intelligence_provider(self) -> IDocumentIntelligenceProvider:
        """Return configured IDocumentIntelligenceProvider."""
        return self._intelligence_provider

    @property
    def recommendation_provider(self) -> IDocumentRecommendationProvider:
        """Return configured IDocumentRecommendationProvider."""
        return self._recommendation_provider

    @property
    def emitted_events(self) -> list[DocumentBaseEvent]:
        """Return list of immutable system events emitted by the engine."""
        return list(self._emitted_events)

    # -- BaseEngine Lifecycle Implementations ---------------------------------

    async def initialize(self, kernel: Any = None) -> None:
        """Initialize engine resources and register capabilities with Kernel."""
        self._set_state(EngineState.INITIALIZING)

        # Wire Storage Engine relational persistence from Kernel IoC container if registered.
        # Explicit constructor injection (lifecycle_manager with its own repository) always
        # takes priority; this only fills in persistence when none was already configured.
        if kernel is not None and hasattr(kernel, "container"):
            try:
                storage_engine = kernel.container.resolve("engine.storage")
                if storage_engine is not None and hasattr(storage_engine, "data"):
                    if self._data_store is None:
                        self._data_store = storage_engine.data
                    if self._lifecycle_manager.repository is None and self._data_store is not None:
                        self._lifecycle_manager._repository = DocumentRepository(
                            data_store=self._data_store
                        )
                    if self._template_library.repository is None and self._data_store is not None:
                        self._template_library._repository = TemplateRepository(
                            data_store=self._data_store
                        )
                    if self._profile_manager.repository is None and self._data_store is not None:
                        self._profile_manager._repository = DocumentRepository(
                            data_store=self._data_store
                        )
            except Exception:
                self.logger.debug(
                    "StorageEngine not resolved from Kernel container; DocumentLifecycleManager "
                    "remains in standalone in-memory mode."
                )

        # Populate the adapter registry with any discovered in-package reference adapters
        # (e.g. DummyDocumentAdapter). Safe to call even if adapters were already registered
        # via explicit constructor injection or a prior initialize() call — the loader skips
        # duplicates rather than raising, so this never fails engine initialization.
        try:
            DocumentAdapterLoader(registry=self._adapter_registry).load_and_register_all()
        except Exception:
            self.logger.debug(
                "DocumentAdapterLoader failed to discover/register adapters; continuing with "
                "whatever adapters were already registered (if any)."
            )

        if kernel is not None and hasattr(kernel, "register_capability"):
            for cap in self.capabilities():
                handler = None
                if cap == "kortex.document.operation.execute":
                    handler = self.execute_profile
                elif cap == "kortex.document.lifecycle.transition":
                    handler = self.transition_lifecycle
                elif cap == "kortex.document.template.bind":
                    handler = self.bind_template
                elif cap == "kortex.document.preview.generate":
                    handler = self.generate_preview
                elif cap == "kortex.document.adapter.list":
                    handler = self.list_adapters

                try:
                    kernel.register_capability(
                        name=cap,
                        description=f"Document Engine capability: {cap}",
                        provider=self.name,
                        handler=handler,
                    )
                except TypeError:
                    kernel.register_capability(cap, handler or self)

        self._set_state(EngineState.READY)

    async def start(self) -> None:
        """Start active background services, listeners, or loops."""
        self.ensure_state(EngineState.READY, EngineState.STOPPED)
        self._set_state(EngineState.RUNNING)

    async def health_check(self) -> dict[str, Any]:
        """Return diagnostic health information about the engine status."""
        return self._diagnostics.health()

    async def stop(self) -> None:
        """Gracefully shut down active background tasks and release resources."""
        self._set_state(EngineState.STOPPED)

    # -- Common Diagnostics Interface (IEngineDiagnostics) -------------------

    def health(self) -> dict[str, Any]:
        """Return operational health status and diagnostic checks."""
        return self._diagnostics.health()

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and throughput metrics."""
        return self._diagnostics.metrics()

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics and system environment details."""
        return self._diagnostics.diagnostics()

    def status(self) -> str:
        """Return current engine state name string."""
        return self._state.value

    def version(self) -> str:
        """Return semantic version string of the engine."""
        return "1.0.0"

    def capabilities(self) -> list[str]:
        """Return list of capability strings registered by the engine."""
        return self._diagnostics.capabilities()

    # -- Engine Facade Core Operations (IDocumentEngine) --------------------

    async def execute_profile(
        self, profile_id: str, request: OperationRequest
    ) -> OperationResult:
        """Execute a Document Operation Profile via configured Adapter Pipeline (IDocumentEngine protocol)."""
        start_time = time.perf_counter()

        if request is None or not request.request_id:
            raise DocumentOperationError("Invalid OperationRequest: request_id missing.")

        # Emit operation started event
        self._emitted_events.append(
            DocumentOperationStartedEvent(request_id=request.request_id, profile_id=profile_id)
        )

        binding_context = request.binding_context or BindingContext(
            context_id=f"ctx-{request.request_id}"
        )

        # 1. Resolve profile
        profile = await self._profile_manager.get_profile(
            profile_id, tenant_id=binding_context.tenant_id
        )

        # 2. Template Resolution & Binding
        if profile.required_template_id:
            schema = await self._template_library.get_template(
                profile.required_template_id, tenant_id=binding_context.tenant_id
            )
            report = await self._template_binder.bind(schema, binding_context)
            if not report.is_valid:
                exec_ms = (time.perf_counter() - start_time) * 1000.0
                self._diagnostics.record_operation_executed(is_success=False)
                self._emitted_events.append(
                    DocumentOperationFailedEvent(
                        request_id=request.request_id,
                        profile_id=profile_id,
                        errors=report.errors,
                    )
                )
                return OperationResult(
                    request_id=request.request_id,
                    status="FAILED",
                    output_bytes=None,
                    execution_time_ms=exec_ms,
                    errors=report.errors,
                )

        # 3. Pipeline Execution through Sandboxed Adapter Pipeline Executor
        if profile.adapter_pipeline is not None:
            initial_bytes = request.options.get("input_bytes")
            pipeline_res = await self._pipeline_executor.execute_pipeline_definition(
                definition=profile.adapter_pipeline,
                context=binding_context,
                initial_input=initial_bytes,
                request_id=request.request_id,
            )

            exec_ms = (time.perf_counter() - start_time) * 1000.0
            is_completed = pipeline_res.is_success
            self._diagnostics.record_operation_executed(is_success=is_completed)

            if is_completed:
                self._emitted_events.append(
                    DocumentOperationCompletedEvent(
                        request_id=request.request_id,
                        profile_id=profile_id,
                        status="COMPLETED",
                        execution_time_ms=exec_ms,
                    )
                )
            else:
                self._emitted_events.append(
                    DocumentOperationFailedEvent(
                        request_id=request.request_id,
                        profile_id=profile_id,
                        errors=pipeline_res.errors,
                    )
                )

            return OperationResult(
                request_id=request.request_id,
                status="COMPLETED" if is_completed else "FAILED",
                output_bytes=pipeline_res.final_output_bytes,
                execution_time_ms=exec_ms,
                errors=pipeline_res.errors,
            )

        exec_ms = (time.perf_counter() - start_time) * 1000.0
        self._diagnostics.record_operation_executed(is_success=True)
        self._emitted_events.append(
            DocumentOperationCompletedEvent(
                request_id=request.request_id,
                profile_id=profile_id,
                status="COMPLETED",
                execution_time_ms=exec_ms,
            )
        )
        return OperationResult(
            request_id=request.request_id,
            status="COMPLETED",
            output_bytes=b"",
            execution_time_ms=exec_ms,
            errors=[],
        )

    async def transition_lifecycle(
        self,
        document_id: str,
        version_id: str,
        target_state: DocumentLifecycleState,
    ) -> DocumentMetadata:
        """Transition document version to a new lifecycle state (IDocumentEngine protocol)."""
        try:
            prev_meta = await self._lifecycle_manager.get_lineage(document_id)
            from_state = prev_meta[-1].lifecycle_state.value if prev_meta else "UNKNOWN"
        except Exception:
            from_state = "UNKNOWN"

        new_meta = await self._lifecycle_manager.transition_state(
            document_id=document_id,
            version_id=version_id,
            target_state=target_state,
        )

        # Emit lifecycle transitioned event
        self._emitted_events.append(
            DocumentLifecycleTransitionedEvent(
                document_id=document_id,
                version_id=version_id,
                from_state=from_state,
                to_state=target_state.value,
            )
        )
        return new_meta

    async def bind_template(
        self, template_id: str, context: BindingContext
    ) -> ValidationReport:
        """Validate and bind context data against a declarative Template Schema (IDocumentEngine protocol)."""
        schema = await self._template_library.get_template(template_id, tenant_id=context.tenant_id)
        return await self._template_binder.bind(schema, context)

    async def generate_preview(
        self, request_id: str, options: PreviewOptions
    ) -> PreviewResult:
        """Generate a preview thumbnail for a document operation page (IDocumentEngine protocol)."""
        if options is None or not request_id:
            return PreviewResult(
                request_id=request_id or "",
                image_bytes=None,
            )

        try:
            adapter_meta = self._adapter_registry.get_adapter(AdapterCapability.PREVIEW)
            adapter = self._adapter_registry.get_adapter_by_id(adapter_meta.adapter_id)
            context = BindingContext(context_id=f"ctx-prev-{request_id}", data=options.model_dump())

            out_bytes = await self._sandbox.execute_sandboxed(
                adapter_id=adapter.adapter_id,
                operation_type="PREVIEW",
                context=context,
                options={"page_number": options.page_number, "max_width": options.width_px},
            )

            return PreviewResult(
                request_id=request_id,
                image_bytes=out_bytes,
                format=options.format or "PNG",
                page_count=1,
                width_px=options.width_px,
                height_px=options.height_px,
            )
        except (AdapterNotFoundError, DocumentEngineError, Exception):
            return PreviewResult(
                request_id=request_id,
                image_bytes=None,
            )

    def list_adapters(self) -> list[AdapterMetadata]:
        """Return list of metadata objects for all registered document adapters (IDocumentEngine protocol)."""
        return self._adapter_registry.list_adapters()


__all__ = ["DocumentEngine"]
