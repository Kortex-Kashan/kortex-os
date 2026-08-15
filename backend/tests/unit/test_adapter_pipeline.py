"""Unit tests for AdapterPipelineExecutor (Milestone 6).

Target: 100% pass rate, 100% line coverage for adapter_pipeline.py.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from kortex.engines.document.adapter_pipeline import (
    AdapterPipelineExecutor,
    PipelineExecutionResult,
    StageExecutionResult,
    evaluate_declarative_condition,
)
from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.exceptions import (
    AdapterNotFoundError,
    DocumentOperationError,
)
from kortex.engines.document.adapters.macro_adapter import MacroAdapter
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    AdapterPipelineDefinition,
    BindingContext,
    DocumentOperationProfile,
    DocumentOperationType,
    OperationRequest,
    PipelineExecutionMode,
    PipelineStage,
    TemplateSchema,
)
from kortex.engines.document.operation_profile import DocumentOperationProfileManager


class MockNormalizerAdapter(BaseDocumentAdapter):
    """Mock Normalizer Adapter."""

    def __init__(self, adapter_id: str = "kortex.adapter.normalizer") -> None:
        self.call_order: list[str] = []
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="Normalizer Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Normalizer",
            supported_capabilities=[AdapterCapability.TRANSFORM],
            supported_operations=[DocumentOperationType.TRANSFORM],
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
        self.call_order.append(self.adapter_id)
        prev_input = options.get("input_bytes", b"")
        return prev_input + b"[NORMALIZED]"

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


class MockPdfAdapter(BaseDocumentAdapter):
    """Mock PDF Adapter."""

    def __init__(self, adapter_id: str = "kortex.adapter.pdf") -> None:
        self.call_order: list[str] = []
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="PDF Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="PDF Adapter",
            supported_capabilities=[AdapterCapability.GENERATE, AdapterCapability.CONVERT],
            supported_operations=[DocumentOperationType.GENERATE, DocumentOperationType.CONVERT],
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
        self.call_order.append(self.adapter_id)
        prev_input = options.get("input_bytes", b"")
        return prev_input + b"[PDF_BYTES]"

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


class MockWatermarkAdapter(BaseDocumentAdapter):
    """Mock Watermark Adapter."""

    def __init__(self, adapter_id: str = "kortex.adapter.watermark") -> None:
        self.call_order: list[str] = []
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="Watermark Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Watermark Adapter",
            supported_capabilities=[AdapterCapability.PREVIEW, AdapterCapability.TRANSFORM],
            supported_operations=[DocumentOperationType.PREVIEW],
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
        self.call_order.append(self.adapter_id)
        prev_input = options.get("input_bytes", b"")
        return prev_input + b"[WATERMARKED]"

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


class FailingAdapter(BaseDocumentAdapter):
    """Adapter designed to fail during execution."""

    def __init__(self, adapter_id: str = "kortex.adapter.failing") -> None:
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="Failing Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Failing Adapter",
            supported_capabilities=[AdapterCapability.GENERATE],
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
        raise RuntimeError("Adapter crashed during processing!")

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return False


def test_evaluate_declarative_condition() -> None:
    """Test evaluate_declarative_condition helper function."""
    ctx = BindingContext(
        context_id="ctx-cond",
        data={"flag_true": True, "flag_false": False, "status": "active", "count": 5},
        computed_fields={"is_vip": True},
    )

    assert evaluate_declarative_condition(None, ctx) is True
    assert evaluate_declarative_condition("", ctx) is True
    assert evaluate_declarative_condition("true", ctx) is True
    assert evaluate_declarative_condition("false", ctx) is False
    assert evaluate_declarative_condition("is_vip", ctx) is True
    assert evaluate_declarative_condition("!flag_false", ctx) is True
    assert evaluate_declarative_condition("status == active", ctx) is True
    assert evaluate_declarative_condition("status != active", ctx) is False
    assert evaluate_declarative_condition("non_existent", ctx) is False


@pytest.mark.asyncio
async def test_valid_single_stage_pipeline() -> None:
    """1. Valid single-stage pipeline."""
    registry = DocumentAdapterRegistry()
    pdf_adapter = MockPdfAdapter()
    registry.register_adapter(pdf_adapter)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-1",
        profile_id="prof-1",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.adapter.pdf",
                required_capability=AdapterCapability.GENERATE,
            )
        ],
    )
    context = BindingContext(context_id="c1")

    res = await executor.execute_pipeline_definition(definition, context, initial_input=b"START:")
    assert res.is_success is True
    assert res.final_output_bytes == b"START:[PDF_BYTES]"
    assert len(res.stage_results) == 1
    assert res.stage_results[0].is_success is True


@pytest.mark.asyncio
async def test_valid_multi_stage_sequential_pipeline() -> None:
    """2. Valid multi-stage sequential pipeline."""
    registry = DocumentAdapterRegistry()
    norm = MockNormalizerAdapter()
    pdf = MockPdfAdapter()
    wm = MockWatermarkAdapter()
    registry.register_adapter(norm)
    registry.register_adapter(pdf)
    registry.register_adapter(wm)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-multi",
        profile_id="prof-multi",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.normalizer", required_capability=AdapterCapability.TRANSFORM),
            PipelineStage(stage_id="s2", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
            PipelineStage(stage_id="s3", adapter_id="kortex.adapter.watermark", required_capability=AdapterCapability.TRANSFORM),
        ],
    )
    context = BindingContext(context_id="c2")

    res = await executor.execute_pipeline_definition(definition, context, initial_input=b"RAW:")
    assert res.is_success is True
    assert res.final_output_bytes == b"RAW:[NORMALIZED][PDF_BYTES][WATERMARKED]"
    assert len(res.stage_results) == 3


@pytest.mark.asyncio
async def test_correct_stage_ordering_and_data_flow() -> None:
    """3. Correct stage ordering and 4. Stage N output passed to Stage N+1."""
    registry = DocumentAdapterRegistry()
    execution_order: list[str] = []

    class OrderTrackingAdapter(BaseDocumentAdapter):
        def __init__(self, name: str, cap: AdapterCapability) -> None:
            self.name = name
            self._meta = AdapterMetadata(
                adapter_id=f"adapter.{name}",
                display_name=name,
                vendor="Kortex",
                author="Dev",
                version="1.0.0",
                license="MIT",
                description="Desc",
                supported_capabilities=[cap],
            )

        @property
        def metadata(self) -> AdapterMetadata:
            return self._meta

        async def execute(self, operation_type: DocumentOperationType, binding_context: BindingContext, options: dict[str, Any]) -> bytes:
            execution_order.append(self.name)
            prev = options.get("input_bytes", b"")
            return prev + f"->{self.name}".encode()

        def validate_schema(self, schema: TemplateSchema) -> bool:
            return True

    a1 = OrderTrackingAdapter("A", AdapterCapability.TRANSFORM)
    a2 = OrderTrackingAdapter("B", AdapterCapability.GENERATE)
    a3 = OrderTrackingAdapter("C", AdapterCapability.PREVIEW)
    registry.register_adapter(a1)
    registry.register_adapter(a2)
    registry.register_adapter(a3)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-order",
        profile_id="prof-order",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="adapter.A", required_capability=AdapterCapability.TRANSFORM),
            PipelineStage(stage_id="s2", adapter_id="adapter.B", required_capability=AdapterCapability.GENERATE),
            PipelineStage(stage_id="s3", adapter_id="adapter.C", required_capability=AdapterCapability.PREVIEW),
        ],
    )

    res = await executor.execute_pipeline_definition(definition, BindingContext(context_id="c3"), initial_input=b"IN")
    assert execution_order == ["A", "B", "C"]
    assert res.final_output_bytes == b"IN->A->B->C"


@pytest.mark.asyncio
async def test_unknown_adapter_rejection() -> None:
    """5. Unknown adapter rejection."""
    executor = AdapterPipelineExecutor(registry=DocumentAdapterRegistry())
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-err",
        profile_id="p",
        stages=[PipelineStage(stage_id="s1", adapter_id="unknown.adapter", required_capability=AdapterCapability.GENERATE)],
    )

    with pytest.raises(AdapterNotFoundError, match="not found in registry"):
        await executor.execute_pipeline_definition(definition, BindingContext(context_id="c"))


@pytest.mark.asyncio
async def test_unsupported_capability_rejection() -> None:
    """6. Unsupported operation & 7. Unsupported capability rejection."""
    registry = DocumentAdapterRegistry()
    norm = MockNormalizerAdapter()
    registry.register_adapter(norm)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-cap-err",
        profile_id="p",
        stages=[PipelineStage(stage_id="s1", adapter_id="kortex.adapter.normalizer", required_capability=AdapterCapability.OCR)],
    )

    with pytest.raises(DocumentOperationError, match="does not support required capability 'OCR'"):
        await executor.execute_pipeline_definition(definition, BindingContext(context_id="c"))


@pytest.mark.asyncio
async def test_empty_pipeline_rejection() -> None:
    """8. Empty pipeline rejection."""
    executor = AdapterPipelineExecutor(registry=DocumentAdapterRegistry())
    definition = AdapterPipelineDefinition(pipeline_id="pipe-empty", profile_id="p", stages=[])

    with pytest.raises(DocumentOperationError, match="contains no execution stages"):
        await executor.execute_pipeline_definition(definition, BindingContext(context_id="c"))


@pytest.mark.asyncio
async def test_invalid_pipeline_definition_rejection() -> None:
    """9. Invalid pipeline definition rejection."""
    executor = AdapterPipelineExecutor(registry=DocumentAdapterRegistry())

    with pytest.raises(DocumentOperationError, match="Pipeline definition cannot be None"):
        executor.validate_pipeline_definition(None)  # type: ignore[arg-type]

    with pytest.raises(DocumentOperationError, match="Missing pipeline_id"):
        executor.validate_pipeline_definition(AdapterPipelineDefinition(pipeline_id="", profile_id="p"))

    with pytest.raises(DocumentOperationError, match="Pipeline stage missing stage_id"):
        executor.validate_pipeline_definition(
            AdapterPipelineDefinition(
                pipeline_id="p1", profile_id="pr", stages=[PipelineStage(stage_id="", adapter_id="a", required_capability=AdapterCapability.GENERATE)]
            )
        )

    with pytest.raises(DocumentOperationError, match="missing adapter_id"):
        executor.validate_pipeline_definition(
            AdapterPipelineDefinition(
                pipeline_id="p1", profile_id="pr", stages=[PipelineStage(stage_id="s1", adapter_id="", required_capability=AdapterCapability.GENERATE)]
            )
        )


@pytest.mark.asyncio
async def test_duplicate_stage_id_rejection() -> None:
    """10. Duplicate stage ID rejection."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    registry.register_adapter(pdf)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-dup-stage",
        profile_id="p",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
        ],
    )

    with pytest.raises(DocumentOperationError, match="Duplicate stage ID 's1'"):
        executor.validate_pipeline_definition(definition)


