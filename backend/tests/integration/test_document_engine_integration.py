"""Integration Tests for KORTEX OS Document Engine (Milestone 10).

This module implements comprehensive end-to-end integration testing for the Document Engine
across Kernel IoC container boot sequence, Capability Registry resolution, Template System,
Adapter Registry, Adapter Pipeline, Adapter Sandbox, Document Lifecycle state machine,
Storage Engine abstractions (IDataStore, IFileStore, IObjectStore, ICacheStore), and Event Engine
system event publication in accordance with Milestone 10 of the Document Engine Implementation
Specification (Version 3.0.0).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import kortex.engines.document
from kortex.core.base_engine import EngineState
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.document.exceptions import (
    AdapterNotFoundError,
    DocumentLifecycleError,
    DocumentOperationError,
    DocumentProfileNotFoundError,
)
from kortex.engines.document.adapters.macro_adapter import MacroAdapter
from kortex.engines.document.lifecycle import DocumentLifecycleManager
from kortex.engines.document.operation_profile import DocumentOperationProfileManager
from kortex.engines.document.persistence import DocumentRepository, TemplateRepository
from kortex.engines.document.recovery import DocumentRecoveryManager
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    AdapterPipelineDefinition,
    BindingContext,
    DocumentLifecycleState,
    DocumentOperationProfile,
    DocumentOperationType,
    OperationRequest,
    PipelineStage,
    PreviewOptions,
    SecurityClassification,
    SecurityMetadata,
    TemplateSchema,
)
from kortex.engines.document.security import DocumentStorageBinder
from kortex.engines.document.template_library import TemplateLibrary
from kortex.engines.storage.engine import StorageEngine


class IntegrationDummyAdapter(BaseDocumentAdapter):
    """Dummy document adapter for integration testing."""

    def __init__(self, adapter_id: str = "kortex.adapter.integration_pdf") -> None:
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="Integration PDF Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Mock adapter for integration testing",
            supported_capabilities=[
                AdapterCapability.GENERATE,
                AdapterCapability.PREVIEW,
                AdapterCapability.TRANSFORM,
            ],
            supported_operations=[
                DocumentOperationType.GENERATE,
                DocumentOperationType.TRANSFORM,
            ],
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
        if options.get("trigger_crash"):
            raise RuntimeError("Integration adapter crashed!")
        input_data = options.get("input_bytes", b"")
        return b"[INTEGRATION_PDF_OUTPUT]" + (b"_" + input_data if input_data else b"")

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


@pytest.mark.asyncio
async def test_kernel_boot_and_ioc_registration(tmp_path) -> None:
    """1. Kernel Boot / IoC Registration test."""
    kernel = Kernel()

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    # Boot kernel runtime
    await kernel.boot()

    assert kernel.state == KernelState.RUNNING
    assert storage_engine.state == EngineState.RUNNING
    assert document_engine.state == EngineState.RUNNING

    # IoC container resolution
    resolved_by_string = kernel.container.resolve("engine.document")
    assert resolved_by_string is document_engine

    # System health check aggregation
    health = await kernel.health_check()
    assert health["kernel_state"] == "RUNNING"
    assert "document" in health["system_health"]["engines"]
    assert health["system_health"]["engines"]["document"]["status"] == "HEALTHY"

    # Shutdown
    await kernel.shutdown()
    assert kernel.state == KernelState.STOPPED
    assert document_engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_capability_registration_and_resolution(tmp_path) -> None:
    """2. Capability Registration / Resolution test."""
    kernel = Kernel()

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    # Discover capabilities through RegistryEngine
    cap_exec = kernel.get_capability("kortex.document.operation.execute")
    assert cap_exec.provider == "document"

    cap_trans = kernel.get_capability("kortex.document.lifecycle.transition")
    assert cap_trans.provider == "document"

    cap_bind = kernel.get_capability("kortex.document.template.bind")
    assert cap_bind.provider == "document"

    cap_list = kernel.get_capability("kortex.document.adapter.list")
    assert cap_list.provider == "document"

    # Invoke capability handler directly via the M8 test-only accessor
    # (CapabilityDescriptor itself never carries an executable handler).
    adapters_list = kernel._registry_engine.get_raw_handler_for_testing("kortex.document.adapter.list")()
    assert isinstance(adapters_list, list)

    # Milestone 8 remediation: the three capabilities previously registered with
    # handler=None (a real Kernel boot, not a mock) must now resolve to real, working
    # handlers and be genuinely invocable end-to-end.
    cap_intel = kernel.get_capability("kortex.document.intelligence.analyze")
    assert cap_intel.provider == "document"
    intel_handler = kernel._registry_engine.get_raw_handler_for_testing("kortex.document.intelligence.analyze")
    assert intel_handler is not None
    intel_result = await intel_handler("doc-cap-1", "ver-cap-1")
    assert intel_result.document_id == "doc-cap-1"

    cap_rec = kernel.get_capability("kortex.document.recommendation.get")
    assert cap_rec.provider == "document"
    rec_handler = kernel._registry_engine.get_raw_handler_for_testing("kortex.document.recommendation.get")
    assert rec_handler is not None
    rec_result = await rec_handler("operation_profile", business_operation="GENERATE_PAYROLL_SLIP")
    assert rec_result == "profile.payslip.v1"

    cap_register = kernel.get_capability("kortex.document.adapter.register")
    assert cap_register.provider == "document"
    register_handler = kernel._registry_engine.get_raw_handler_for_testing("kortex.document.adapter.register")
    assert register_handler is not None
    registered = await register_handler(
        AdapterMetadata(
            adapter_id="kortex.adapter.cap_test",
            display_name="Capability Test Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Registered via kortex.document.adapter.register in a real Kernel boot",
        )
    )
    assert registered.metadata.adapter_id == "kortex.adapter.cap_test"
    assert document_engine.adapter_registry.get_adapter_by_id("kortex.adapter.cap_test") is not None

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_complete_end_to_end_document_flow(tmp_path) -> None:
    """3. Complete End-to-End Document Flow integration test."""
    kernel = Kernel()

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    # Bind storage engine stores after boot sequence transitions storage_engine to RUNNING
    storage_binder = DocumentStorageBinder(
        data_store=storage_engine.data,
        file_store=storage_engine.file,
        object_store=storage_engine.object,
        cache_store=storage_engine.cache,
    )
    document_engine._storage_binder = storage_binder

    # Track published system events
    received_events: list[Any] = []

    def event_handler(evt: Any) -> None:
        received_events.append(evt)

    kernel.subscribe_event("*", event_handler)

    # 1. Register adapter
    adapter = IntegrationDummyAdapter()
    document_engine.adapter_registry.register_adapter(adapter)

    # Unique per test run: DatabaseEngineManager() defaults to a shared on-disk file
    # (kortex_local.db) when the Kernel isn't given an explicit connection, so a fixed
    # profile_id could collide with a leftover row from a prior run now that
    # DocumentOperationProfileManager persists through it.
    e2e_profile_id = f"profile.e2e.payslip.{uuid4()}"

    # 2. Define pipeline
    pipeline_def = AdapterPipelineDefinition(
        pipeline_id="pipe-e2e",
        profile_id=e2e_profile_id,
        stages=[
            PipelineStage(
                stage_id="stage-gen",
                adapter_id="kortex.adapter.integration_pdf",
                required_capability=AdapterCapability.GENERATE,
            )
        ],
    )

    # 3. Register operation profile
    profile = DocumentOperationProfile(
        id=e2e_profile_id,
        name="End-To-End Payslip Profile",
        namespace="kortex.e2e",
        version="1.0.0",
        description="E2E testing operation profile",
        business_operation="GENERATE_PAYROLL_SLIP",
        required_template_id="payslip.declarative.v1",
        adapter_pipeline=pipeline_def,
        output_bucket="payslips",
    )
    await document_engine.profile_manager.register_profile(profile)

    # 4. Create root document version in Draft state
    doc_ver = await document_engine.lifecycle_manager.create_version(
        title="August 2026 Payslip",
        author_id="hr_manager",
        security_metadata=SecurityMetadata(classification=SecurityClassification.CONFIDENTIAL),
    )
    assert doc_ver.metadata.lifecycle_state == DocumentLifecycleState.DRAFT

    # 5. Execute operation request
    context = BindingContext(
        context_id=f"ctx-{doc_ver.metadata.document_id}",
        data={
            "employee_id": "EMP-999",
            "employee_name": "John Doe",
            "basic_salary": 6000.0,
            "net_salary": 5200.0,
            "period": "2026-08",
        },
    )

    request = OperationRequest(
        request_id="req-e2e-100",
        profile_id=e2e_profile_id,
        binding_context=context,
    )

    result = await document_engine.execute_profile(e2e_profile_id, request)

    assert result.status == "COMPLETED"
    assert result.output_bytes is not None
    assert b"[INTEGRATION_PDF_OUTPUT]" in result.output_bytes

    # 6. Store output binary payload in Storage Engine Object Store
    obj_meta = await storage_binder.store_document_output(
        bucket_name="payslips",
        object_key=f"{doc_ver.metadata.document_id}/v1.pdf",
        data=result.output_bytes,
    )
    assert obj_meta is not None
    assert obj_meta.bucket_name == "payslips"

    # Verify retrieved payload from Object Store
    retrieved_blob = await storage_binder.retrieve_document_output(
        bucket_name="payslips",
        object_key=f"{doc_ver.metadata.document_id}/v1.pdf",
    )
    assert retrieved_blob == result.output_bytes

    # 7. Transition lifecycle: Draft -> Review -> Published
    meta_rev = await document_engine.transition_lifecycle(
        doc_ver.metadata.document_id, doc_ver.version_id, DocumentLifecycleState.REVIEW
    )
    assert meta_rev.lifecycle_state == DocumentLifecycleState.REVIEW

    meta_pub = await document_engine.transition_lifecycle(
        doc_ver.metadata.document_id, doc_ver.version_id, DocumentLifecycleState.PUBLISHED
    )
    assert meta_pub.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert meta_pub.is_immutable is True

    # 8. Verify system event publication — both the local emitted_events record AND real
    # publication to the Kernel's Event Engine (Milestone 8 remediation: engine.py previously
    # never called kernel.publish_event, so this wildcard subscription always silently
    # received nothing; it now proves genuine Kernel Event Engine integration).
    await asyncio.sleep(0.05)
    assert len(document_engine.emitted_events) >= 3

    assert len(received_events) >= 3
    received_topics = {evt.topic for evt in received_events}
    assert "document.operation.started" in received_topics
    assert "document.operation.completed" in received_topics
    assert "document.lifecycle.transitioned" in received_topics
    assert "document.published" in received_topics

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_template_integration_and_immutability(tmp_path) -> None:
    """4. Template Integration test."""
    engine = DocumentEngine()

    schema = await engine.template_library.get_template("payslip.declarative.v1")
    assert schema.template_id == "payslip.declarative.v1"
    assert "employee_id" in schema.placeholders

    context = BindingContext(
        context_id="ctx-tmpl-1",
        data={
            "employee_id": "EMP-100",
            "employee_name": "Jane",
            "basic_salary": 5000.0,
            "net_salary": 4500.0,
            "period": "2026-08",
        },
    )

    report = await engine.bind_template("payslip.declarative.v1", context)
    assert report.is_valid is True
    assert len(report.errors) == 0

    # Immutability check: modifying schema raises exception
    with pytest.raises(Exception):
        schema.template_id = "mutated.id"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_adapter_registry_and_pipeline_sandbox_integration(tmp_path) -> None:
    """5. Adapter Registry + Pipeline Integration test."""
    engine = DocumentEngine()

    adapter = IntegrationDummyAdapter()
    engine.adapter_registry.register_adapter(adapter)

    meta = engine.adapter_registry.get_adapter(AdapterCapability.GENERATE)
    assert meta.adapter_id == "kortex.adapter.integration_pdf"

    pipeline_def = AdapterPipelineDefinition(
        pipeline_id="pipe-seq",
        profile_id="prof-seq",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.adapter.integration_pdf",
                required_capability=AdapterCapability.GENERATE,
            ),
            PipelineStage(
                stage_id="s2",
                adapter_id="kortex.adapter.integration_pdf",
                required_capability=AdapterCapability.TRANSFORM,
            ),
        ],
    )

    res = await engine.pipeline_executor.execute_pipeline_definition(
        definition=pipeline_def,
        context=BindingContext(context_id="ctx-seq"),
    )

    assert res.is_success is True
    assert len(res.stage_results) == 2
    assert b"[INTEGRATION_PDF_OUTPUT]" in res.final_output_bytes


@pytest.mark.asyncio
async def test_adapter_loader_registers_dummy_adapter_on_kernel_boot(tmp_path) -> None:
    """A DocumentEngine booted through a real Kernel has the Dummy Adapter available."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_adapter_loader"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    adapters = document_engine.list_adapters()
    assert any(a.adapter_id == "kortex.document.dummy.v1" for a in adapters)

    preview_result = await document_engine.generate_preview(
        "req-boot-preview", PreviewOptions(page_number=1)
    )
    assert preview_result.image_bytes is not None

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_lifecycle_versioning_and_lineage_rules(tmp_path) -> None:
    """6. Lifecycle Integration test."""
    engine = DocumentEngine()

    # Create root version (Draft)
    v1 = await engine.lifecycle_manager.create_version(
        document_id="doc-lineage-1", title="Lineage Test Doc"
    )
    assert v1.metadata.lifecycle_state == DocumentLifecycleState.DRAFT

    # Transition to Published
    meta_pub = await engine.transition_lifecycle(
        "doc-lineage-1", v1.version_id, DocumentLifecycleState.PUBLISHED
    )
    assert meta_pub.is_immutable is True

    # Reject transition from Published back to Draft
    with pytest.raises(DocumentLifecycleError):
        await engine.transition_lifecycle(
            "doc-lineage-1", v1.version_id, DocumentLifecycleState.DRAFT
        )

    # Create child version from published parent
    v2 = await engine.lifecycle_manager.create_version(
        document_id="doc-lineage-1",
        parent_version_id=v1.version_id,
        title="Lineage Test Doc v2",
    )
    assert v2.version_number == "1.0.1"
    assert v2.metadata.parent_version_id == v1.version_id

    lineage = await engine.lifecycle_manager.get_lineage("doc-lineage-1")
    assert len(lineage) == 2


