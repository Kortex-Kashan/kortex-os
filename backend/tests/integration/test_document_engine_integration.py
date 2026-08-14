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
from typing import Any
from uuid import uuid4

import pytest

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
from kortex.engines.document.lifecycle import DocumentLifecycleManager
from kortex.engines.document.persistence import DocumentRepository, TemplateRepository
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

    # Invoke capability handler directly through CapabilityDescriptor
    adapters_list = cap_list.handler()
    assert isinstance(adapters_list, list)

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

    # 2. Define pipeline
    pipeline_def = AdapterPipelineDefinition(
        pipeline_id="pipe-e2e",
        profile_id="profile.e2e.payslip",
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
        id="profile.e2e.payslip",
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
        profile_id="profile.e2e.payslip",
        binding_context=context,
    )

    result = await document_engine.execute_profile("profile.e2e.payslip", request)

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

    # 8. Verify system event publication
    await asyncio.sleep(0.05)
    assert len(document_engine.emitted_events) >= 3

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
    profile = DocumentOperationProfile(
        id="profile.evt.test",
        name="Event Test Profile",
        namespace="kortex.evt",
        version="1.0.0",
        description="Event test profile",
        business_operation="EVT_OP",
    )
    await document_engine.profile_manager.register_profile(profile)

    req = OperationRequest(
        request_id="req-evt-test-1",
        profile_id="profile.evt.test",
        business_operation="EVT_OP",
        binding_context=BindingContext(context_id="ctx-evt"),
    )
    await document_engine.execute_profile("profile.evt.test", req)

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


def test_architecture_compliance_assertions() -> None:
    """10. Architecture Compliance assertions."""
    engine = DocumentEngine()

    # Verify sandbox is bound
    assert engine.sandbox is not None
    assert engine.pipeline_executor.sandbox is engine.sandbox

    # Verify local offline execution
    assert engine.diagnostics()["version"] == "1.0.0"
    assert engine.status() == "UNINITIALIZED"
