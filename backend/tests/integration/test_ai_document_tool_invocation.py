"""M7.4-W3/W4 canonical vertical slice: AI Studio agent tool calls reaching
the Document Engine.

Direct analogue of `test_ai_connector_tool_invocation.py` (M7.3), which
proved the same architecture for the Connector Engine -- this file proves
it generalizes to a second, unrelated target engine, driving the *real*
production tool definitions (`kernel_bootstrap.register_document_ai_tools`)
against the *real* Document Engine, end to end:

    AI proposes `document_list_templates` (read, no approval)
        -> AIToolInvoker -> KernelToolExecutionPort -> CapabilityDispatcher
        -> DocumentEngine.list_templates -> real pre-seeded standard
        templates -> conversation history

    AI proposes `document_generate` (mutation)
        -> pauses PAUSED_FOR_APPROVAL
        -> real durable approval ticket (kortex.workflow.approval.create)
        -> human decides APPROVED via kortex.workflow.approval.decide
        -> workflow.approval.decided event resumes the task automatically
        -> real DocumentEngine.execute_profile dispatch executes exactly once
        -> conversation history records the resolved turn

    rejection -> no dispatch, task cancelled, no history recorded

    cross-tenant: an agent running under tenant B cannot execute tenant A's
    operation profile, even with a guessed profile_id (T1 in the M7.4
    threat model) -- this is the first-ever real-dispatch proof of the
    M7.4-W1 tenant-derivation fix reached through the AI path specifically

    duplicate approval-event delivery does not dispatch the document
    generation twice (T8) -- proves the same pre-existing, general
    AgentOrchestrator resume-CAS mechanism M7.3 already proved for
    Connector generalizes here too, with no Document-specific fix needed

No component here is mocked below the Kernel boundary: real SecurityEngine,
real WorkflowEngine (DurableApprovalManager), real DocumentEngine, real
AIOrchestrationEngine production wiring.

A final, standalone test (`test_large_document_output_is_bounded_before_
entering_conversation_history`) proves T4 (content-leakage risk, planning
report §15): `OperationResult.output_bytes` can genuinely carry a full
generated document's content, and the existing, generic `ToolResult.
to_context_entry` truncation is what actually bounds it -- not a new
mitigation this milestone invented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.kernel_bootstrap import _build_ai_system_identity, register_document_ai_tools
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.agent import AgentTask
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.identity import AI_SYSTEM_PRINCIPAL_ID, AI_SYSTEM_ROLE
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.ai.tools import MAX_TOOL_OUTPUT_CHARS, ToolExecutionStatus, ToolResult
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.document.models import DocumentOperationProfile, OperationResult
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import WorkflowSettings

_TEST_MASTER_KEY = b"\x11" * 32
_TEST_SIGNING_KEY = b"\x22" * 32
_TENANT_A = "tenant_ai_document_a"
_TENANT_B = "tenant_ai_document_b"
_HUMAN_APPROVER_ROLE = "ai_approver"  # hardcoded in ai/governance.py's KernelDurableApprovalBridge
_PROFILE_ID = "reference-document-profile"


class _ScriptedProvider(BaseAIProvider):
    """Real, functioning provider proposing a scripted sequence of tool calls."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._metadata = AIProviderMetadata(
            provider_id="document-vslice-provider",
            display_name="Document Vertical Slice Test Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=["vslice-model"],
            credential_requirement="none",
        )
        self._script = script
        self.call_count = 0

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        step = self._script[min(self.call_count, len(self._script) - 1)]
        self.call_count += 1
        return LLMResponse(
            request_id=request.request_id,
            text_content=step.get("text", ""),
            tool_calls=step.get("tool_calls", []),
            token_usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True


def _profile(profile_id: str) -> DocumentOperationProfile:
    """A minimal profile with no adapter_pipeline -- `execute_profile` completes
    it trivially, exactly what a vertical-slice test needs without requiring
    a real adapter/template chain."""
    return DocumentOperationProfile(
        id=profile_id,
        name="Reference Document Profile",
        namespace="kortex.test",
        version="1.0.0",
        description="Reference profile for the M7.4 AI-tool vertical slice.",
        business_operation="test.generate",
    )


async def _build_kernel(tmp_path: Path, provider: BaseAIProvider) -> tuple[Kernel, Any, DocumentEngine]:
    db_path = (tmp_path / f"kortex_ai_document_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_ai_document_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    document_engine = DocumentEngine()
    workflow_engine = WorkflowEngine(
        settings=WorkflowSettings(approval_sweep_enabled=True, approval_sweep_interval_seconds=0.5)
    )
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(document_engine)
    kernel.register_engine(workflow_engine)

    bridge = KernelBridgeAdapter(kernel)
    ai_identity = _build_ai_system_identity(security_engine)
    ai_config = AIEngineRuntimeConfig(environment="production", enable_cloud_models=False)
    ai_bootstrap = KernelProductionBootstrap(ai_config)
    ai_engine = ai_bootstrap.create_ai_engine(
        kernel_bridge=bridge,
        data_store=data_store,
        custom_providers=[provider],
        registered_engines=list(kernel.get_all_engines().keys()),
        ai_identity=ai_identity,
    )
    # Register the REAL M7.4 production tool definitions -- not test doubles.
    register_document_ai_tools(ai_engine.tool_registry)
    kernel.register_engine(ai_engine)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        for perm in ("ai:orchestrate", "ai:execute", "document:execute", "document:read", "approval:write"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=AI_SYSTEM_ROLE, permission=perm))

        for perm in ("approval:write", "approval:read", "document:execute", "document:read"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_HUMAN_APPROVER_ROLE, permission=perm))

        for tenant_id, principal_id in ((_TENANT_A, "human_reviewer_a"), (_TENANT_B, "human_reviewer_b")):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    principal_type="USER",
                    credential_hash=hasher.hash("reviewer-pass"),
                    roles=[_HUMAN_APPROVER_ROLE],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    return kernel, ai_engine, document_engine


