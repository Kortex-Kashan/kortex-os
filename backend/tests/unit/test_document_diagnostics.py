"""Unit tests for Document Engine Facade, Kernel Integration, Diagnostics, and System Events (Milestone 8).

Target: 100% pass rate, 100% line coverage for diagnostics.py, events.py, and engine.py.
"""

from __future__ import annotations

from typing import Any
import pytest

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.document.diagnostics import DocumentDiagnostics
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.document.events import (
    DocumentAdapterRegisteredEvent,
    DocumentArchivedEvent,
    DocumentCreatedEvent,
    DocumentIntelligenceUpdatedEvent,
    DocumentLifecycleTransitionedEvent,
    DocumentOperationCompletedEvent,
    DocumentOperationFailedEvent,
    DocumentOperationStartedEvent,
    DocumentPublishedEvent,
    DocumentSupersededEvent,
)
from kortex.engines.document.models import (
    BindingContext,
    DocumentLifecycleState,
    DocumentOperationProfile,
    OperationRequest,
)
from kortex.engines.storage.interfaces import IEngineDiagnostics


class MockKernel:
    """Mock Kernel for capability registration tests."""

    def __init__(self) -> None:
        self.registered_capabilities: dict[str, Any] = {}

    def register_capability(self, capability_name: str, handler: Any) -> None:
        self.registered_capabilities[capability_name] = handler


@pytest.mark.asyncio
async def test_document_diagnostics_standalone() -> None:
    """Test DocumentDiagnostics telemetry and IEngineDiagnostics protocol compliance."""
    diag = DocumentDiagnostics()
    assert isinstance(diag, IEngineDiagnostics)

    # Health check
    h = diag.health()
    assert h["status"] == "HEALTHY"
    assert h["engine"] == "document"
    assert "adapters_registered" in h

    # Initial metrics
    m = diag.metrics()
    assert m["total_operations_executed"] == 0
    assert m["success_rate_percentage"] == 100.0

    # Record operation attempts
    diag.record_operation_executed(is_success=True)
    diag.record_operation_executed(is_success=False)

    m2 = diag.metrics()
    assert m2["total_operations_executed"] == 2
    assert m2["failed_operations_count"] == 1
    assert m2["success_rate_percentage"] == 50.0

    # System diagnostics and status
    d = diag.diagnostics()
    assert d["engine"] == "document"
    assert d["version"] == "1.0.0"
    assert "capabilities" in d

    assert diag.status() == "READY"
    assert diag.version() == "1.0.0"
    assert len(diag.capabilities()) == 8
    assert "kortex.document.operation.execute" in diag.capabilities()


@pytest.mark.asyncio
async def test_document_engine_base_engine_lifecycle_and_diagnostics() -> None:
    """Test DocumentEngine inheriting BaseEngine, kernel capability registration, and diagnostics."""
    engine = DocumentEngine()
    assert isinstance(engine, BaseEngine)
    assert isinstance(engine, IEngineDiagnostics)

    # Engine properties
    assert engine.name == "document"
    assert engine.dependencies == ["storage"]
    assert engine.state == EngineState.UNINITIALIZED

    # Kernel initialization and capability registration
    kernel = MockKernel()
    await engine.initialize(kernel)
    assert engine.state == EngineState.READY
    assert len(kernel.registered_capabilities) == 8
    assert "kortex.document.operation.execute" in kernel.registered_capabilities

    # Start engine
    await engine.start()
    assert engine.state == EngineState.RUNNING

    # Health check and diagnostic delegations
    hc = await engine.health_check()
    assert hc["status"] == "HEALTHY"

    assert engine.status() == "RUNNING"
    assert engine.version() == "1.0.0"
    assert len(engine.capabilities()) == 8
    assert engine.metrics()["total_operations_executed"] >= 0
    assert engine.diagnostics()["engine"] == "document"

    # Stop engine
    await engine.stop()
    assert engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_document_engine_events_emission() -> None:
    """Test system event emissions during operation execution and lifecycle transitions."""
    engine = DocumentEngine()
    await engine.initialize()
    await engine.start()

    # Register test profile
    profile = DocumentOperationProfile(
        id="profile.payslip.v1",
        name="Payslip Profile",
        namespace="kortex.document",
        version="1.0.0",
        description="Payslip test profile",
        business_operation="GENERATE_PAYROLL_SLIP",
    )
    await engine.profile_manager.register_profile(profile)

    # Execute profile successfully
    req = OperationRequest(
        request_id="req-evt-1",
        profile_id="profile.payslip.v1",
        business_operation="GENERATE_PAYROLL_SLIP",
        binding_context=BindingContext(context_id="ctx-evt-1"),
    )
    res = await engine.execute_profile("profile.payslip.v1", req)
    assert res.status == "COMPLETED"

    # Check emitted events
    events = engine.emitted_events
    assert len(events) >= 2
    assert isinstance(events[0], DocumentOperationStartedEvent)
    assert events[0].request_id == "req-evt-1"
    assert isinstance(events[1], DocumentOperationCompletedEvent)
    assert events[1].request_id == "req-evt-1"

    # Register profile with required template to test failure event when binding fails
    prof_req_tmpl = DocumentOperationProfile(
        id="profile.invalid.v1",
        name="Invalid Template Profile",
        namespace="kortex.document",
        version="1.0.0",
        description="Invalid template test profile",
        business_operation="FAIL_OP",
        required_template_id="payslip.declarative.v1",
    )
    await engine.profile_manager.register_profile(prof_req_tmpl)

    # Pass empty binding context where required template fields are missing
    req_fail = OperationRequest(
        request_id="req-fail-1",
        profile_id="profile.invalid.v1",
        business_operation="FAIL_OP",
        binding_context=BindingContext(context_id="ctx-fail", data={}),
    )
    res_fail = await engine.execute_profile("profile.invalid.v1", req_fail)
    assert res_fail.status == "FAILED"

    fail_events = [e for e in engine.emitted_events if isinstance(e, DocumentOperationFailedEvent)]
    assert len(fail_events) == 1
    assert fail_events[0].request_id == "req-fail-1"

    # Lifecycle transition event emission
    doc_ver = await engine.lifecycle_manager.create_version(title="Doc 100", author_id="user1")
    meta = await engine.transition_lifecycle(doc_ver.metadata.document_id, doc_ver.version_id, DocumentLifecycleState.REVIEW)
    assert meta.lifecycle_state == DocumentLifecycleState.REVIEW

    events2 = engine.emitted_events
    transition_evts = [e for e in events2 if isinstance(e, DocumentLifecycleTransitionedEvent)]
    assert len(transition_evts) == 1
    assert transition_evts[0].document_id == doc_ver.metadata.document_id
    assert transition_evts[0].to_state == "REVIEW"