@pytest.mark.asyncio
async def test_storage_engine_persistence_integration(tmp_path) -> None:
    """7. Storage Engine Integration test."""
    kernel = Kernel()

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_integ"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    binder = DocumentStorageBinder(
        data_store=storage_engine.data,
        file_store=storage_engine.file,
        object_store=storage_engine.object,
        cache_store=storage_engine.cache,
    )
    document_engine._storage_binder = binder

    # Save schema to IFileStore
    file_meta = await binder.save_template_schema("templates/custom.json", b'{"id": "c1"}')
    assert file_meta is not None
    assert await storage_engine.file.file_exists("templates/custom.json") is True

    # Put object in IObjectStore
    obj_meta = await binder.store_document_output("renders", "doc1.pdf", b"[PDF_BYTES]")
    assert obj_meta is not None
    assert obj_meta.sha256_hash is not None

    # Multi-level caching in ICacheStore
    await binder.cache_set("metadata", "doc1", {"state": "PUBLISHED"})
    cached_data = await binder.cache_get("metadata", "doc1")
    assert cached_data == {"state": "PUBLISHED"}

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_document_lifecycle_persistence_survives_fresh_session(tmp_path) -> None:
    """7b. Default DocumentEngine construction must auto-wire repository-backed persistence.

    A version published through the booted engine must be readable by a completely
    independent DocumentLifecycleManager/DocumentRepository pairing that shares only the
    underlying Storage Engine data store, proving durability beyond the original engine
    instance's in-memory state rather than merely asserting an attribute was set.
    """
    kernel = Kernel()

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_persist"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    # DocumentEngine.initialize() must have auto-wired a repository from the Kernel
    # container; the default construction path must no longer be silently in-memory-only.
    assert document_engine.lifecycle_manager.repository is not None

    version = await document_engine.lifecycle_manager.create_version(
        title="Persisted Payslip",
        author_id="user-1",
        version_number="1.0.0",
    )
    published_meta = await document_engine.lifecycle_manager.transition_state(
        document_id=version.document_id,
        version_id=version.version_id,
        target_state=DocumentLifecycleState.PUBLISHED,
    )
    assert published_meta.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert published_meta.is_immutable is True

    # Capture the underlying data store reference before shutdown; StorageEngine.data
    # guards access once the engine leaves READY/RUNNING, but the underlying store itself
    # is not torn down by stop() (only the cache store is cleared).
    data_store = storage_engine.data
    await kernel.shutdown()

    # Simulate a fresh session: a brand-new manager/repository pairing, sharing no Python
    # object state with the original engine, reading through the same underlying store.
    fresh_manager = DocumentLifecycleManager(
        repository=DocumentRepository(data_store=data_store)
    )
    reread_meta = await fresh_manager.get_version(version.document_id, version.version_id)
    assert reread_meta.lifecycle_state == DocumentLifecycleState.PUBLISHED
    assert reread_meta.is_immutable is True