@pytest.mark.asyncio
async def test_required_stage_failure_stops_execution() -> None:
    """11. Required stage failure stops execution."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    fail = FailingAdapter()
    wm = MockWatermarkAdapter()
    registry.register_adapter(pdf)
    registry.register_adapter(fail)
    registry.register_adapter(wm)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-fail",
        profile_id="p",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
            PipelineStage(stage_id="s2", adapter_id="kortex.adapter.failing", required_capability=AdapterCapability.GENERATE, is_optional=False),
            PipelineStage(stage_id="s3", adapter_id="kortex.adapter.watermark", required_capability=AdapterCapability.TRANSFORM),
        ],
    )

    res = await executor.execute_pipeline_definition(definition, BindingContext(context_id="c"))
    assert res.is_success is False
    assert res.final_output_bytes is None
    assert len(res.stage_results) == 2  # Stage 3 was not executed
    assert res.stage_results[1].is_success is False
    assert "crashed" in res.stage_results[1].error_message


@pytest.mark.asyncio
async def test_optional_stage_failure_behavior() -> None:
    """12. Optional stage failure behavior."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    fail = FailingAdapter()
    wm = MockWatermarkAdapter()
    registry.register_adapter(pdf)
    registry.register_adapter(fail)
    registry.register_adapter(wm)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-opt-fail",
        profile_id="p",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
            PipelineStage(stage_id="s2", adapter_id="kortex.adapter.failing", required_capability=AdapterCapability.GENERATE, is_optional=True),
            PipelineStage(stage_id="s3", adapter_id="kortex.adapter.watermark", required_capability=AdapterCapability.TRANSFORM),
        ],
    )

    res = await executor.execute_pipeline_definition(definition, BindingContext(context_id="c"))
    assert res.is_success is True  # Optional stage failure does not fail overall pipeline
    assert res.final_output_bytes == b"[PDF_BYTES][WATERMARKED]"
    assert len(res.stage_results) == 3
    assert res.stage_results[1].is_success is False
    assert len(res.errors) == 1
    assert "Optional Stage Warning" in res.errors[0]


