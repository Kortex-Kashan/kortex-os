"""Unit tests for DocumentEngine Facade (Milestone 9).

Target: 100% pass rate, 100% line coverage for engine.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.document.exceptions import (
    DocumentOperationError,
    DocumentProfileNotFoundError,
    DocumentTemplateError,
)
from kortex.engines.document.interfaces import IDocumentEngine
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    AdapterPipelineDefinition,
    BindingContext,
    DocumentLifecycleState,
    DocumentOperationProfile,
    DocumentOperationType,
    OperationRequest,
    OperationResult,
    PipelineStage,
    PreviewOptions,
    PreviewResult,
    TemplateSchema,
)
from kortex.engines.document.security import DocumentStorageBinder


class DummyFacadeAdapter(BaseDocumentAdapter):
    """Dummy document adapter for testing facade pipeline execution."""

    def __init__(self, adapter_id: str = "kortex.adapter.facade_pdf") -> None:
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="Facade PDF Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Mock adapter for facade unit tests",
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
            raise RuntimeError("Dummy adapter crashed!")
        return b"[FACADE_PDF_BYTES]"

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


def create_facade_test_profile(
    profile_id: str = "profile.facade.test",
    required_template_id: str | None = None,
    pipeline: AdapterPipelineDefinition | None = None,
) -> DocumentOperationProfile:
    """Helper function to build valid DocumentOperationProfile for facade testing."""
    return DocumentOperationProfile(
        id=profile_id,
        name="Facade Test Profile",
        namespace="kortex.test.facade",
        version="1.0.0",
        description="Profile for testing DocumentEngine facade orchestration",
        business_operation="TEST_FACADE_OPERATION",
        required_template_id=required_template_id,
        adapter_pipeline=pipeline,
        permissions=["test:read"],
        output_bucket="test_bucket",
    )


@pytest.mark.asyncio
async def test_engine_construction_and_protocol_compatibility() -> None:
    """1. Engine construction with DI & 2. Protocol compatibility & 23-25. Independent DI state."""
    engine = DocumentEngine()

    assert isinstance(engine, IDocumentEngine)
    assert engine.lifecycle_manager is not None
    assert engine.template_library is not None
    assert engine.template_binder is not None
    assert engine.adapter_registry is not None
    assert engine.sandbox is not None
    assert engine.pipeline_executor is not None
    assert engine.profile_manager is not None

    # Verify custom dependency injection
    custom_reg = DocumentAdapterRegistry()
    engine_custom = DocumentEngine(adapter_registry=custom_reg)
    assert engine_custom.adapter_registry is custom_reg
    assert engine_custom.adapter_registry is not engine.adapter_registry


@pytest.mark.asyncio
async def test_successful_end_to_end_orchestration() -> None:
    """3. Profile resolution, 7. Pipeline delegation, 8. Sandbox enforcement, 9. End-to-end orchestration."""
    engine = DocumentEngine()

    adapter = DummyFacadeAdapter()
    engine.adapter_registry.register_adapter(adapter)

    pipeline_def = AdapterPipelineDefinition(
        pipeline_id="pipe-facade",
        profile_id="profile.facade.test",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.adapter.facade_pdf",
                required_capability=AdapterCapability.GENERATE,
            )
        ],
    )

    profile = create_facade_test_profile(required_template_id="payslip.declarative.v1", pipeline=pipeline_def)
    await engine.profile_manager.register_profile(profile)

    context = BindingContext(
        context_id="ctx-facade-1",
        data={
            "employee_id": "EMP-101",
            "employee_name": "Alice Smith",
            "basic_salary": 5000.0,
            "net_salary": 4500.0,
            "period": "2026-08",
        },
    )

    req = OperationRequest(
        request_id="req-facade-1",
        profile_id="profile.facade.test",
        binding_context=context,
    )

    res = await engine.execute_profile("profile.facade.test", req)

    assert isinstance(res, OperationResult)
    assert res.status == "COMPLETED"
    assert res.output_bytes == b"[FACADE_PDF_BYTES]"
    assert res.execution_time_ms >= 0.0
    assert len(res.errors) == 0


@pytest.mark.asyncio
async def test_execute_profile_validation_and_missing_profile() -> None:
    """4. Missing profile handling & Request validation."""
    engine = DocumentEngine()

    # Invalid request
    with pytest.raises(DocumentOperationError, match="Invalid OperationRequest"):
        await engine.execute_profile("missing.profile", None)  # type: ignore[arg-type]

    with pytest.raises(DocumentOperationError, match="request_id missing"):
        await engine.execute_profile(
            "missing.profile",
            OperationRequest(
                request_id="", profile_id="missing.profile", binding_context=BindingContext(context_id="c")
            ),
        )

    # Missing profile
    req = OperationRequest(
        request_id="req-missing",
        profile_id="missing.profile",
        binding_context=BindingContext(context_id="c"),
    )
    with pytest.raises(DocumentProfileNotFoundError):
        await engine.execute_profile("missing.profile", req)


@pytest.mark.asyncio
async def test_template_resolution_and_binding_delegation() -> None:
    """5. Template resolution delegation & 6. Template binding delegation & 12. Binding failure handling."""
    engine = DocumentEngine()

    # Direct bind_template call
    context_valid = BindingContext(
        context_id="c-bind",
        data={
            "employee_id": "EMP-200",
            "employee_name": "Bob",
            "basic_salary": 5000.0,
            "net_salary": 4000.0,
            "period": "2026-08",
        },
    )
    report = await engine.bind_template("payslip.declarative.v1", context_valid)
    assert report.is_valid is True

    # Failed template binding inside execute_profile
    profile = create_facade_test_profile(profile_id="p.tmpl.fail", required_template_id="payslip.declarative.v1")
    await engine.profile_manager.register_profile(profile)

    context_invalid = BindingContext(context_id="c-bind-fail", data={})  # missing required fields
    req = OperationRequest(
        request_id="req-bind-fail",
        profile_id="p.tmpl.fail",
        binding_context=context_invalid,
    )

    res = await engine.execute_profile("p.tmpl.fail", req)
    assert res.status == "FAILED"
    assert len(res.errors) > 0


@pytest.mark.asyncio
async def test_pipeline_without_pipeline_definition() -> None:
    """Test execute_profile when profile has no adapter_pipeline specified."""
    engine = DocumentEngine()
    profile = create_facade_test_profile(profile_id="p.no_pipeline")
    await engine.profile_manager.register_profile(profile)

    req = OperationRequest(
        request_id="req-no-pipe",
        profile_id="p.no_pipeline",
        binding_context=BindingContext(context_id="c"),
    )
    res = await engine.execute_profile("p.no_pipeline", req)
    assert res.status == "COMPLETED"
    assert res.output_bytes == b""


@pytest.mark.asyncio
async def test_pipeline_and_adapter_failure_propagation() -> None:
    """10. Pipeline failure propagation & 11. Adapter failure propagation."""
    engine = DocumentEngine()
    adapter = DummyFacadeAdapter()
    engine.adapter_registry.register_adapter(adapter)

    pipeline_def = AdapterPipelineDefinition(
        pipeline_id="pipe-crash",
        profile_id="p.crash",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.adapter.facade_pdf",
                required_capability=AdapterCapability.GENERATE,
                stage_options={"trigger_crash": True},
            )
        ],
    )
    profile = create_facade_test_profile(profile_id="p.crash", pipeline=pipeline_def)
    await engine.profile_manager.register_profile(profile)

    req = OperationRequest(
        request_id="req-crash",
        profile_id="p.crash",
        binding_context=BindingContext(context_id="c"),
    )

    res = await engine.execute_profile("p.crash", req)
    assert res.status == "FAILED"
    assert "Dummy adapter crashed" in res.errors[0]


@pytest.mark.asyncio
async def test_lifecycle_transition_delegation() -> None:
    """Lifecycle transition delegation test."""
    engine = DocumentEngine()

    version = await engine.lifecycle_manager.create_version(document_id="doc-100", title="Test Doc")
    assert version.document_id == "doc-100"

    updated = await engine.transition_lifecycle(
        document_id="doc-100",
        version_id=version.version_id,
        target_state=DocumentLifecycleState.REVIEW,
    )
    assert updated.lifecycle_state == DocumentLifecycleState.REVIEW


@pytest.mark.asyncio
async def test_preview_generation_and_unsupported_handling() -> None:
    """14. Preview delegation where supported & 15. Unsupported preview handling."""
    engine = DocumentEngine()

    # Unsupported preview handling (no preview adapter registered)
    res_fail = await engine.generate_preview("req-p1", PreviewOptions(page_number=1))
    assert res_fail.image_bytes is None

    # Invalid options
    res_none = await engine.generate_preview("", None)  # type: ignore[arg-type]
    assert res_none.image_bytes is None

    # Preview delegation with registered adapter
    adapter = DummyFacadeAdapter()
    engine.adapter_registry.register_adapter(adapter)

    res_success = await engine.generate_preview("req-p2", PreviewOptions(page_number=1))
    assert isinstance(res_success, PreviewResult)
    assert res_success.image_bytes == b"[FACADE_PDF_BYTES]"


def test_list_adapters_delegation() -> None:
    """Test list_adapters delegation."""
    engine = DocumentEngine()
    adapter = DummyFacadeAdapter()
    engine.adapter_registry.register_adapter(adapter)

    adapters = engine.list_adapters()
    assert len(adapters) == 1
    assert adapters[0].adapter_id == "kortex.adapter.facade_pdf"


@pytest.mark.asyncio
async def test_concurrent_engine_operations() -> None:
    """26. Concurrent operations test."""
    engine = DocumentEngine()
    adapter = DummyFacadeAdapter()
    engine.adapter_registry.register_adapter(adapter)

    profile = create_facade_test_profile(profile_id="p.conc")
    await engine.profile_manager.register_profile(profile)

    req1 = OperationRequest(request_id="r1", profile_id="p.conc", binding_context=BindingContext(context_id="c1"))
    req2 = OperationRequest(request_id="r2", profile_id="p.conc", binding_context=BindingContext(context_id="c2"))

    t1 = engine.execute_profile("p.conc", req1)
    t2 = engine.execute_profile("p.conc", req2)

    res1, res2 = await asyncio.gather(t1, t2)
    assert res1.status == "COMPLETED"
    assert res2.status == "COMPLETED"


def test_security_rules_no_direct_adapter_execution() -> None:
    """21-22. Sandbox enforcement & security rules."""
    engine = DocumentEngine()
    assert engine.sandbox is not None
    assert engine.pipeline_executor.sandbox is engine.sandbox


@pytest.mark.asyncio
async def test_initialize_registers_dummy_adapter_on_fresh_engine() -> None:
    """A freshly initialized engine (no kernel) has the Dummy Adapter registered and usable."""
    engine = DocumentEngine()

    await engine.initialize(kernel=None)

    adapters = engine.list_adapters()
    assert any(a.adapter_id == "kortex.document.dummy.v1" for a in adapters)

    # generate_preview() no longer raises AdapterNotFoundError on a freshly booted engine.
    result = await engine.generate_preview("req-dummy-preview", PreviewOptions(page_number=1))
    assert result.image_bytes is not None


@pytest.mark.asyncio
async def test_initialize_does_not_duplicate_manually_registered_adapter() -> None:
    """A manually pre-registered adapter is not clobbered or duplicated by initialize()."""
    engine = DocumentEngine()
    manual_adapter = DummyFacadeAdapter()
    engine.adapter_registry.register_adapter(manual_adapter)

    await engine.initialize(kernel=None)

    adapters = engine.list_adapters()
    adapter_ids = {a.adapter_id for a in adapters}
    assert manual_adapter.adapter_id in adapter_ids
    assert "kortex.document.dummy.v1" in adapter_ids
    fetched = engine.adapter_registry.get_adapter_by_id(manual_adapter.adapter_id)
    assert fetched is manual_adapter


@pytest.mark.asyncio
async def test_initialize_is_idempotent_when_called_twice() -> None:
    """Calling initialize() a second time does not raise or duplicate the Dummy Adapter."""
    engine = DocumentEngine()

    await engine.initialize(kernel=None)
    await engine.initialize(kernel=None)

    adapters = engine.list_adapters()
    dummy_matches = [a for a in adapters if a.adapter_id == "kortex.document.dummy.v1"]
    assert len(dummy_matches) == 1


# =============================================================================
# Milestone 9: Remaining Named Edge Cases (missing templates, storage errors)
# =============================================================================


class RaisingStorageBinder(DocumentStorageBinder):
    """DocumentStorageBinder whose store_document_output always raises.

    Used to force the storage-persistence failure path in execute_profile through the
    engine's existing constructor-level dependency injection seam (storage_binder=...),
    rather than monkeypatching engine internals.
    """

    async def store_document_output(
        self,
        bucket_name: str,
        object_key: str,
        data: bytes,
        mime_type: str = "application/pdf",
    ) -> Any:
        raise RuntimeError("Simulated Object Store failure")


@pytest.mark.asyncio
async def test_execute_profile_raises_for_missing_required_template() -> None:
    """M9 edge case: missing templates — execute_profile lets DocumentTemplateError from
    TemplateLibrary.get_template() propagate uncaught, consistent with the already-tested
    missing-profile behavior (DocumentProfileNotFoundError is likewise never converted to a
    FAILED result).

    Note: register_profile() (Milestone 5) validates required_template_id at registration
    time, so a profile can never be registered against a template that never existed. This
    test registers the template, registers the profile against it, then deletes the template
    before calling execute_profile — the only way to reach execute_profile's own
    template-resolution failure path.
    """
    engine = DocumentEngine()

    schema = TemplateSchema(
        template_id="tmpl.removed_after_registration",
        name="Removable Template",
        namespace="kortex.test.facade",
        version="1.0.0",
        description="Template removed after profile registration, to reach execute_profile's own missing-template path",
    )
    await engine.template_library.register_template(schema)

    profile = create_facade_test_profile(
        profile_id="p.missing_tmpl", required_template_id="tmpl.removed_after_registration"
    )
    await engine.profile_manager.register_profile(profile)

    await engine.template_library.delete_template("tmpl.removed_after_registration")

    req = OperationRequest(
        request_id="req-missing-tmpl",
        profile_id="p.missing_tmpl",
        binding_context=BindingContext(context_id="c-missing-tmpl"),
    )

    with pytest.raises(DocumentTemplateError):
        await engine.execute_profile("p.missing_tmpl", req)


@pytest.mark.asyncio
async def test_execute_profile_survives_storage_persistence_failure() -> None:
    """M9 edge case: storage errors — a failure in store_document_output during
    execute_profile's additive object-persistence step must be caught and logged, never flip
    an otherwise-successful operation to FAILED, and never alter output_bytes. Forced via the
    engine's existing storage_binder constructor injection point (RaisingStorageBinder).
    """
    engine = DocumentEngine(storage_binder=RaisingStorageBinder())
    adapter = DummyFacadeAdapter()
    engine.adapter_registry.register_adapter(adapter)

    pipeline_def = AdapterPipelineDefinition(
        pipeline_id="pipe-storage-fail",
        profile_id="p.storage_fail",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.adapter.facade_pdf",
                required_capability=AdapterCapability.GENERATE,
            )
        ],
    )
    # create_facade_test_profile defaults output_bucket to "test_bucket", so execute_profile's
    # object-persistence branch (is_completed and final_output_bytes and profile.output_bucket)
    # is genuinely reached and RaisingStorageBinder's exception is genuinely exercised.
    profile = create_facade_test_profile(profile_id="p.storage_fail", pipeline=pipeline_def)
    await engine.profile_manager.register_profile(profile)

    req = OperationRequest(
        request_id="req-storage-fail",
        profile_id="p.storage_fail",
        binding_context=BindingContext(context_id="c-storage-fail"),
    )

    res = await engine.execute_profile("p.storage_fail", req)

    assert res.status == "COMPLETED"
    assert res.output_bytes == b"[FACADE_PDF_BYTES]"
    assert len(res.errors) == 0