@pytest.mark.asyncio
async def test_template_library_persistence_survives_fresh_session(tmp_path) -> None:
    """7c. Default DocumentEngine construction must also auto-wire TemplateLibrary persistence.

    A custom template registered through the booted engine must be readable by a completely
    independent TemplateLibrary/TemplateRepository pairing sharing only the underlying
    Storage Engine data store, and the built-in standard templates must remain available
    throughout.
    """
    kernel = Kernel()

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_template_persist"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    assert document_engine.template_library.repository is not None

    # Unique per test run: DatabaseEngineManager() defaults to a shared on-disk file
    # (kortex_local.db) when the Kernel isn't given an explicit connection, so a fixed
    # template_id could collide with a leftover row from a prior run.
    unique_id = f"integration.custom.{uuid4()}"
    custom_schema = TemplateSchema(
        template_id=unique_id,
        name="Integration Custom Template",
        namespace="kortex.integration.custom",
        version="1.0.0",
        description="Persisted through the booted DocumentEngine",
        placeholders=["field_a"],
    )
    await document_engine.template_library.register_template(custom_schema)

    # Built-in standard templates remain resolvable alongside the persisted custom one.
    invoice = await document_engine.template_library.get_template("invoice.declarative.v1")
    assert invoice.name == "Standard Invoice Template"

    data_store = storage_engine.data
    await kernel.shutdown()

    fresh_library = TemplateLibrary(
        load_defaults=False, repository=TemplateRepository(data_store=data_store)
    )
    reread = await fresh_library.get_template(unique_id)
    assert reread.namespace == "kortex.integration.custom"


