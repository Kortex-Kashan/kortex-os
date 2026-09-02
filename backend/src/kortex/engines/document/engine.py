"""Document Engine Facade for KORTEX OS.

This module implements DocumentEngine, the public entry point and primary facade
coordinating Document Lifecycle, Template Library, Template Binder, Adapter Registry,
Adapter Pipeline, Adapter Sandbox, Operation Profile Manager, Intelligence, Recovery,
Security, and Diagnostic Telemetry in accordance with Milestone 8 of the Document Engine
Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.document.adapter_pipeline import AdapterPipelineExecutor
from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.adapter_sandbox import AdapterSandbox
from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.diagnostics import DocumentDiagnostics
from kortex.engines.document.events import (
    DocumentAdapterRegisteredEvent,
    DocumentArchivedEvent,
    DocumentBaseEvent,
    DocumentIntelligenceUpdatedEvent,
    DocumentLifecycleTransitionedEvent,
    DocumentOperationCompletedEvent,
    DocumentOperationFailedEvent,
    DocumentOperationStartedEvent,
    DocumentPublishedEvent,
    DocumentSupersededEvent,
)
from kortex.engines.document.exceptions import (
    AdapterNotFoundError,
    DocumentEngineError,
    DocumentOperationError,
)
from kortex.engines.document.intelligence import (
    DefaultDocumentIntelligenceProvider,
    DefaultDocumentRecommendationProvider,
    DocumentIntelligenceModel,
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
    DocumentOperationProfile,
    OperationRequest,
    OperationResult,
    PreviewOptions,
    PreviewResult,
    TemplateSchema,
    ValidationReport,
)
from kortex.engines.document.operation_profile import DocumentOperationProfileManager
from kortex.engines.document.persistence import DocumentRepository, TemplateRepository
from kortex.engines.document.recovery import DocumentRecoveryManager
from kortex.engines.document.security import DocumentSecurityVerifier, DocumentStorageBinder
from kortex.engines.document.template_binder import TemplateBinder
from kortex.engines.document.template_library import TemplateLibrary
from kortex.engines.security.models import SecurityPrincipal
from kortex.engines.storage.interfaces import IDataStore, IEngineDiagnostics

# RBAC permission requirements per Document Engine capability, keyed by the
# same capability names returned by `DocumentDiagnostics.capabilities()`.
_DOCUMENT_CAPABILITY_PERMISSIONS: dict[str, list[str]] = {
    "kortex.document.operation.execute": ["document:execute"],
    "kortex.document.lifecycle.transition": ["document:write"],
    "kortex.document.template.bind": ["document:write"],
    "kortex.document.preview.generate": ["document:read"],
    "kortex.document.adapter.list": ["document:read"],
    "kortex.document.intelligence.analyze": ["document:read"],
    "kortex.document.recommendation.get": ["document:read"],
    "kortex.document.adapter.register": ["document:write"],
    "kortex.document.template.list": ["document:read"],
    "kortex.document.profile.list": ["document:read"],  # M7.4-W2
}


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
        self._adapter_registry = adapter_registry if adapter_registry is not None else DocumentAdapterRegistry()
        self._sandbox = sandbox if sandbox is not None else AdapterSandbox(registry=self._adapter_registry)
        self._template_library = (
            template_library if template_library is not None else TemplateLibrary(load_defaults=True)
        )
        self._template_binder = template_binder if template_binder is not None else TemplateBinder()
        self._lifecycle_manager = lifecycle_manager if lifecycle_manager is not None else DocumentLifecycleManager()
        self._data_store: IDataStore | None = None
        self._profile_manager = (
            profile_manager
            if profile_manager is not None
            else DocumentOperationProfileManager(
                template_library=self._template_library,
                adapter_registry=self._adapter_registry,
            )
        )
        self._recovery_manager = recovery_manager if recovery_manager is not None else DocumentRecoveryManager()
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
        self._security_verifier = security_verifier if security_verifier is not None else DocumentSecurityVerifier()
        self._storage_binder = storage_binder if storage_binder is not None else DocumentStorageBinder()
        self._intelligence_provider = (
            intelligence_provider if intelligence_provider is not None else DefaultDocumentIntelligenceProvider()
        )
        self._recommendation_provider = (
            recommendation_provider if recommendation_provider is not None else DefaultDocumentRecommendationProvider()
        )

        self._diagnostics = DocumentDiagnostics(
            registry=self._adapter_registry,
            template_library=self._template_library,
            profile_manager=self._profile_manager,
            lifecycle_manager=self._lifecycle_manager,
        )
        self._emitted_events: list[DocumentBaseEvent] = []
        # Set by initialize() when booted through a real Kernel; stays None for standalone
        # construction (e.g. DocumentEngine() in unit tests). Enables _emit_event to publish
        # to the real Kernel Event Engine in addition to local recording.
        self._kernel: Any = None

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

    async def _emit_event(self, event: DocumentBaseEvent) -> None:
        """Record an emitted event locally and publish it to the Kernel Event Engine.

        Local recording into self._emitted_events always happens, preserving existing
        behavior for standalone construction (DocumentEngine() with no Kernel, as used by
        most unit tests) and for callers that inspect the emitted_events property directly.
        Publication to the real Kernel Event Engine additionally occurs whenever this engine
        was initialized with a Kernel exposing publish_event(); a publication failure is
        logged and never propagates, since local event recording must never depend on Kernel
        availability.
        """
        self._emitted_events.append(event)
        if self._kernel is not None and hasattr(self._kernel, "publish_event"):
            try:
                await self._kernel.publish_event(
                    topic=event.event_type,
                    payload=event.model_dump(),
                    sender=self.name,
                )
            except Exception:
                self.logger.debug("Failed to publish event '%s' to Kernel Event Engine.", event.event_type)

    # -- BaseEngine Lifecycle Implementations ---------------------------------

    async def initialize(self, kernel: Any = None) -> None:
        """Initialize engine resources and register capabilities with Kernel."""
        self._set_state(EngineState.INITIALIZING)
        self._kernel = kernel

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
                        self._lifecycle_manager._repository = DocumentRepository(data_store=self._data_store)
                    if self._template_library.repository is None and self._data_store is not None:
                        self._template_library._repository = TemplateRepository(data_store=self._data_store)
                    if self._profile_manager.repository is None and self._data_store is not None:
                        self._profile_manager._repository = DocumentRepository(data_store=self._data_store)

                # Resolve IFileStore/IObjectStore/ICacheStore alongside the IDataStore already
                # resolved above, and populate the DocumentStorageBinder plus every subsystem's
                # optional cache_store dependency. Explicit constructor injection always takes
                # priority (mirrors the IDataStore wiring pattern above) — this only fills in
                # stores that were never explicitly configured.
                file_store = getattr(storage_engine, "file", None) if storage_engine is not None else None
                object_store = getattr(storage_engine, "object", None) if storage_engine is not None else None
                cache_store = getattr(storage_engine, "cache", None) if storage_engine is not None else None

                if self._storage_binder.data_store is None and self._data_store is not None:
                    self._storage_binder._data_store = self._data_store
                if self._storage_binder.file_store is None and file_store is not None:
                    self._storage_binder._file_store = file_store
                if self._storage_binder.object_store is None and object_store is not None:
                    self._storage_binder._object_store = object_store
                if self._storage_binder.cache_store is None and cache_store is not None:
                    self._storage_binder._cache_store = cache_store

                if self._recovery_manager.cache_store is None and cache_store is not None:
                    self._recovery_manager._cache_store = cache_store
                if self._template_library.cache_store is None and cache_store is not None:
                    self._template_library._cache_store = cache_store
                if self._lifecycle_manager.cache_store is None and cache_store is not None:
                    self._lifecycle_manager._cache_store = cache_store
                if self._adapter_registry.cache_store is None and cache_store is not None:
                    self._adapter_registry._cache_store = cache_store
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
                # Capability handlers are deliberately heterogeneous -- each one
                # has its own parameter/return shape. `Callable[..., Any] | None`
                # is exactly the type `Kernel.register_capability(handler=...)`
                # declares for them, so this annotation states the real dispatch
                # contract rather than letting the first branch narrow it.
                handler: Callable[..., Any] | None = None
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
                elif cap == "kortex.document.intelligence.analyze":
                    handler = self.analyze_document_intelligence
                elif cap == "kortex.document.recommendation.get":
                    handler = self.get_recommendation
                elif cap == "kortex.document.adapter.register":
                    handler = self.register_adapter
                elif cap == "kortex.document.template.list":
                    handler = self.list_templates
                elif cap == "kortex.document.profile.list":
                    handler = self.list_profiles

                if handler is None:
                    # Never register a capability with no working handler — a resolvable but
                    # uninvocable capability is worse than one that simply isn't registered.
                    # This is a defensive backstop: every capability declared by
                    # DocumentDiagnostics.capabilities() above is mapped to a real handler, so
                    # this only triggers if that list is ever extended without a matching branch.
                    self.logger.warning(
                        "Capability '%s' declared by capabilities() has no registered handler; "
                        "skipping Kernel registration to avoid exposing an unusable capability.",
                        cap,
                    )
                    continue

                try:
                    kernel.register_capability(
                        name=cap,
                        description=f"Document Engine capability: {cap}",
                        provider=self.name,
                        handler=handler,
                        required_permissions=_DOCUMENT_CAPABILITY_PERMISSIONS.get(cap),
                    )
                except TypeError:
                    kernel.register_capability(cap, handler)

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
        self,
        profile_id: str,
        request: OperationRequest,
        principal: SecurityPrincipal | None = None,
    ) -> OperationResult:
        """Execute a Document Operation Profile via configured Adapter Pipeline (IDocumentEngine protocol).

        `principal` (M7.4-W1): the Kernel dispatcher injects its own verified
        identity into any handler parameter literally named `principal`.
        Before this fix, `binding_context.tenant_id` was trusted as-is from
        caller-supplied data with no cross-check against the authenticated
        caller's real tenant -- a caller holding the coarse `document:execute`
        permission could reach any tenant's operation profile, template, and
        output storage location by supplying that tenant's id in the request
        payload. When a verified `principal` is present, its `tenant_id` is
        authoritative: the effective binding context is corrected to it
        before profile resolution, template resolution, or output storage
        ever read a tenant identifier. Mirrors `ConnectorEngine.execute_action`'s
        identical M6.3-1 correction.
        """
        start_time = time.perf_counter()

        if request is None or not request.request_id:
            raise DocumentOperationError("Invalid OperationRequest: request_id missing.")

        # Emit operation started event
        await self._emit_event(DocumentOperationStartedEvent(request_id=request.request_id, profile_id=profile_id))

        binding_context = request.binding_context or BindingContext(context_id=f"ctx-{request.request_id}")
        if principal is not None:
            binding_context = binding_context.model_copy(update={"tenant_id": principal.tenant_id})

        # 1. Resolve profile
        profile = await self._profile_manager.get_profile(profile_id, tenant_id=binding_context.tenant_id)

        # 2. Template Resolution & Binding
        if profile.required_template_id:
            schema = await self._template_library.get_template(
                profile.required_template_id, tenant_id=binding_context.tenant_id
            )
            report = await self._template_binder.bind(schema, binding_context)
            if not report.is_valid:
                exec_ms = (time.perf_counter() - start_time) * 1000.0
                self._diagnostics.record_operation_executed(is_success=False)
                await self._emit_event(
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

            # Additive object storage persistence: OperationResult.output_bytes below is
            # unaffected by this and always reflects pipeline_res.final_output_bytes exactly
            # as before. A storage failure here is logged and never fails the operation itself.
            if is_completed and pipeline_res.final_output_bytes and profile.output_bucket:
                try:
                    object_key = f"{binding_context.tenant_id}/{profile_id}/{request.request_id}"
                    await self._storage_binder.store_document_output(
                        bucket_name=profile.output_bucket,
                        object_key=object_key,
                        data=pipeline_res.final_output_bytes,
                    )
                except Exception:
                    self.logger.debug(
                        "Object storage output persistence failed for request '%s'; OperationResult is unaffected.",
                        request.request_id,
                    )

            if is_completed:
                await self._emit_event(
                    DocumentOperationCompletedEvent(
                        request_id=request.request_id,
                        profile_id=profile_id,
                        status="COMPLETED",
                        execution_time_ms=exec_ms,
                    )
                )
            else:
                await self._emit_event(
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
        await self._emit_event(
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
        payload: bytes | None = None,
        principal: SecurityPrincipal | None = None,
    ) -> DocumentMetadata:
        """Transition document version to a new lifecycle state (IDocumentEngine protocol).

        `principal` (M7.4-W1): before this fix, this method accepted no
        tenant identifier of any kind and always operated against
        `DocumentLifecycleManager`'s own `tenant_id="default"` fallback --
        the manager itself was already correctly tenant-scoped (`get_lineage`/
        `transition_state` both already take `tenant_id`), the gap was
        entirely that this Kernel-facing handler never passed one through.
        When a verified `principal` is present, its `tenant_id` is
        authoritative and is threaded into both calls below, so a caller can
        no longer transition (including publishing) a document version
        outside their own tenant partition merely by supplying its
        document_id/version_id.

        Args:
            document_id: Root document UUID.
            version_id: Specific version UUID to transition.
            target_state: Proposed target lifecycle state.
            payload: Optional binary payload being published, used to compute a SHA256
                     integrity hash when target_state is PUBLISHED. Ignored otherwise.
        """
        # M7.4-W1 (unrelated pre-existing defect, discovered by this milestone's own
        # first-ever real-dispatch test of this capability, fixed narrowly here): a real
        # Kernel/HTTP/AI caller can only ever deliver `target_state` as a JSON string, not
        # a live `DocumentLifecycleState` instance -- the M7.2 dispatch-coercion fix only
        # resolves `BaseModel`-typed parameters, not `Enum`-typed ones, so nothing coerced
        # this before it reached `DocumentLifecycleManager`, which eventually calls
        # `target_state.value` and would crash with `AttributeError: 'str' object has no
        # attribute 'value'`. Every prior test of this capability called the Python method
        # directly with a real enum instance, so this was never exercised until now.
        if isinstance(target_state, str):
            target_state = DocumentLifecycleState(target_state)

        tenant_id = principal.tenant_id if principal is not None else "default"

        try:
            prev_meta = await self._lifecycle_manager.get_lineage(document_id, tenant_id=tenant_id)
            from_state = prev_meta[-1].lifecycle_state.value if prev_meta else "UNKNOWN"
        except Exception:
            from_state = "UNKNOWN"

        new_meta = await self._lifecycle_manager.transition_state(
            document_id=document_id,
            version_id=version_id,
            target_state=target_state,
            tenant_id=tenant_id,
            payload=payload,
        )

        # Emit lifecycle transitioned event
        await self._emit_event(
            DocumentLifecycleTransitionedEvent(
                document_id=document_id,
                version_id=version_id,
                from_state=from_state,
                to_state=target_state.value,
            )
        )

        # Emit the more specific state-transition events. transition_lifecycle is the single
        # correct production boundary for every lifecycle state change (PUBLISHED, SUPERSEDED,
        # ARCHIVED all necessarily pass through here), so no additional facade method is needed
        # to wire these — unlike DocumentCreatedEvent, which has no corresponding capability
        # (see analyze_document_intelligence/register_adapter docstrings for the analogous
        # reasoning on the other two previously-unwired events).
        if target_state == DocumentLifecycleState.PUBLISHED:
            await self._emit_event(
                DocumentPublishedEvent(
                    document_id=document_id,
                    version_id=version_id,
                    published_at=new_meta.published_at or "",
                )
            )
            if new_meta.parent_version_id is not None:
                await self._emit_event(
                    DocumentSupersededEvent(
                        document_id=document_id,
                        superseded_version_id=new_meta.parent_version_id,
                        new_version_id=version_id,
                    )
                )
        elif target_state == DocumentLifecycleState.ARCHIVED:
            await self._emit_event(DocumentArchivedEvent(document_id=document_id, version_id=version_id))

        return new_meta

    async def bind_template(
        self,
        template_id: str,
        context: BindingContext,
        principal: SecurityPrincipal | None = None,
    ) -> ValidationReport:
        """Validate and bind context data against a declarative Template Schema (IDocumentEngine protocol).

        `principal` (M7.4-W1): `context.tenant_id` was previously trusted
        as-is from caller-supplied data. When a verified `principal` is
        present, its `tenant_id` is authoritative and corrects the effective
        context before template resolution, mirroring `execute_profile`'s
        identical fix.
        """
        if principal is not None:
            context = context.model_copy(update={"tenant_id": principal.tenant_id})
        schema = await self._template_library.get_template(template_id, tenant_id=context.tenant_id)
        return await self._template_binder.bind(schema, context)

    async def generate_preview(self, request_id: str, options: PreviewOptions) -> PreviewResult:
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

    async def list_templates(self) -> list[TemplateSchema]:
        """List the latest version of every registered template.

        Backs capability `kortex.document.template.list` (Slice 4.7).
        `TemplateLibrary.list_templates()` already existed, fully
        implemented and pre-seeded with real standard templates
        (`TemplateLibrary._load_standard_templates`) — this is a thin
        passthrough exposing it, not new business logic. No filters are
        passed (no capability path exists yet to register a tenant-specific
        template, so every tenant sees the same standard set; revisit once
        `register_template`/`install_template` are ever exposed)."""
        return await self._template_library.list_templates()

    async def list_profiles(self, principal: SecurityPrincipal | None = None) -> list[DocumentOperationProfile]:
        """List the tenant's registered Document Operation Profiles (M7.4-W2).

        Backs capability `kortex.document.profile.list`. Closes the gap
        identified in M7.4 planning: `DocumentOperationProfileManager.
        list_profiles(tenant_id)` already existed, already correctly
        tenant-scoped, but had no corresponding Kernel capability -- neither
        an AI agent nor a desktop client had any authorized way to discover
        which profile_id values exist to target via `execute_profile`.
        Mirrors `ConnectorEngine.list_profiles`'s identical M7.3 precedent:
        `principal`, when present, is authoritative over any caller-supplied
        tenant identifier (there is none here to override -- this handler
        never accepted one to begin with, so there is nothing to correct,
        only to bind correctly from the start).
        """
        tenant_id = principal.tenant_id if principal is not None else None
        return await self._profile_manager.list_profiles(tenant_id=tenant_id)

    async def register_adapter(self, adapter: BaseDocumentAdapter | AdapterMetadata) -> BaseDocumentAdapter:
        """Register a new document adapter into DocumentAdapterRegistry.

        Backs capability `kortex.document.adapter.register` (Section 17, item 7). Emits
        DocumentAdapterRegisteredEvent — this is the correct production boundary for that
        event since it is the one spec-declared capability whose action is "a new document
        adapter is registered."

        Args:
            adapter: BaseDocumentAdapter subclass instance or AdapterMetadata.

        Returns:
            The registered BaseDocumentAdapter instance.

        Raises:
            DocumentAdapterError: If contract validation fails or version is duplicate.
        """
        registered = self._adapter_registry.register_adapter(adapter)
        await self._emit_event(
            DocumentAdapterRegisteredEvent(
                adapter_id=registered.metadata.adapter_id,
                display_name=registered.metadata.display_name,
                vendor=registered.metadata.vendor,
            )
        )
        return registered

    async def analyze_document_intelligence(
        self,
        document_id: str,
        version_id: str,
        ontology: dict[str, Any] | None = None,
    ) -> DocumentIntelligenceModel:
        """Trigger intelligence analysis via IDocumentIntelligenceProvider.

        Backs capability `kortex.document.intelligence.analyze` (Section 17, item 5). This is
        an explicitly-invoked, standalone capability — consistent with the engine's AI-Optional
        Design (Section 10), intelligence analysis is never triggered automatically as a side
        effect of execute_profile or any other operation; a caller must invoke this capability
        deliberately for analysis to occur.

        M7.4-W1 security audit note (deliberately NOT fixed here): like
        `execute_profile`/`bind_template`/`transition_lifecycle` before their
        M7.4 fixes, this handler accepts no verified tenant identity and
        cannot correct one either -- unlike those three,
        `IDocumentIntelligenceProvider.analyze_document` (the underlying
        provider protocol) has no `tenant_id` parameter at all, so there is
        no tenant-aware call to thread a corrected identity into. Closing
        this gap correctly would require extending the
        `IDocumentIntelligenceProvider` protocol itself (and its
        `DefaultDocumentIntelligenceProvider` implementation) to accept and
        enforce tenant scoping -- a provider-interface change, not a
        capability-handler fix, and out of M7.4's scope (this capability is
        not part of M7.4's AI tool surface; see the M7.4 planning report
        §8/§17). Flagged explicitly rather than silently left as an
        undocumented gap or patched with an incomplete, non-functional fix.
        The same reasoning applies to `kortex.document.recommendation.get`
        and `kortex.document.preview.generate`, neither of which this
        milestone touches either.

        Emits DocumentIntelligenceUpdatedEvent: analyze_document is the only capability that
        computes/refreshes a document's DocumentIntelligenceModel, so a successful call here is
        the correct production boundary for "intelligence metadata model is updated"
        (Section 16). update_intelligence_incrementally() has no corresponding capability and
        is intentionally not wired to this event for the same reason DocumentCreatedEvent is
        not wired (see class docstring notes on transition_lifecycle) — no capability exposes
        that action yet.

        Args:
            document_id: Root document UUID.
            version_id: Specific version UUID.
            ontology: Optional declarative ontology schema guiding extraction.

        Returns:
            Structured DocumentIntelligenceModel.
        """
        # Provider protocol is intentionally `-> Any` (pluggable boundary); see
        # this method's docstring on why narrowing it is out of scope here.
        model: DocumentIntelligenceModel = await self._intelligence_provider.analyze_document(
            document_id, version_id, ontology=ontology
        )
        await self._emit_event(DocumentIntelligenceUpdatedEvent(document_id=document_id, version_id=version_id))
        return model

    async def get_recommendation(self, recommendation_type: str, **kwargs: Any) -> Any:
        """Query AI recommendations via IDocumentRecommendationProvider.

        Backs capability `kortex.document.recommendation.get` (Section 17, item 6). The
        underlying IDocumentRecommendationProvider protocol exposes three distinct recommendation
        kinds (Section 10.2); since the spec declares exactly one canonical capability name for
        all of them, recommendation_type selects which one to invoke rather than exposing three
        separate capabilities not named anywhere in Section 17.

        Args:
            recommendation_type: One of "template", "operation_profile", "adapter_pipeline".
            **kwargs: Forwarded to the corresponding IDocumentRecommendationProvider method:
                - "template": user_intent (str), data_schema (dict, optional).
                - "operation_profile": business_operation (str), user_context (dict, optional).
                - "adapter_pipeline": profile_id (str), installed_adapters (list, optional —
                  defaults to the currently registered adapters when omitted).

        Returns:
            The recommendation result; shape depends on recommendation_type.

        Raises:
            DocumentOperationError: If recommendation_type is not recognized.
        """
        if recommendation_type == "template":
            return await self._recommendation_provider.recommend_template(
                user_intent=kwargs.get("user_intent", ""),
                data_schema=kwargs.get("data_schema", {}),
            )
        if recommendation_type == "operation_profile":
            return await self._recommendation_provider.recommend_operation_profile(
                business_operation=kwargs.get("business_operation", ""),
                user_context=kwargs.get("user_context", {}),
            )
        if recommendation_type == "adapter_pipeline":
            installed_adapters = kwargs.get("installed_adapters")
            if installed_adapters is None:
                installed_adapters = self._adapter_registry.list_adapters()
            return await self._recommendation_provider.recommend_adapter_pipeline(
                profile_id=kwargs.get("profile_id", ""),
                installed_adapters=installed_adapters,
            )
        raise DocumentOperationError(
            f"Unknown recommendation_type '{recommendation_type}'. Expected one of: "
            f"'template', 'operation_profile', 'adapter_pipeline'."
        )


__all__ = ["DocumentEngine"]