@pytest.mark.asyncio
async def test_system_events_instantiation() -> None:
    """Test all system event classes instantiations and model validation."""
    e1 = DocumentCreatedEvent(
        document_id="d1", version_id="v1", title="Title", author_id="user1"
    )
    assert e1.event_type == "document.created"

    e2 = DocumentPublishedEvent(document_id="d1", version_id="v1", published_at="2026-08-11T12:00:00Z")
    assert e2.event_type == "document.published"

    e3 = DocumentSupersededEvent(document_id="d1", superseded_version_id="v1", new_version_id="v2")
    assert e3.event_type == "document.superseded"

    e4 = DocumentArchivedEvent(document_id="d1", version_id="v1")
    assert e4.event_type == "document.archived"

    e5 = DocumentOperationFailedEvent(request_id="r1", profile_id="p1", errors=["error"])
    assert e5.event_type == "document.operation.failed"

    e6 = DocumentIntelligenceUpdatedEvent(document_id="d1", version_id="v1")
    assert e6.event_type == "document.intelligence.updated"

    e7 = DocumentAdapterRegisteredEvent(adapter_id="a1", display_name="Adapter 1", vendor="Kortex")
    assert e7.event_type == "document.adapter.registered"


@pytest.mark.asyncio
async def test_document_engine_facade_additional_methods() -> None:
    """Test DocumentEngine bind_template, generate_preview, list_adapters, and subsystem properties."""
    engine = DocumentEngine()

    assert engine.recovery_manager is not None
    assert engine.security_verifier is not None
    assert engine.storage_binder is not None

    # list_adapters
    adapters = engine.list_adapters()
    assert isinstance(adapters, list)

    # bind_template
    report = await engine.bind_template(
        "payslip.declarative.v1",
        BindingContext(context_id="ctx-bind-1", data={"employee_id": "EMP1"}),
    )
    assert report is not None

    # generate_preview when no preview adapter registered
    from kortex.engines.document.models import PreviewOptions
    prev_res = await engine.generate_preview("req-prev-1", PreviewOptions(page_number=1))
    assert prev_res.request_id == "req-prev-1"
    assert prev_res.image_bytes is None

    # generate_preview with empty options
    assert (await engine.generate_preview("", None)).image_bytes is None  # type: ignore[arg-type]

    assert engine.template_library is not None
    assert engine.template_binder is not None
    assert engine.adapter_registry is not None
    assert engine.sandbox is not None
    assert engine.pipeline_executor is not None
    assert engine.profile_manager is not None
    assert engine.health()["status"] == "HEALTHY"
    assert engine.metrics()["total_operations_executed"] >= 0
    assert engine.diagnostics()["engine"] == "document"
    assert engine.version() == "1.0.0"
    assert len(engine.capabilities()) == 8

    # transition_lifecycle invalid document_id
    with pytest.raises(Exception):
        await engine.transition_lifecycle("non_existent_doc", "v1", DocumentLifecycleState.REVIEW)

    # execute_profile with invalid request
    with pytest.raises(Exception):
        await engine.execute_profile("prof-1", None)  # type: ignore[arg-type]