@pytest.mark.asyncio
async def test_operation_profile_persistence_survives_fresh_session(tmp_path) -> None:
    """7d. Default DocumentEngine construction must also auto-wire profile_manager persistence.

    A profile registered through the booted engine must be readable by a completely
    independent DocumentOperationProfileManager/DocumentRepository pairing sharing only the
    underlying Storage Engine data store (mirrors the established TemplateLibrary/
    DocumentLifecycleManager fresh-session persistence pattern).
    """
    kernel = Kernel()

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_profile_persist"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    assert document_engine.profile_manager.repository is not None

    unique_id = f"integration.profile.{uuid4()}"
    pipeline_def = AdapterPipelineDefinition(
        pipeline_id=f"pipe-{unique_id}",
        profile_id=unique_id,
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.document.dummy.v1",
                required_capability=AdapterCapability.GENERATE,
            )
        ],
    )
    profile = DocumentOperationProfile(
        id=unique_id,
        name="Integration Custom Profile",
        namespace="kortex.integration.profile",
        version="1.0.0",
        description="Persisted through the booted DocumentEngine",
        business_operation="INTEGRATION_TEST_OP",
        adapter_pipeline=pipeline_def,
    )
    await document_engine.profile_manager.register_profile(profile)

    data_store = storage_engine.data
    await kernel.shutdown()

    fresh_manager = DocumentOperationProfileManager(
        repository=DocumentRepository(data_store=data_store)
    )
    reread = await fresh_manager.get_profile(unique_id)
    assert reread.namespace == "kortex.integration.profile"
    assert reread.adapter_pipeline is not None
    assert reread.adapter_pipeline.stages[0].adapter_id == "kortex.document.dummy.v1"


