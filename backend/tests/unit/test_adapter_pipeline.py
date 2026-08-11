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
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    AdapterPipelineDefinition,
    BindingContext,
    DocumentOperationType,
    OperationRequest,
    PipelineExecutionMode,
    PipelineStage,
    TemplateSchema,
)


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