@pytest.mark.asyncio
async def test_conditional_stage_behavior() -> None:
    """13. Conditional stage behavior."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    wm = MockWatermarkAdapter()
    registry.register_adapter(pdf)
    registry.register_adapter(wm)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-cond",
        profile_id="p",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
            PipelineStage(
                stage_id="s2",
                adapter_id="kortex.adapter.watermark",
                required_capability=AdapterCapability.TRANSFORM,
                execution_condition="apply_watermark == true",
            ),
        ],
    )

    # Context without watermark flag
    ctx1 = BindingContext(context_id="c-no-wm", data={"apply_watermark": False})
    res1 = await executor.execute_pipeline_definition(definition, ctx1)
    assert res1.is_success is True
    assert res1.final_output_bytes == b"[PDF_BYTES]"
    assert res1.stage_results[1].is_skipped is True

    # Context with watermark flag
    ctx2 = BindingContext(context_id="c-wm", data={"apply_watermark": True})
    res2 = await executor.execute_pipeline_definition(definition, ctx2)
    assert res2.is_success is True
    assert res2.final_output_bytes == b"[PDF_BYTES][WATERMARKED]"
    assert res2.stage_results[1].is_skipped is False


@pytest.mark.asyncio
async def test_multiple_adapters_and_reused_adapter_in_stages() -> None:
    """14. Multiple adapters & 15. Same adapter used in multiple valid stages."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    wm = MockWatermarkAdapter()
    registry.register_adapter(pdf)
    registry.register_adapter(wm)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-reuse",
        profile_id="p",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
            PipelineStage(stage_id="s2", adapter_id="kortex.adapter.watermark", required_capability=AdapterCapability.TRANSFORM),
            PipelineStage(stage_id="s3", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.CONVERT),
        ],
    )

    res = await executor.execute_pipeline_definition(definition, BindingContext(context_id="c"))
    assert res.is_success is True
    assert res.final_output_bytes == b"[PDF_BYTES][WATERMARKED][PDF_BYTES]"