@pytest.mark.asyncio
async def test_full_business_operation_flow_with_macro_and_dummy_adapters(tmp_path) -> None:
    """Full hierarchy exercised end-to-end: Business Operation -> Operation Profile ->
    Adapter Pipeline -> Document Adapters (Macro + Dummy), executed via
    DocumentEngine.execute_profile() through a fully booted Kernel.
    """
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_full_flow"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    # Both reference adapters are auto-registered at boot by DocumentAdapterLoader.
    adapter_ids = {a.adapter_id for a in document_engine.list_adapters()}
    assert "kortex.document.macro.v1" in adapter_ids
    assert "kortex.document.dummy.v1" in adapter_ids

    profile_id = f"integration.full.flow.{uuid4()}"
    pipeline_def = AdapterPipelineDefinition(
        pipeline_id=f"pipe-{profile_id}",
        profile_id=profile_id,
        stages=[
            PipelineStage(
                stage_id="stage-macro",
                adapter_id="kortex.document.macro.v1",
                required_capability=AdapterCapability.MACROS,
            ),
            PipelineStage(
                stage_id="stage-dummy",
                adapter_id="kortex.document.dummy.v1",
                required_capability=AdapterCapability.GENERATE,
            ),
        ],
    )
    profile = DocumentOperationProfile(
        id=profile_id,
        name="Full Flow Profile",
        namespace="kortex.integration.fullflow",
        version="1.0.0",
        description="Business Operation -> Profile -> Pipeline -> Adapters, end to end",
        business_operation="INTEGRATION_FULL_FLOW",
        adapter_pipeline=pipeline_def,
    )
    await document_engine.profile_manager.register_profile(profile)

    request = OperationRequest(
        request_id=f"req-{profile_id}",
        profile_id=profile_id,
        binding_context=BindingContext(context_id=f"ctx-{profile_id}", data={"rows": 5}),
    )
    result = await document_engine.execute_profile(profile_id, request)

    assert result.status == "COMPLETED"
    assert result.output_bytes is not None
    # Final output came from the last stage (dummy adapter), which echoes its own adapter_id.
    assert b"kortex.document.dummy.v1" in result.output_bytes

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_event_engine_pubsub_integration(tmp_path) -> None:
    """8. Event Engine Integration test."""
    kernel = Kernel()

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_evt"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    received_topics: list[str] = []

    def handle_event(topic: str, payload: Any = None, sender: str = "") -> None:
        received_topics.append(topic)

    kernel.subscribe_event("system.started", handle_event)

    # Trigger operation on document engine
    # Unique per test run: DatabaseEngineManager() defaults to a shared on-disk file
    # (kortex_local.db) when the Kernel isn't given an explicit connection, so a fixed
    # profile_id could collide with a leftover row from a prior run now that
    # DocumentOperationProfileManager persists through it.
    evt_profile_id = f"profile.evt.test.{uuid4()}"
    profile = DocumentOperationProfile(
        id=evt_profile_id,
        name="Event Test Profile",
        namespace="kortex.evt",
        version="1.0.0",
        description="Event test profile",
        business_operation="EVT_OP",
    )
    await document_engine.profile_manager.register_profile(profile)

    req = OperationRequest(
        request_id="req-evt-test-1",
        profile_id=evt_profile_id,
        business_operation="EVT_OP",
        binding_context=BindingContext(context_id="ctx-evt"),
    )
    await document_engine.execute_profile(evt_profile_id, req)

    # Verify event logged in engine emitted_events
    assert len(document_engine.emitted_events) >= 2
    event_types = [e.event_type for e in document_engine.emitted_events]
    assert "document.operation.started" in event_types
    assert "document.operation.completed" in event_types

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_failure_and_boundary_verification(tmp_path) -> None:
    """9. Failure / Boundary Verification test."""
    engine = DocumentEngine()

    # Unknown operation profile
    req_missing_prof = OperationRequest(
        request_id="req-f1",
        profile_id="non_existent_profile",
        binding_context=BindingContext(context_id="c"),
    )
    with pytest.raises(DocumentProfileNotFoundError):
        await engine.execute_profile("non_existent_profile", req_missing_prof)

    # Invalid operation request
    with pytest.raises(DocumentOperationError):
        await engine.execute_profile("p1", None)  # type: ignore[arg-type]

    # Unknown adapter lookup
    with pytest.raises(AdapterNotFoundError):
        engine.adapter_registry.get_adapter_by_id("non_existent_adapter")

    # Adapter stage crash failure
    adapter = IntegrationDummyAdapter()
    engine.adapter_registry.register_adapter(adapter)

    pipeline_crash = AdapterPipelineDefinition(
        pipeline_id="pipe-fail",
        profile_id="prof-fail",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.adapter.integration_pdf",
                required_capability=AdapterCapability.GENERATE,
                stage_options={"trigger_crash": True},
            )
        ],
    )
    profile_crash = DocumentOperationProfile(
        id="prof-fail",
        name="Crash Profile",
        namespace="kortex.fail",
        version="1.0.0",
        description="Crash profile",
        business_operation="CRASH_OP",
        adapter_pipeline=pipeline_crash,
    )
    await engine.profile_manager.register_profile(profile_crash)

    req_crash = OperationRequest(
        request_id="req-crash-99",
        profile_id="prof-fail",
        binding_context=BindingContext(context_id="c"),
    )
    res_crash = await engine.execute_profile("prof-fail", req_crash)
    assert res_crash.status == "FAILED"
    assert len(res_crash.errors) > 0


# Milestone 10: Architecture Compliance Audit verification (spec Section 13, Milestone 10,
# bullet 4). These patterns scan Document Engine's own source tree — never test files — and
# back the five checks in test_architecture_compliance_assertions below. Each check fails the
# test the moment the real rule is violated; none of them are presence/snapshot checks.
_ENGINE_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+kortex\.engines\.(?P<engine>\w+)(?:\.(?P<submodule>\w+))?"
)
_STORAGE_PRIMITIVE_RE = re.compile(
    r"\bimport\s+sqlite3\b|\bimport\s+asyncpg\b|\bimport\s+aiosqlite\b|"
    r"\bcreate_engine\s*\(|\bcreate_async_engine\s*\(|(?<![\w.])open\s*\("
)
_PRINT_CALL_RE = re.compile(r"(?<![\w.])print\s*\(")

# Only the abstraction/DTO layer of the Storage Engine may be imported directly by Document
# Engine source — never its concrete implementation modules (.engine, .stores.*). Declaring
# "storage" as a Kernel dependency authorizes consuming this boundary, not reaching past it.
_STORAGE_ABSTRACTION_SUBMODULES = {"interfaces", "models"}

# M7.4-W1: `kortex.engines.security.models` (pure Pydantic DTOs — `SecurityPrincipal`,
# `TokenPayload`, etc.) is a shared-domain-type exception, not a grant to consume the
# Security Engine's own behavior — Document Engine's tenant-derivation fix only needs the
# *type* of the Kernel-verified principal the dispatcher injects, never SecurityEngine
# itself, identical to the narrow, already-audited exception `ConnectorEngine` and
# `WorkflowEngine` both already rely on without declaring "security" as a hard Kernel boot
# dependency (see each engine's own `dependencies()` docstring for why "security" is
# deliberately not a declared dependency anywhere in this codebase). Unlike the storage
# carve-out above, this does not require "security" to be a declared dependency at all —
# it is a standalone always-allowed exception, since Document Engine has no other
# legitimate reason to import from Security Engine.
_ALWAYS_ALLOWED_UNDECLARED_ENGINE_SUBMODULES: dict[str, set[str]] = {"security": {"models"}}


