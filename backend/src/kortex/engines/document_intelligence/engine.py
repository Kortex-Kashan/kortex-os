"""KORTEX Document Intelligence Engine.

Standalone, capability-dispatch-driven, locally executed, deterministic
where applicable, stateless in Phase 4 (Article 10/12/29; ARCHITECTURE_
VERSION_1.0.md S20; docs/architecture/document_intelligence_engine_
implementation_spec.md).

Dependency direction (locked): Kernel -> Capability Dispatcher ->
DocumentIntelligenceEngine -> Provider abstractions -> Storage
abstractions. This module imports nothing from `kortex.engines.document`,
`kortex.engines.knowledge`, or `kortex.engines.ai` — enforced by
`backend/tests/unit/test_document_intelligence_architecture.py`.
`SecurityEngine` is never imported directly.

Tenant authority (HARD security invariant — KORTEX Platform Security:
Capability Identity Propagation): every tenant-scoped handler receives its
identity exclusively via the dispatcher-injected `CapabilityExecutionContext`
(`requires_execution_context=True` at registration) — this engine never
independently authenticates a credential of its own. The previous design
had each handler re-verify a `DocumentParseRequest.session_token` field;
that field and the re-verification method (`_verify_principal`) are removed
entirely, not merely bypassed, because they were exactly the vector an
authenticated Tenant-A caller could use to smuggle a separately-valid
Tenant-B token and have this engine execute as Tenant B. A caller-supplied
`bucket_name`/`object_key` is never used as-is: the verified tenant from
`execution_context.tenant_id` is always prefixed onto it, so a forged
reference can at worst resolve to a nonexistent path inside the caller's
own tenant namespace, never into another tenant's real storage.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.core.exceptions import ResourceNotFoundError
from kortex.engines.document_intelligence.config import DocumentIntelligenceConfig
from kortex.engines.document_intelligence.diagnostics import DocumentIntelligenceDiagnostics
from kortex.engines.document_intelligence.exceptions import (
    DocumentIntelligenceError,
    ExtractionTimeoutError,
    ResourceLimitExceededError,
    StorageAccessError,
)
from kortex.engines.document_intelligence.interfaces import IOCREngine, IPDFParser
from kortex.engines.document_intelligence.models import (
    DocumentLayoutBlock,
    DocumentParseRequest,
    OCRResult,
    ParsedDocumentResult,
    StructureAnalysisRequest,
)
from kortex.engines.document_intelligence.providers.ocr_provider import RapidOcrProvider
from kortex.engines.document_intelligence.providers.pdf_parser import PdfPlumberParser
from kortex.engines.document_intelligence.structure_analyzer import StructureAnalyzer
from kortex.engines.storage.interfaces import IEngineDiagnostics

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel
    from kortex.engines.storage.interfaces import IObjectStore

_TENANT_BUCKET_PREFIX = "docint"


class DocumentIntelligenceEngine(BaseEngine, IEngineDiagnostics):
    """Production Document Intelligence Engine: local PDF parsing, local OCR,
    and deterministic structural composition."""

    def __init__(
        self,
        config: DocumentIntelligenceConfig | None = None,
        pdf_provider: IPDFParser | None = None,
        ocr_provider: IOCREngine | None = None,
        structure_analyzer: StructureAnalyzer | None = None,
    ) -> None:
        super().__init__()
        self._config = config or DocumentIntelligenceConfig()
        self._pdf_provider: IPDFParser = pdf_provider or PdfPlumberParser()
        self._ocr_provider: IOCREngine = ocr_provider or RapidOcrProvider()
        self._structure_analyzer = structure_analyzer or StructureAnalyzer()
        self._diagnostics = DocumentIntelligenceDiagnostics(self)
        self._kernel: Kernel | None = None
        self._object_store: IObjectStore | None = None

        self._registered_capabilities: tuple[str, ...] = (
            "kortex.document_intelligence.pdf.parse",
            "kortex.document_intelligence.ocr.extract",
            "kortex.document_intelligence.structure.analyze",
        )

    @property
    def name(self) -> str:
        return "document_intelligence"

    @property
    def dependencies(self) -> list[str]:
        return ["configuration", "registry", "event", "storage"]

    @property
    def config(self) -> DocumentIntelligenceConfig:
        return self._config

    @property
    def registered_capabilities(self) -> tuple[str, ...]:
        return self._registered_capabilities

    # -- Lifecycle -------------------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize engine resources and register capabilities with the Kernel."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)

        try:
            self._kernel = kernel

            if kernel is not None and hasattr(kernel, "container"):
                try:
                    storage_engine = kernel.container.resolve("engine.storage")
                    if storage_engine is not None and hasattr(storage_engine, "object"):
                        self._object_store = storage_engine.object
                except Exception:
                    self.logger.debug(
                        "StorageEngine not resolved from Kernel container; "
                        "object-reference requests will fail until Storage is available."
                    )

            if kernel is not None and hasattr(kernel, "register_capability"):
                kernel.register_capability(
                    name="kortex.document_intelligence.pdf.parse",
                    description="Deterministic local extraction of text, metadata, and tables from PDF bytes.",
                    provider=self.name,
                    handler=self.handle_pdf_parse,
                    requires_authentication=True,
                    required_permissions=["document_intelligence:parse"],
                    security_classification="INTERNAL",
                    requires_execution_context=True,
                )
                kernel.register_capability(
                    name="kortex.document_intelligence.ocr.extract",
                    description="Local OCR text/bounding-box/confidence extraction from image bytes.",
                    provider=self.name,
                    handler=self.handle_ocr_extract,
                    requires_authentication=True,
                    required_permissions=["document_intelligence:parse"],
                    security_classification="INTERNAL",
                    requires_execution_context=True,
                )
                kernel.register_capability(
                    name="kortex.document_intelligence.structure.analyze",
                    description="Deterministic structural composition over previously computed "
                    "PDF/OCR results. Performs no I/O and no automatic chaining.",
                    provider=self.name,
                    handler=self.handle_structure_analyze,
                    requires_authentication=True,
                    required_permissions=["document_intelligence:analyze"],
                    security_classification="INTERNAL",
                )

            self._set_state(EngineState.READY)
            self.logger.info("Document Intelligence Engine initialized successfully.")
        except Exception as exc:
            self._set_state(EngineState.FAILED)
            self.logger.error("Document Intelligence Engine initialization failed: %s", exc, exc_info=True)
            raise

    async def start(self) -> None:
        """No background services required — the engine is stateless in Phase 4."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Document Intelligence Engine started.")

    async def stop(self) -> None:
        """No background tasks to drain — deterministic, immediate shutdown."""
        self.ensure_state(EngineState.RUNNING, EngineState.READY)
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)
        self.logger.info("Document Intelligence Engine stopped.")

    async def health_check(self) -> dict[str, Any]:
        return self.health()

    # -- IEngineDiagnostics ------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._diagnostics.health()

    def metrics(self) -> dict[str, Any]:
        return self._diagnostics.metrics()

    def diagnostics(self) -> dict[str, Any]:
        return self._diagnostics.diagnostics()

    def status(self) -> str:
        return self._diagnostics.status()

    def version(self) -> str:
        return self._diagnostics.version()

    def capabilities(self) -> list[str]:
        return self._diagnostics.capabilities()

    # -- Tenant Authority (HARD security invariant) -------------------------

    def _tenant_bucket(self, tenant_id: str, bucket_name: str) -> str:
        """Prefix the verified tenant onto the caller-supplied bucket name.

        The prefix always comes from the cryptographically verified
        principal, never from the request — so a forged `bucket_name`/
        `object_key` can at most resolve to a nonexistent path inside the
        caller's own tenant namespace, never into another tenant's real
        object. Proven by `test_document_intelligence_security.py`.
        """
        return f"{_TENANT_BUCKET_PREFIX}/{tenant_id}/{bucket_name}"

    async def _resolve_content(self, request: DocumentParseRequest, tenant_id: str) -> bytes:
        if request.content is not None:
            content = request.content
        else:
            if self._object_store is None:
                raise StorageAccessError("Storage Engine is unavailable; cannot resolve object reference.")
            assert request.bucket_name is not None and request.object_key is not None
            scoped_bucket = self._tenant_bucket(tenant_id, request.bucket_name)
            try:
                content = await self._object_store.get_object(bucket_name=scoped_bucket, object_key=request.object_key)
            except ResourceNotFoundError as exc:
                raise StorageAccessError(f"Referenced object was not found: {exc}") from exc

        if len(content) > self._config.max_input_size_bytes:
            raise ResourceLimitExceededError(
                f"Input size {len(content)} bytes exceeds configured limit {self._config.max_input_size_bytes} bytes."
            )
        return content

    # -- Capability Handlers -------------------------------------------------

    async def handle_pdf_parse(
        self, request: DocumentParseRequest, execution_context: CapabilityExecutionContext
    ) -> ParsedDocumentResult:
        content = await self._resolve_content(request, execution_context.tenant_id)

        try:
            result = await asyncio.wait_for(
                self._pdf_provider.parse(content, request.options),
                timeout=self._config.operation_timeout_seconds,
            )
        except TimeoutError as exc:
            self._diagnostics.record_failure()
            raise ExtractionTimeoutError(
                f"PDF parse exceeded {self._config.operation_timeout_seconds}s timeout."
            ) from exc
        except DocumentIntelligenceError:
            self._diagnostics.record_failure()
            raise

        self._diagnostics.record_pdf_parse()
        if result.page_count > self._config.max_pdf_pages:
            self._diagnostics.record_failure()
            raise ResourceLimitExceededError(
                f"Document has {result.page_count} pages, exceeding configured limit {self._config.max_pdf_pages}."
            )
        return result.model_copy(update={"document_id": request.document_id, "version_id": request.version_id})

    async def handle_ocr_extract(
        self, request: DocumentParseRequest, execution_context: CapabilityExecutionContext
    ) -> OCRResult:
        content = await self._resolve_content(request, execution_context.tenant_id)

        if len(content) > self._config.max_ocr_image_bytes:
            self._diagnostics.record_failure()
            raise ResourceLimitExceededError(
                f"Image size {len(content)} bytes exceeds configured limit {self._config.max_ocr_image_bytes} bytes."
            )

        try:
            result = await asyncio.wait_for(
                self._ocr_provider.extract_text(content, request.options),
                timeout=self._config.operation_timeout_seconds,
            )
        except TimeoutError as exc:
            self._diagnostics.record_failure()
            raise ExtractionTimeoutError(
                f"OCR extraction exceeded {self._config.operation_timeout_seconds}s timeout."
            ) from exc
        except DocumentIntelligenceError:
            self._diagnostics.record_failure()
            raise

        self._diagnostics.record_ocr_extraction()
        return result.model_copy(update={"document_id": request.document_id, "version_id": request.version_id})

    async def handle_structure_analyze(self, request: StructureAnalysisRequest) -> list[DocumentLayoutBlock]:
        """Pure composition: no I/O, no PDF/OCR invocation, no AI, no
        semantics. Performs no tenant-scoped Storage access, so no
        `session_token` is required on `StructureAnalysisRequest` at all."""
        blocks = self._structure_analyzer.analyze(request.parsed_result, request.ocr_result)
        self._diagnostics.record_structure_analysis()
        return blocks