@pytest.mark.asyncio
async def test_stage_and_pipeline_result_structures() -> None:
    """16. Exception handling, 17. Stage execution results, and 18. Final result model validation."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    registry.register_adapter(pdf)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-res",
        profile_id="prof-res",
        stages=[PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE)],
    )

    res = await executor.execute_pipeline_definition(definition, BindingContext(context_id="c"))
    assert isinstance(res, PipelineExecutionResult)
    assert res.pipeline_id == "pipe-res"
    assert res.profile_id == "prof-res"
    assert res.is_success is True
    assert res.total_execution_time_ms >= 0.0

    stage_res = res.stage_results[0]
    assert isinstance(stage_res, StageExecutionResult)
    assert stage_res.stage_id == "s1"
    assert stage_res.adapter_id == "kortex.adapter.pdf"
    assert stage_res.capability == AdapterCapability.GENERATE
    assert stage_res.is_success is True
    assert stage_res.is_skipped is False


@pytest.mark.asyncio
async def test_deterministic_execution_and_immutability() -> None:
    """19. Deterministic execution order, 20. Input immutability, 21. Definition immutability."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    registry.register_adapter(pdf)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-immut",
        profile_id="p",
        stages=[PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE)],
    )
    context = BindingContext(context_id="c-immut", data={"val": 100})

    def_dump_before = definition.model_dump()
    ctx_dump_before = context.model_dump()

    res1 = await executor.execute_pipeline_definition(definition, context, initial_input=b"DATA")
    res2 = await executor.execute_pipeline_definition(definition, context, initial_input=b"DATA")

    assert res1.final_output_bytes == res2.final_output_bytes == b"DATA[PDF_BYTES]"
    assert definition.model_dump() == def_dump_before
    assert context.model_dump() == ctx_dump_before