def _document_engine_source_files() -> list[Path]:
    """Return every .py file in the Document Engine package (source only, never tests)."""
    package_dir = Path(kortex.engines.document.__file__).parent
    return sorted(package_dir.rglob("*.py"))


@pytest.mark.asyncio
async def test_architecture_compliance_assertions(tmp_path) -> None:
    """10. Architecture Compliance Audit verification (Milestone 10).

    Substantively verifies the five rules in docs/testing/ARCHITECTURE_AUDIT_STANDARD.md,
    scoped to the Document Engine package: import boundary, storage-access boundary,
    capability naming, tenant isolation, and no print(). Each assertion is tied to a real,
    observable violation — not a hardcoded snapshot of current file/capability names.
    """
    source_files = _document_engine_source_files()
    assert source_files, "Document Engine source scan found no files — scan path is wrong."

    declared_dependencies = set(DocumentEngine().dependencies) | {"document"}

    import_violations: list[str] = []
    storage_access_violations: list[str] = []
    print_violations: list[str] = []

    for path in source_files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            # Rule 1: Import boundary. Document Engine may only import from itself and from
            # the abstraction layer of engines it declares as Kernel dependencies.
            import_match = _ENGINE_IMPORT_RE.match(line)
            if import_match is not None:
                engine, submodule = import_match.group("engine"), import_match.group("submodule")
                if engine not in declared_dependencies:
                    allowed_undeclared_submodules = _ALWAYS_ALLOWED_UNDECLARED_ENGINE_SUBMODULES.get(
                        engine, set()
                    )
                    if submodule not in allowed_undeclared_submodules:
                        import_violations.append(
                            f"{path.name}:{lineno}: imports 'kortex.engines.{engine}', which is "
                            f"not 'document' itself nor in its declared dependency contract "
                            f"{sorted(declared_dependencies)}, and is not one of the standalone "
                            f"always-allowed shared-domain-type imports "
                            f"{_ALWAYS_ALLOWED_UNDECLARED_ENGINE_SUBMODULES}"
                        )
                elif engine == "storage" and submodule not in _STORAGE_ABSTRACTION_SUBMODULES:
                    import_violations.append(
                        f"{path.name}:{lineno}: imports 'kortex.engines.storage.{submodule}' — "
                        f"only {sorted(_STORAGE_ABSTRACTION_SUBMODULES)} are within the Storage "
                        f"Engine's abstraction boundary; its concrete implementation modules "
                        f"must not be imported directly"
                    )

            # Rule 2: Storage-access boundary. Document Engine must consume Storage Engine only
            # via its abstractions/repositories, never a raw DB driver or the filesystem.
            if _STORAGE_PRIMITIVE_RE.search(line):
                storage_access_violations.append(f"{path.name}:{lineno}: {stripped}")

            # Rule 5: No print(). Checked in the same pass since it is the same kind of scan.
            if _PRINT_CALL_RE.search(line):
                print_violations.append(f"{path.name}:{lineno}: {stripped}")

    assert not import_violations, "Import boundary violated:\n" + "\n".join(import_violations)
    assert not storage_access_violations, (
        "Storage-access boundary violated (raw DB/file I/O primitive found):\n"
        + "\n".join(storage_access_violations)
    )
    assert not print_violations, (
        "print() statement found in Document Engine source:\n" + "\n".join(print_violations)
    )

    # Rule 3: Capability naming. Every live, currently-declared capability must conform to
    # kortex.<domain>.<resource>.<action> — checked against the real capabilities() list, not
    # a hardcoded copy of it.
    capability_name_re = re.compile(r"^kortex\.[a-z_]+\.[a-z_]+\.[a-z_]+$")
    live_capabilities = DocumentEngine().capabilities()
    assert live_capabilities, "DocumentEngine.capabilities() returned no capabilities to verify."
    for cap_name in live_capabilities:
        assert capability_name_re.match(cap_name), (
            f"Capability name violates naming convention: '{cap_name}'"
        )

    # Rule 4: Tenant isolation. Real Kernel + real StorageEngine + real DocumentEngine boot,
    # verifying the actual, already-established repository-level tenant boundary
    # (DocumentRepository.get_version's tenant_id-scoped query) rather than merely checking
    # that a tenant_id field exists on a model.
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_arch_compliance"))
    document_engine = DocumentEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)
    await kernel.boot()

    assert document_engine.lifecycle_manager.repository is not None, (
        "Tenant isolation check requires the repository-backed path; Kernel wiring did not "
        "auto-configure a repository."
    )

    version = await document_engine.lifecycle_manager.create_version(
        title="Architecture Compliance Tenant Isolation Doc",
        author_id="arch-audit",
        tenant_id="tenant-arch-a",
    )

    with pytest.raises(DocumentLifecycleError):
        await document_engine.lifecycle_manager.get_version(
            version.document_id, version.version_id, tenant_id="tenant-arch-b"
        )

    owned = await document_engine.lifecycle_manager.get_version(
        version.document_id, version.version_id, tenant_id="tenant-arch-a"
    )
    assert owned.document_id == version.document_id

    await kernel.shutdown()