async def _human_token(kernel: Kernel, tenant_id: str, principal_id: str):
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "reviewer-pass",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


def _response_payload_bytes(output: Any) -> object:
    """A tool result's `output` is the real `OperationResult` instance on the
    immediate-dispatch path, but a plain dict (round-tripped through JSON
    persistence) on the resumed-after-approval path -- mirrors the identical
    finding in `test_ai_connector_tool_invocation.py`."""
    if isinstance(output, dict):
        return output.get("status")
    return output.status


@pytest.mark.asyncio
async def test_read_only_document_tool_executes_without_approval_and_records_history(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [
            {
                "text": "Checking available templates.",
                "tool_calls": [{"name": "document_list_templates", "arguments": {}}],
            },
            {"text": "Several standard templates are available.", "tool_calls": []},
        ]
    )
    kernel, ai_engine, _document = await _build_kernel(tmp_path, provider)
    try:
        task = AgentTask(
            task_id="read-task-1",
            tenant_id=_TENANT_A,
            user_id="user-does-not-matter",
            conversation_id="conv-read-1",
            goal="What document templates are available?",
        )
        result = await ai_engine.orchestrate_agent(task)

        assert result.status.value == "COMPLETED"
        tool_result = result.steps[0].tool_results[0]
        assert tool_result.status.value == "SUCCESS"
        # Real, pre-seeded standard templates -- never a fabricated empty/success result.
        assert len(tool_result.output) > 0

        history = await ai_engine.get_conversation_history(_TENANT_A, "conv-read-1")
        assert len(history) == 1
        assert history[0].assistant_content == "Several standard templates are available."
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_mutating_document_tool_requires_approval_then_resumes_and_executes(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [
            {
                "text": "Generating the document.",
                "tool_calls": [
                    {
                        "name": "document_generate",
                        "arguments": {
                            "profile_id": _PROFILE_ID,
                            "request": {
                                "request_id": "ai-doc-request-1",
                                "profile_id": _PROFILE_ID,
                                "binding_context": {"context_id": "ai-doc-ctx-1"},
                            },
                        },
                    }
                ],
            },
            {"text": "Document generated.", "tool_calls": []},
        ]
    )
    kernel, ai_engine, document_engine = await _build_kernel(tmp_path, provider)
    try:
        await document_engine.profile_manager.register_profile(_profile(_PROFILE_ID), tenant_id=_TENANT_A)

        task = AgentTask(
            task_id="mutate-task-1",
            tenant_id=_TENANT_A,
            user_id="user-does-not-matter",
            conversation_id="conv-mutate-1",
            goal="Generate the reference document",
        )

        paused = await ai_engine.orchestrate_agent(task)
        assert paused.status.value == "PAUSED_FOR_APPROVAL"

        human_token = await _human_token(kernel, _TENANT_A, "human_reviewer_a")
        list_result = await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.approval.list",
                session_token=human_token,
                parameters={"tenant_id": _TENANT_A},
                context={"resource_tenant_id": _TENANT_A},
            )
        )
        ticket = next(t for t in list_result if t["correlation_id"] == task.task_id)
        assert ticket["requester_principal_id"] == AI_SYSTEM_PRINCIPAL_ID

        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.approval.decide",
                session_token=human_token,
                parameters={
                    "request_id": ticket["id"],
                    "decision": "APPROVED",
                    "approver_id": "human_reviewer_a",
                    "tenant_id": _TENANT_A,
                },
                context={"resource_tenant_id": _TENANT_A},
            )
        )

        record = await ai_engine.agent_orchestrator.get_task(task.task_id, _TENANT_A)
        assert record is not None
        assert record.status.value == "COMPLETED"
        tool_result = record.steps[-2].tool_results[0]
        assert tool_result.status.value == "SUCCESS"
        assert _response_payload_bytes(tool_result.output) == "COMPLETED"

        history = await ai_engine.get_conversation_history(_TENANT_A, "conv-mutate-1")
        assert len(history) == 1
        assert history[0].assistant_content == "Document generated."
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_duplicate_approval_decided_event_does_not_dispatch_the_document_generation_twice(
    tmp_path: Path,
) -> None:
    """T8: proves the *existing*, general `AgentOrchestrator`/
    `_on_approval_decided` mechanism -- not anything document-specific --
    already prevents a replayed `workflow.approval.decided` event from
    generating a document twice. Mirrors
    `test_ai_connector_tool_invocation.py`'s identical M7.3 proof for a
    second, unrelated target engine."""
    provider = _ScriptedProvider(
        [
            {
                "text": "Generating the document.",
                "tool_calls": [
                    {
                        "name": "document_generate",
                        "arguments": {
                            "profile_id": _PROFILE_ID,
                            "request": {
                                "request_id": "ai-doc-request-idempotency",
                                "profile_id": _PROFILE_ID,
                                "binding_context": {"context_id": "ai-doc-ctx-idempotency"},
                            },
                        },
                    }
                ],
            },
            {"text": "Document generated.", "tool_calls": []},
        ]
    )
    kernel, ai_engine, document_engine = await _build_kernel(tmp_path, provider)
    try:
        await document_engine.profile_manager.register_profile(_profile(_PROFILE_ID), tenant_id=_TENANT_A)

        captured_events: list[Any] = []

        def _capture(event: Any) -> None:
            if event.topic == "workflow.approval.decided":
                captured_events.append(event)

        kernel.subscribe_event("*", _capture)

        task = AgentTask(
            task_id="idempotency-task-1",
            tenant_id=_TENANT_A,
            user_id="user-does-not-matter",
            conversation_id="conv-idempotency-1",
            goal="Generate the reference document",
        )
        await ai_engine.orchestrate_agent(task)

        human_token = await _human_token(kernel, _TENANT_A, "human_reviewer_a")
        list_result = await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.approval.list",
                session_token=human_token,
                parameters={"tenant_id": _TENANT_A},
                context={"resource_tenant_id": _TENANT_A},
            )
        )
        ticket = next(t for t in list_result if t["correlation_id"] == task.task_id)

        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.approval.decide",
                session_token=human_token,
                parameters={
                    "request_id": ticket["id"],
                    "decision": "APPROVED",
                    "approver_id": "human_reviewer_a",
                    "tenant_id": _TENANT_A,
                },
                context={"resource_tenant_id": _TENANT_A},
            )
        )

        assert len(captured_events) == 1
        record_after_first = await ai_engine.agent_orchestrator.get_task(task.task_id, _TENANT_A)
        assert record_after_first is not None
        assert record_after_first.status.value == "COMPLETED"

        await ai_engine._on_approval_decided(captured_events[0])

        record_after_replay = await ai_engine.agent_orchestrator.get_task(task.task_id, _TENANT_A)
        assert record_after_replay is not None
        assert record_after_replay.status.value == "COMPLETED"
        assert len(record_after_replay.steps) == len(record_after_first.steps)

        history = await ai_engine.get_conversation_history(_TENANT_A, "conv-idempotency-1")
        assert len(history) == 1
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_rejected_document_generation_never_dispatches(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [
            {
                "text": "Generating the document.",
                "tool_calls": [
                    {
                        "name": "document_generate",
                        "arguments": {
                            "profile_id": _PROFILE_ID,
                            "request": {
                                "request_id": "ai-doc-request-reject",
                                "profile_id": _PROFILE_ID,
                                "binding_context": {"context_id": "ai-doc-ctx-reject"},
                            },
                        },
                    }
                ],
            }
        ]
    )
    kernel, ai_engine, document_engine = await _build_kernel(tmp_path, provider)
    try:
        await document_engine.profile_manager.register_profile(_profile(_PROFILE_ID), tenant_id=_TENANT_A)

        task = AgentTask(
            task_id="reject-task-1",
            tenant_id=_TENANT_A,
            user_id="user-does-not-matter",
            conversation_id="conv-reject-1",
            goal="Generate the reference document",
        )
        await ai_engine.orchestrate_agent(task)

        human_token = await _human_token(kernel, _TENANT_A, "human_reviewer_a")
        list_result = await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.approval.list",
                session_token=human_token,
                parameters={"tenant_id": _TENANT_A},
                context={"resource_tenant_id": _TENANT_A},
            )
        )
        ticket = next(t for t in list_result if t["correlation_id"] == task.task_id)

        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.approval.decide",
                session_token=human_token,
                parameters={
                    "request_id": ticket["id"],
                    "decision": "REJECTED",
                    "approver_id": "human_reviewer_a",
                    "tenant_id": _TENANT_A,
                },
                context={"resource_tenant_id": _TENANT_A},
            )
        )

        record = await ai_engine.agent_orchestrator.get_task(task.task_id, _TENANT_A)
        assert record is not None
        assert record.status.value == "CANCELLED"

        history = await ai_engine.get_conversation_history(_TENANT_A, "conv-reject-1")
        assert history == []

        assert all(result.status.value != "SUCCESS" for step in record.steps for result in step.tool_results)
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_cross_tenant_agent_cannot_execute_another_tenants_document_profile(tmp_path: Path) -> None:
    """T1: an agent orchestrated under tenant B must not be able to execute
    tenant A's operation profile, even by supplying tenant A's exact
    (guessed) profile_id -- `DocumentEngine.execute_profile`'s M7.4-W1 fix
    binds `principal.tenant_id` authoritatively regardless of what the
    LLM-proposed tool arguments contain. This is the first real-dispatch
    proof of the W1 fix reached through the AI path specifically."""
    provider = _ScriptedProvider(
        [
            {
                "text": "Generating the document.",
                "tool_calls": [
                    {
                        "name": "document_generate",
                        "arguments": {
                            "profile_id": _PROFILE_ID,
                            "request": {
                                "request_id": "ai-doc-request-cross-tenant",
                                "profile_id": _PROFILE_ID,
                                "binding_context": {"context_id": "ai-doc-ctx-cross-tenant"},
                            },
                        },
                    }
                ],
            },
            {"text": "Done.", "tool_calls": []},
        ]
    )
    kernel, ai_engine, document_engine = await _build_kernel(tmp_path, provider)
    try:
        # Tenant A owns this profile; tenant B never registers it.
        await document_engine.profile_manager.register_profile(_profile(_PROFILE_ID), tenant_id=_TENANT_A)

        task = AgentTask(
            task_id="cross-tenant-task-1",
            tenant_id=_TENANT_B,
            user_id="user-does-not-matter",
            conversation_id="conv-cross-tenant-1",
            goal="Generate the reference document",
        )

        paused = await ai_engine.orchestrate_agent(task)
        assert paused.status.value == "PAUSED_FOR_APPROVAL"

        human_token = await _human_token(kernel, _TENANT_B, "human_reviewer_b")
        list_result = await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.approval.list",
                session_token=human_token,
                parameters={"tenant_id": _TENANT_B},
                context={"resource_tenant_id": _TENANT_B},
            )
        )
        ticket = next(t for t in list_result if t["correlation_id"] == task.task_id)

        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.approval.decide",
                session_token=human_token,
                parameters={
                    "request_id": ticket["id"],
                    "decision": "APPROVED",
                    "approver_id": "human_reviewer_b",
                    "tenant_id": _TENANT_B,
                },
                context={"resource_tenant_id": _TENANT_B},
            )
        )

        record = await ai_engine.agent_orchestrator.get_task(task.task_id, _TENANT_B)
        assert record is not None
        # The tool call itself must have failed closed -- never a fabricated
        # success, and never tenant A's real profile execution.
        tool_result = record.steps[-2].tool_results[0]
        assert tool_result.status.value != "SUCCESS"
    finally:
        await kernel.shutdown()


def test_large_document_output_is_bounded_before_entering_conversation_history() -> None:
    """T4 (planning report §15): `OperationResult.output_bytes` can genuinely
    carry a full generated document's content -- proves the existing,
    generic `ToolResult.to_context_entry` truncation actually bounds a
    result shaped exactly like `document_generate`'s real output, not just
    a small structured payload like the connector tools return. This is not
    a new mitigation this milestone built; it is the same backstop control
    every other capability's potentially-large output already relies on,
    verified here against this tool's specific, larger-than-usual result
    shape rather than assumed to apply."""
    large_content = b"X" * 200_000  # far larger than MAX_TOOL_OUTPUT_CHARS
    result = OperationResult(
        request_id="req-large",
        status="COMPLETED",
        output_bytes=large_content,
        storage_key="tenant-a/reference-document-profile/req-large",
    )

    tool_result = ToolResult(
        call_id="call-large",
        tool_name="document_generate",
        status=ToolExecutionStatus.SUCCESS,
        output=result,
    )

    context_entry = tool_result.to_context_entry()

    assert len(context_entry) < len(large_content)
    assert len(context_entry) < MAX_TOOL_OUTPUT_CHARS + 1000  # payload + marker/status overhead
    assert "TRUNCATED" in context_entry