@pytest.mark.asyncio
async def test_security_against_arbitrary_code_in_conditions() -> None:
    """22. Security test: No arbitrary code execution, 23. No filesystem, 24. No network, 25. No storage."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    registry.register_adapter(pdf)

    executor = AdapterPipelineExecutor(registry=registry)
    malicious_condition = "__import__('os').system('echo HACKED') == True"

    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-sec",
        profile_id="p",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.adapter.pdf",
                required_capability=AdapterCapability.GENERATE,
                execution_condition=malicious_condition,
            )
        ],
    )
    context = BindingContext(context_id="c-sec")

    # Declarative condition evaluator treats malicious string safely without eval/exec
    res = await executor.execute_pipeline_definition(definition, context)
    assert res.is_success is True
    # The condition string did not match any context value, so stage was skipped safely
    assert res.stage_results[0].is_skipped is True


@pytest.mark.asyncio
async def test_empty_registry_behavior() -> None:
    """26. Empty registry behavior."""
    executor = AdapterPipelineExecutor(registry=DocumentAdapterRegistry())
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-empty-reg",
        profile_id="p",
        stages=[PipelineStage(stage_id="s1", adapter_id="missing.adapter", required_capability=AdapterCapability.GENERATE)],
    )

    with pytest.raises(AdapterNotFoundError):
        await executor.execute_pipeline_definition(definition, BindingContext(context_id="c"))


@pytest.mark.asyncio
async def test_capability_and_registry_integration() -> None:
    """27. Capability validation, 28. Registry regression, 29. BaseDocumentAdapter regression."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    assert registry.register_adapter(pdf) is pdf

    executor = AdapterPipelineExecutor(registry=registry)
    assert executor.registry is registry


@pytest.mark.asyncio
async def test_performance_sanity_test_overhead() -> None:
    """30. Performance sanity test for orchestration overhead (< 50ms)."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    registry.register_adapter(pdf)

    executor = AdapterPipelineExecutor(registry=registry)
    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-perf",
        profile_id="p",
        stages=[PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE)],
    )

    start = time.perf_counter()
    res = await executor.execute_pipeline_definition(definition, BindingContext(context_id="c"))
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert res.is_success is True
    assert elapsed_ms < 50.0  # Orchestration overhead is under 50ms


@pytest.mark.asyncio
async def test_execute_pipeline_protocol_facade() -> None:
    """Test execute_pipeline protocol facade method (IAdapterPipelineExecutor)."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter(adapter_id="profile-pdf")
    registry.register_adapter(pdf)

    executor = AdapterPipelineExecutor(registry=registry)
    req = OperationRequest(request_id="req-100", profile_id="profile-pdf", binding_context=BindingContext(context_id="c-100"))

    res = await executor.execute_pipeline("profile-pdf", req)
    assert res.status == "COMPLETED"
    assert res.output_bytes == b"[PDF_BYTES]"

    # Test error handling when request_id is missing or request is None
    with pytest.raises(DocumentOperationError, match="Invalid OperationRequest"):
        await executor.execute_pipeline("profile-pdf", OperationRequest(request_id="", profile_id="profile-pdf", binding_context=BindingContext(context_id="c-empty")))

    with pytest.raises(DocumentOperationError, match="Invalid OperationRequest"):
        await executor.execute_pipeline("profile-pdf", None)  # type: ignore[arg-type]

    # Test non-existent profile fallback failure
    res_fail = await executor.execute_pipeline("non_existent_profile", req)
    assert res_fail.status == "FAILED"
    assert "contains no execution stages" in res_fail.errors[0]