class TransientIntegrationAdapter(BaseDocumentAdapter):
    """Adapter for integration testing transient recovery in DocumentEngine."""

    def __init__(self, adapter_id: str = "kortex.adapter.transient_integ", fail_count: int = 1) -> None:
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="Transient Integ Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Transient integration adapter",
            supported_capabilities=[AdapterCapability.TRANSFORM],
            supported_operations=[DocumentOperationType.TRANSFORM],
        )
        self.fail_count = fail_count
        self.call_count = 0

    @property
    def metadata(self) -> AdapterMetadata:
        return self._meta

    async def execute(self, operation_type, binding_context, options) -> bytes:
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise RuntimeError(f"Transient integration failure {self.call_count}")
        return b"[TRANSIENT_INTEG_SUCCESS]"

    def validate_schema(self, schema) -> bool:
        return True


@pytest.mark.asyncio
async def test_document_engine_recovery_execution_path_integration(tmp_path) -> None:
    """Exercise checkpointing, retry re-dispatch, and rollback through the real DocumentEngine.execute_profile() path."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_recovery_integ"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    # Register adapters
    adapter1 = IntegrationDummyAdapter()
    adapter2 = TransientIntegrationAdapter(fail_count=1)
    document_engine.adapter_registry.register_adapter(adapter1)
    document_engine.adapter_registry.register_adapter(adapter2)

    # 1. Success with transient retry recovery
    prof_id_success = f"prof-rec-integ-{uuid4()}"
    pipeline_def = AdapterPipelineDefinition(
        pipeline_id=f"pipe-{prof_id_success}",
        profile_id=prof_id_success,
        stages=[
            PipelineStage(
                stage_id="stage-gen",
                adapter_id="kortex.adapter.integration_pdf",
                required_capability=AdapterCapability.GENERATE,
            ),
            PipelineStage(
                stage_id="stage-trans",
                adapter_id="kortex.adapter.transient_integ",
                required_capability=AdapterCapability.TRANSFORM,
            ),
        ],
    )
    profile = DocumentOperationProfile(
        id=prof_id_success,
        name="Recovery Integration Profile",
        namespace="kortex.integ",
        version="1.0.0",
        description="Profile for testing real DocumentEngine execution recovery",
        business_operation="RECOVERY_INTEG_OP",
        adapter_pipeline=pipeline_def,
    )
    await document_engine.profile_manager.register_profile(profile)

    req_success = OperationRequest(
        request_id="req-integ-rec-1",
        profile_id=prof_id_success,
        business_operation="RECOVERY_INTEG_OP",
        binding_context=BindingContext(context_id="ctx-integ-rec"),
    )
    result_success = await document_engine.execute_profile(prof_id_success, req_success)

    assert result_success.status == "COMPLETED"
    assert result_success.output_bytes == b"[TRANSIENT_INTEG_SUCCESS]"

    # Verify recovery manager recorded checkpoints on both stages
    checkpoints = await document_engine.recovery_manager.get_checkpoints("req-integ-rec-1")
    assert len(checkpoints) == 2
    assert checkpoints[0].stage_id == "stage-gen"
    assert checkpoints[1].stage_id == "stage-trans"

    # Verify failure telemetry recorded transient failure
    failures = await document_engine.recovery_manager.get_failures("req-integ-rec-1")
    assert len(failures) == 1
    assert failures[0].stage_id == "stage-trans"

    # 2. Terminal failure and rollback
    failing_adapter = TransientIntegrationAdapter(adapter_id="kortex.adapter.always_fail_integ", fail_count=10)
    document_engine.adapter_registry.register_adapter(failing_adapter)

    prof_id_fail = f"prof-rec-fail-{uuid4()}"
    pipeline_fail = AdapterPipelineDefinition(
        pipeline_id=f"pipe-{prof_id_fail}",
        profile_id=prof_id_fail,
        stages=[
            PipelineStage(
                stage_id="stage-gen-ok",
                adapter_id="kortex.adapter.integration_pdf",
                required_capability=AdapterCapability.GENERATE,
            ),
            PipelineStage(
                stage_id="stage-fail-term",
                adapter_id="kortex.adapter.always_fail_integ",
                required_capability=AdapterCapability.TRANSFORM,
            ),
        ],
    )
    profile_fail = DocumentOperationProfile(
        id=prof_id_fail,
        name="Recovery Fail Profile",
        namespace="kortex.integ",
        version="1.0.0",
        description="Profile for testing rollback on retry exhaustion",
        business_operation="RECOVERY_FAIL_OP",
        adapter_pipeline=pipeline_fail,
    )
    await document_engine.profile_manager.register_profile(profile_fail)

    req_fail = OperationRequest(
        request_id="req-integ-rec-fail",
        profile_id=prof_id_fail,
        business_operation="RECOVERY_FAIL_OP",
        binding_context=BindingContext(context_id="ctx-integ-rec-fail"),
    )
    result_fail = await document_engine.execute_profile(prof_id_fail, req_fail)

    assert result_fail.status == "FAILED"
    assert len(result_fail.errors) > 0

    # 3 failures recorded
    failures_term = await document_engine.recovery_manager.get_failures("req-integ-rec-fail")
    assert len(failures_term) == 3

    # Checkpoints were rolled back and cleared
    checkpoints_term = await document_engine.recovery_manager.get_checkpoints("req-integ-rec-fail")
    assert len(checkpoints_term) == 0

    await kernel.shutdown()


# =============================================================================
# Milestone 7: Storage Integration
# =============================================================================

@pytest.mark.asyncio
async def test_storage_binder_and_cache_stores_auto_wired_on_kernel_boot(tmp_path) -> None:
    """Milestone 7: Storage Binder Integration.

    A real Kernel boot must populate DocumentStorageBinder's four stores and every
    subsystem's optional cache_store dependency (recovery_manager, template_library,
    lifecycle_manager, adapter_registry) without any manual post-boot wiring, sharing the
    same underlying ICacheStore instance resolved from the Storage Engine.
    """
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_m7_wiring"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    binder = document_engine.storage_binder
    assert binder.data_store is not None
    assert binder.file_store is not None
    assert binder.object_store is not None
    assert binder.cache_store is not None

    assert document_engine.recovery_manager.cache_store is binder.cache_store
    assert document_engine.template_library.cache_store is binder.cache_store
    assert document_engine.lifecycle_manager.cache_store is binder.cache_store
    assert document_engine.adapter_registry.cache_store is binder.cache_store

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_execute_profile_persists_output_to_object_store_tenant_scoped(tmp_path) -> None:
    """Milestone 7: Object Storage Output Persistence.

    execute_profile must additively persist the generated output through the auto-wired
    storage_binder when profile.output_bucket is set, keyed with a tenant-scoped object key,
    while OperationResult.output_bytes remains exactly the same raw bytes as before this
    milestone's change (never replaced by a storage reference).
    """
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_m7_object"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    adapter = IntegrationDummyAdapter(adapter_id="kortex.adapter.m7_object_test")
    document_engine.adapter_registry.register_adapter(adapter)

    profile_id = f"profile.m7.object.{uuid4()}"
    pipeline_def = AdapterPipelineDefinition(
        pipeline_id=f"pipe-{profile_id}",
        profile_id=profile_id,
        stages=[
            PipelineStage(
                stage_id="stage-gen",
                adapter_id="kortex.adapter.m7_object_test",
                required_capability=AdapterCapability.GENERATE,
            )
        ],
    )
    profile = DocumentOperationProfile(
        id=profile_id,
        name="M7 Object Persistence Profile",
        namespace="kortex.m7",
        version="1.0.0",
        description="Profile exercising Object Storage Output Persistence",
        business_operation="M7_OBJECT_TEST",
        adapter_pipeline=pipeline_def,
        output_bucket="m7-outputs",
    )
    tenant_id = "tenant-m7-object"
    await document_engine.profile_manager.register_profile(profile, tenant_id=tenant_id)

    context = BindingContext(context_id="ctx-m7-object", tenant_id=tenant_id)
    request = OperationRequest(
        request_id="req-m7-object-1",
        profile_id=profile_id,
        binding_context=context,
    )

    result = await document_engine.execute_profile(profile_id, request)

    assert result.status == "COMPLETED"
    assert result.output_bytes is not None
    assert b"[INTEGRATION_PDF_OUTPUT]" in result.output_bytes

    expected_key = f"{tenant_id}/{profile_id}/req-m7-object-1"
    assert await storage_engine.object.object_exists("m7-outputs", expected_key) is True
    stored_bytes = await storage_engine.object.get_object("m7-outputs", expected_key)
    assert stored_bytes == result.output_bytes

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_publish_then_verify_sha256_integrity_flow(tmp_path) -> None:
    """Milestone 7: Security Verification Integration.

    Publishing with a payload through the real, repository-backed
    DocumentEngine.transition_lifecycle() must populate DocumentMetadata.sha256_hash via the
    real IVerificationService, and that hash must verify correctly (and reject tampering)
    through the real DocumentSecurityVerifier.
    """
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_m7_hash"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    payload = b"[PUBLISHED_DOCUMENT_BYTES_FOR_HASH_VERIFICATION]"

    version = await document_engine.lifecycle_manager.create_version(
        title="Hash Verification Document", author_id="user-hash"
    )
    published_meta = await document_engine.transition_lifecycle(
        version.document_id,
        version.version_id,
        DocumentLifecycleState.PUBLISHED,
        payload=payload,
    )

    assert published_meta.sha256_hash is not None
    assert len(published_meta.sha256_hash) == 64

    is_valid = await document_engine.security_verifier.verify_document_integrity(
        payload, published_meta.sha256_hash
    )
    assert is_valid is True

    tampered_payload = payload + b"TAMPERED"
    is_invalid = await document_engine.security_verifier.verify_document_integrity(
        tampered_payload, published_meta.sha256_hash
    )
    assert is_invalid is False

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_recovery_checkpoint_persists_via_cache_store_across_fresh_manager(tmp_path) -> None:
    """Milestone 7: Recovery Checkpoint Persistence.

    A checkpoint written through the booted engine's recovery_manager must be observable by a
    completely independent DocumentRecoveryManager instance sharing only the same underlying
    ICacheStore resolved from the real Storage Engine, mirroring the established
    TemplateLibrary/DocumentLifecycleManager fresh-session persistence pattern. Note: the
    concrete ICacheStore is in-process (MemoryCacheStore), so this proves cross-instance
    resumability within the running process — not crash-durability across a process restart,
    which is explicitly out of scope for this milestone's ICacheStore-based design.
    """
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_m7_recovery"))
    document_engine = DocumentEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(document_engine)

    await kernel.boot()

    assert document_engine.recovery_manager.cache_store is not None
    await document_engine.recovery_manager.checkpoint("req-m7-recovery", "stage-1", b"[STATE]")

    shared_cache_store = document_engine.recovery_manager.cache_store
    fresh_recovery = DocumentRecoveryManager(cache_store=shared_cache_store)

    checkpoints = await fresh_recovery.get_checkpoints("req-m7-recovery")
    assert len(checkpoints) == 1
    assert checkpoints[0].stage_id == "stage-1"

    await kernel.shutdown()