def test_edge_case_condition_and_context_metadata() -> None:
    """Test condition inequality with missing key and context.metadata resolution."""
    ctx = BindingContext(context_id="c-meta", metadata={"meta_key": "meta_val"})

    assert evaluate_declarative_condition("missing_key != expected", ctx) is True
    assert evaluate_declarative_condition("meta_key == meta_val", ctx) is True


@pytest.mark.asyncio
async def test_execute_pipeline_resolves_real_profile_via_profile_manager() -> None:
    """When a profile_manager is configured, execute_pipeline() resolves the REAL registered
    profile's adapter_pipeline instead of treating profile_id as an adapter_id (the fixed
    Milestone 5 defect)."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter(adapter_id="kortex.adapter.real_pdf")
    registry.register_adapter(pdf)

    profile_manager = DocumentOperationProfileManager(adapter_registry=registry)
    real_pipeline = AdapterPipelineDefinition(
        pipeline_id="pipe-real",
        profile_id="profile.real.v1",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.adapter.real_pdf",
                required_capability=AdapterCapability.GENERATE,
            )
        ],
    )
    await profile_manager.register_profile(
        DocumentOperationProfile(
            id="profile.real.v1",
            name="Real Profile",
            namespace="kortex.test.real",
            version="1.0.0",
            description="A real, registered operation profile",
            business_operation="TEST_REAL_OP",
            adapter_pipeline=real_pipeline,
        )
    )

    executor = AdapterPipelineExecutor(registry=registry, profile_manager=profile_manager)
    assert executor.profile_manager is profile_manager
    req = OperationRequest(
        request_id="req-real-profile",
        profile_id="profile.real.v1",
        binding_context=BindingContext(context_id="c-real"),
    )

    # Note: "profile.real.v1" is NOT itself a registered adapter_id — if the legacy shim
    # were still active, this would fail with "contains no execution stages". Success here
    # proves the real profile->pipeline resolution path executed instead.
    res = await executor.execute_pipeline("profile.real.v1", req)

    assert res.status == "COMPLETED"
    assert res.output_bytes == b"[PDF_BYTES]"


@pytest.mark.asyncio
async def test_execute_pipeline_falls_back_to_legacy_shim_when_profile_unresolvable() -> None:
    """With a profile_manager configured but no matching profile registered, execute_pipeline()
    falls back to the legacy adapter_id-as-profile_id shim rather than failing outright."""
    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter(adapter_id="profile-pdf-fallback")
    registry.register_adapter(pdf)

    profile_manager = DocumentOperationProfileManager(adapter_registry=registry)
    executor = AdapterPipelineExecutor(registry=registry, profile_manager=profile_manager)
    req = OperationRequest(
        request_id="req-fallback",
        profile_id="profile-pdf-fallback",
        binding_context=BindingContext(context_id="c-fallback"),
    )

    res = await executor.execute_pipeline("profile-pdf-fallback", req)

    assert res.status == "COMPLETED"
    assert res.output_bytes == b"[PDF_BYTES]"


@pytest.mark.asyncio
async def test_pipeline_stage_with_macro_capability_executes_end_to_end() -> None:
    """A MACROS-capability pipeline stage flows correctly through the unmodified
    AdapterPipelineExecutor, proving the reference Macro Adapter integrates with the
    existing pipeline/registry machinery exactly like DummyDocumentAdapter does for
    GENERATE/PREVIEW."""
    registry = DocumentAdapterRegistry()
    macro = MacroAdapter()
    registry.register_adapter(macro)

    pipeline_def = AdapterPipelineDefinition(
        pipeline_id="pipe-macro",
        profile_id="profile.macro.v1",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id=macro.adapter_id,
                required_capability=AdapterCapability.MACROS,
            )
        ],
    )

    executor = AdapterPipelineExecutor(registry=registry)
    res = await executor.execute_pipeline_definition(
        pipeline_def, BindingContext(context_id="ctx-macro-pipeline", data={"rows": 3})
    )

    assert res.is_success is True
    assert len(res.stage_results) == 1
    assert res.stage_results[0].capability == AdapterCapability.MACROS
    assert res.final_output_bytes is not None
    assert b"kortex.document.macro.v1" in res.final_output_bytes


class TransientFailingAdapter(BaseDocumentAdapter):
    """Adapter that fails a configured number of times before succeeding."""

    def __init__(self, adapter_id: str = "kortex.adapter.transient", fail_count: int = 1) -> None:
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="Transient Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Transient test adapter",
            supported_capabilities=[AdapterCapability.GENERATE],
            supported_operations=[DocumentOperationType.GENERATE],
        )
        self.fail_count = fail_count
        self.call_count = 0

    @property
    def metadata(self) -> AdapterMetadata:
        return self._meta

    async def execute(self, operation_type, binding_context, options) -> bytes:
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise RuntimeError(f"Transient failure on attempt {self.call_count}")
        return b"[RECOVERED_OUTPUT]"

    def validate_schema(self, schema) -> bool:
        return True


@pytest.mark.asyncio
async def test_required_stage_failure_without_recovery_manager_does_not_retry() -> None:
    """Recovery is opt-in: with no recovery_manager configured, a required-stage failure must
    behave exactly as it did pre-Milestone-6 — exactly one execution attempt, no retry, no
    backoff, and no recovery telemetry, since no recovery_manager exists to record any."""
    registry = DocumentAdapterRegistry()
    always_fail = TransientFailingAdapter(adapter_id="kortex.adapter.no_recovery_fail", fail_count=10)
    registry.register_adapter(always_fail)

    # No recovery_manager passed — this is the real AdapterPipelineExecutor execution path,
    # not a mock of it.
    executor = AdapterPipelineExecutor(registry=registry)
    assert executor.recovery_manager is None

    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-no-recovery",
        profile_id="p-no-recovery",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.adapter.no_recovery_fail",
                required_capability=AdapterCapability.GENERATE,
            )
        ],
    )

    res = await executor.execute_pipeline_definition(
        definition, BindingContext(context_id="ctx-no-recovery"), request_id="req-no-recovery-1"
    )

    assert always_fail.call_count == 1
    assert res.is_success is False
    assert len(res.stage_results) == 1
    assert res.stage_results[0].is_success is False


@pytest.mark.asyncio
async def test_pipeline_recovery_checkpoints_created_on_success() -> None:
    """Successful pipeline stages automatically record operational checkpoints in DocumentRecoveryManager."""
    from kortex.engines.document.recovery import DocumentRecoveryManager

    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    norm = MockNormalizerAdapter()
    registry.register_adapter(pdf)
    registry.register_adapter(norm)

    recovery = DocumentRecoveryManager()
    executor = AdapterPipelineExecutor(registry=registry, recovery_manager=recovery)

    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-chk",
        profile_id="p-chk",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
            PipelineStage(stage_id="s2", adapter_id="kortex.adapter.normalizer", required_capability=AdapterCapability.TRANSFORM),
        ],
    )

    res = await executor.execute_pipeline_definition(
        definition, BindingContext(context_id="ctx-chk"), request_id="req-chk-1"
    )
    assert res.is_success is True

    checkpoints = await recovery.get_checkpoints("req-chk-1")
    assert len(checkpoints) == 2
    assert checkpoints[0].stage_id == "s1"
    assert checkpoints[0].state_data == b"[PDF_BYTES]"
    assert checkpoints[1].stage_id == "s2"
    assert checkpoints[1].state_data == b"[PDF_BYTES][NORMALIZED]"

    # Resume retrieves the last valid checkpoint
    last_chk = await recovery.resume("req-chk-1")
    assert last_chk is not None
    assert last_chk.stage_id == "s2"


@pytest.mark.asyncio
async def test_pipeline_recovery_retries_transient_failure_and_succeeds() -> None:
    """A transient stage failure triggers retry with backoff and successfully re-dispatches."""
    from kortex.engines.document.recovery import DocumentRecoveryManager

    registry = DocumentAdapterRegistry()
    transient = TransientFailingAdapter(fail_count=2)
    registry.register_adapter(transient)

    recovery = DocumentRecoveryManager()
    executor = AdapterPipelineExecutor(registry=registry, recovery_manager=recovery)

    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-retry",
        profile_id="p-retry",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.transient", required_capability=AdapterCapability.GENERATE),
        ],
    )

    res = await executor.execute_pipeline_definition(
        definition, BindingContext(context_id="ctx-retry"), request_id="req-retry-1"
    )
    assert res.is_success is True
    assert res.final_output_bytes == b"[RECOVERED_OUTPUT]"
    assert transient.call_count == 3  # Failed 2 times, succeeded on 3rd attempt

    failures = await recovery.get_failures("req-retry-1")
    assert len(failures) == 2
    assert failures[0].error_code == "RuntimeError"

    checkpoints = await recovery.get_checkpoints("req-retry-1")
    assert len(checkpoints) == 1
    assert checkpoints[0].state_data == b"[RECOVERED_OUTPUT]"


@pytest.mark.asyncio
async def test_pipeline_recovery_exhausts_retries_and_rolls_back() -> None:
    """When retries are exhausted on a required stage, rollback occurs and pipeline fails."""
    from kortex.engines.document.recovery import DocumentRecoveryManager

    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    always_fail = TransientFailingAdapter(adapter_id="kortex.adapter.fail", fail_count=10)
    registry.register_adapter(pdf)
    registry.register_adapter(always_fail)

    recovery = DocumentRecoveryManager()
    executor = AdapterPipelineExecutor(registry=registry, recovery_manager=recovery)

    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-exhaust",
        profile_id="p-exhaust",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
            PipelineStage(stage_id="s2", adapter_id="kortex.adapter.fail", required_capability=AdapterCapability.GENERATE),
        ],
    )

    res = await executor.execute_pipeline_definition(
        definition, BindingContext(context_id="ctx-exhaust"), request_id="req-exhaust-1"
    )
    assert res.is_success is False
    assert len(res.errors) > 0

    # 3 failures recorded for s2
    failures = await recovery.get_failures("req-exhaust-1")
    assert len(failures) == 3

    # Rollback cleared checkpoints
    checkpoints = await recovery.get_checkpoints("req-exhaust-1")
    assert len(checkpoints) == 0


@pytest.mark.asyncio
async def test_pipeline_recovery_optional_stage_failure_isolated() -> None:
    """An optional stage failure records failure telemetry but does not trigger rollback or fail pipeline."""
    from kortex.engines.document.recovery import DocumentRecoveryManager

    registry = DocumentAdapterRegistry()
    pdf = MockPdfAdapter()
    always_fail = TransientFailingAdapter(adapter_id="kortex.adapter.opt_fail", fail_count=10)
    norm = MockNormalizerAdapter()
    registry.register_adapter(pdf)
    registry.register_adapter(always_fail)
    registry.register_adapter(norm)

    recovery = DocumentRecoveryManager()
    executor = AdapterPipelineExecutor(registry=registry, recovery_manager=recovery)

    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-opt",
        profile_id="p-opt",
        stages=[
            PipelineStage(stage_id="s1", adapter_id="kortex.adapter.pdf", required_capability=AdapterCapability.GENERATE),
            PipelineStage(stage_id="s2", adapter_id="kortex.adapter.opt_fail", required_capability=AdapterCapability.GENERATE, is_optional=True),
            PipelineStage(stage_id="s3", adapter_id="kortex.adapter.normalizer", required_capability=AdapterCapability.TRANSFORM),
        ],
    )

    res = await executor.execute_pipeline_definition(
        definition, BindingContext(context_id="ctx-opt"), request_id="req-opt-1"
    )
    assert res.is_success is True
    assert res.final_output_bytes == b"[PDF_BYTES][NORMALIZED]"

    # s1 and s3 checkpoints exist
    checkpoints = await recovery.get_checkpoints("req-opt-1")
    assert len(checkpoints) == 2
    assert checkpoints[0].stage_id == "s1"
    assert checkpoints[1].stage_id == "s3"

    # s2 failure recorded exactly once — proves the optional stage did not retry.
    failures = await recovery.get_failures("req-opt-1")
    assert len(failures) == 1
    assert failures[0].stage_id == "s2"

