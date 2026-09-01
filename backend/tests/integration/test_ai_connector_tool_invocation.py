"""M7.3-W4/W5 canonical vertical slice: AI Studio agent tool calls reaching
the Connector Engine.

Prior to M7.3, no test anywhere proved an AI-agent-proposed tool call could
reach the Connector Engine -- only the Workflow Engine's
`ExternalExecutionManager` path was proven (M6.3). This file is the direct
analogue of `test_ai_durable_approval_vertical_slice.py` (which proves the
governed-approval loop with a synthetic test capability) and
`test_external_execution_vertical_slice.py` (M6.3-5, which proves the
Workflow -> Connector path), but drives the *real* production connector
tools (`kernel_bootstrap.register_connector_ai_tools`) against the *real*
Connector Engine and a *real* registered driver, end to end:

    AI proposes `connector_read_status` (read, no approval)
        -> AIToolInvoker -> KernelToolExecutionPort -> CapabilityDispatcher
        -> ConnectorEngine.execute_action -> ConnectorPipeline
        -> DummyConnectorDriver -> real result -> conversation history

    AI proposes `connector_send_action` (mutation)
        -> pauses PAUSED_FOR_APPROVAL
        -> real durable approval ticket (kortex.workflow.approval.create)
        -> human decides APPROVED via kortex.workflow.approval.decide
        -> workflow.approval.decided event resumes the task automatically
        -> real ConnectorEngine dispatch executes exactly once
        -> conversation history records the resolved turn

    rejection -> no dispatch, task cancelled, no history recorded

    cross-tenant: an agent running under tenant B cannot invoke a tool call
    that resolves to tenant A's connector profile, even with a guessed
    profile_id (T2 in the M7.3 threat model)

No component here is mocked below the Kernel boundary: real SecurityEngine,
real WorkflowEngine (DurableApprovalManager), real ConnectorEngine with the
real `DummyConnectorDriver`, real AIOrchestrationEngine production wiring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.kernel_bootstrap import _build_ai_system_identity, register_connector_ai_tools
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.agent import AgentTask
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.identity import AI_SYSTEM_PRINCIPAL_ID, AI_SYSTEM_ROLE
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import ConnectorProfileNotFoundError
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import WorkflowSettings

_TEST_MASTER_KEY = b"\xee" * 32
_TEST_SIGNING_KEY = b"\xff" * 32
_TENANT_A = "tenant_ai_connector_a"
_TENANT_B = "tenant_ai_connector_b"
_HUMAN_APPROVER_ROLE = "ai_approver"  # hardcoded in ai/governance.py's KernelDurableApprovalBridge
_PROFILE_ID = "reference-connector-profile"


class _ScriptedProvider(BaseAIProvider):
    """Real, functioning provider proposing a scripted sequence of tool calls."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._metadata = AIProviderMetadata(
            provider_id="connector-vslice-provider",
            display_name="Connector Vertical Slice Test Provider",
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


async def _build_kernel(
    tmp_path: Path, provider: BaseAIProvider
) -> tuple[Kernel, Any, ConnectorEngine]:
    db_path = (tmp_path / f"kortex_ai_connector_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_ai_connector_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    connector_engine = ConnectorEngine()
    workflow_engine = WorkflowEngine(
        settings=WorkflowSettings(approval_sweep_enabled=True, approval_sweep_interval_seconds=0.5)
    )
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)
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
    # Register the REAL M7.3 production tool definitions -- not test doubles
    # -- proving the actual shipped schema/wiring, not a stand-in for it.
    register_connector_ai_tools(ai_engine.tool_registry)
    kernel.register_engine(ai_engine)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    # Register the real DummyConnectorDriver -- offline, deterministic,
    # exactly what `kernel_bootstrap.register_production_connector_drivers`
    # also registers for the same driver_id in production.
    connector_engine.register_driver(DummyConnectorDriver())

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        # AI system principal: enough RBAC to orchestrate, propose tool
        # calls, dispatch a connector action, and create a durable approval
        # ticket for a mutation.
        for perm in ("ai:orchestrate", "ai:execute", "connector:execute", "approval:write", "connector:write"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=AI_SYSTEM_ROLE, permission=perm))

        # Human approver: may read/decide approval tickets, and provision
        # connector profiles (used by the fixture below via the real
        # kortex.connector.profile.register capability).
        for perm in ("approval:write", "approval:read", "connector:write", "connector:read"):
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

    return kernel, ai_engine, connector_engine


async def _human_token(kernel: Kernel, tenant_id: str, principal_id: str):  # noqa: ANN202
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


async def _register_profile(kernel: Kernel, tenant_id: str, token: Any, profile_id: str = _PROFILE_ID) -> None:
    await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.connector.profile.register",
            session_token=token,
            context={"resource_tenant_id": tenant_id},
            parameters={
                "profile": {
                    "profile_id": profile_id,
                    "name": "Reference Profile",
                    "driver_id": "connector-dummy",
                }
            },
        )
    )


def _response_payload(output: Any) -> dict[str, Any]:
    """A tool result's `output` is the real `ActionResult` instance on the
    immediate-dispatch path, but a plain dict (round-tripped through JSON
    persistence) on the resumed-after-approval path -- both carry the same
    `response_payload` data, just under different container types."""
    if isinstance(output, dict):
        return output["response_payload"]
    return output.response_payload


@pytest.mark.asyncio
async def test_read_only_connector_tool_executes_without_approval_and_records_history(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [
            {
                "text": "Checking connector status.",
                "tool_calls": [
                    {
                        "name": "connector_read_status",
                        "arguments": {
                            "request": {
                                "request_id": "ai-tool-request-1",
                                "profile_id": _PROFILE_ID,
                                "action_type": "FETCH",
                                "payload": {},
                            },
                        },
                    }
                ],
            },
            {"text": "The connector is healthy.", "tool_calls": []},
        ]
    )
    kernel, ai_engine, _connector = await _build_kernel(tmp_path, provider)
    try:
        human_token = await _human_token(kernel, _TENANT_A, "human_reviewer_a")
        await _register_profile(kernel, _TENANT_A, human_token)

        task = AgentTask(
            task_id="read-task-1",
            tenant_id=_TENANT_A,
            user_id="user-does-not-matter",
            conversation_id="conv-read-1",
            goal="Check the connector status",
        )
        result = await ai_engine.orchestrate_agent(task)

        assert result.status.value == "COMPLETED"
        tool_result = result.steps[0].tool_results[0]
        assert tool_result.status.value == "SUCCESS"
        # DummyConnectorDriver's own distinguishable response field -- never
        # a fabricated success (mirrors M6.3-5's own verification approach).
        assert _response_payload(tool_result.output)["mock_driver_id"] == "connector-dummy"

        history = await ai_engine.get_conversation_history(_TENANT_A, "conv-read-1")
        assert len(history) == 1
        assert history[0].assistant_content == "The connector is healthy."
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_mutating_connector_tool_requires_approval_then_resumes_and_executes(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [
            {
                "text": "Sending the update.",
                "tool_calls": [
                    {
                        "name": "connector_send_action",
                        "arguments": {
                            "request": {
                                "request_id": "ai-tool-request-2",
                                "profile_id": _PROFILE_ID,
                                "action_type": "SEND",
                                "payload": {"message": "hello"},
                            },
                        },
                    }
                ],
            },
            {"text": "Update sent.", "tool_calls": []},
        ]
    )
    kernel, ai_engine, _connector = await _build_kernel(tmp_path, provider)
    try:
        human_token = await _human_token(kernel, _TENANT_A, "human_reviewer_a")
        await _register_profile(kernel, _TENANT_A, human_token)

        task = AgentTask(
            task_id="mutate-task-1",
            tenant_id=_TENANT_A,
            user_id="user-does-not-matter",
            conversation_id="conv-mutate-1",
            goal="Send an update to the connector",
        )

        # 1. Proposing a mutation pauses -- no dispatch has happened yet.
        paused = await ai_engine.orchestrate_agent(task)
        assert paused.status.value == "PAUSED_FOR_APPROVAL"

        # 2. A real durable approval ticket exists, attributed to the AI.
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

        # 3. Human approves.
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

        # 4. The decision drove the paused task to completion via the
        # generic workflow.approval.decided event -- with a real,
        # distinguishable ConnectorEngine dispatch, exactly once.
        record = await ai_engine.agent_orchestrator.get_task(task.task_id, _TENANT_A)
        assert record is not None
        assert record.status.value == "COMPLETED"
        tool_result = record.steps[-2].tool_results[0]
        assert tool_result.status.value == "SUCCESS"
        # The resumed dispatch path reads the persisted step back from
        # storage, so `output` comes back as a plain dict (round-tripped
        # through JSON), not the live `ActionResult` instance the immediate
        # (non-approval) path in the test above returns directly.
        assert _response_payload(tool_result.output)["mock_driver_id"] == "connector-dummy"

        history = await ai_engine.get_conversation_history(_TENANT_A, "conv-mutate-1")
        assert len(history) == 1
        assert history[0].assistant_content == "Update sent."
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_duplicate_approval_decided_event_does_not_dispatch_the_connector_action_twice(
    tmp_path: Path,
) -> None:
    """T9 (idempotency, per the M7.3 planning report's open question):
    proves the *existing*, general `AgentOrchestrator`/`_on_approval_decided`
    mechanism -- not anything connector-specific -- already prevents a
    replayed `workflow.approval.decided` event from dispatching a mutating
    connector action twice. `_on_approval_decided` no-ops whenever the
    task's status is no longer `PAUSED_FOR_APPROVAL` (own docstring:
    "idempotent no-op either way"), and `AgentOrchestrator.resume_task`
    itself additionally CAS-claims the record -- two independent guards, not
    one. This test captures the real event the real approval decision
    publishes and re-delivers that exact object a second time (not a
    hand-built stand-in), proving the guarantee holds for a connector tool
    resume specifically, not only for the synthetic capability the
    pre-existing M6.2 vertical slice already covers.

    Idempotency guarantee: an AI-originated mutating connector dispatch
    triggered via `workflow.approval.decided` executes at most once, even
    under duplicate event delivery.
    Evidence: this test.
    Remaining limitation: this covers duplicate *event delivery* only. A
    second, independent duplicate-execution vector -- the underlying
    `ConnectorEngine.execute_action` capability call itself carrying no
    idempotency key of its own (unlike `ExternalExecutionRequest`'s
    `UniqueConstraint(tenant_id, idempotency_key)` on the Workflow-triggered
    path) -- is not covered by this guard and is out of scope for this
    milestone: the AI-tool path never retries a completed dispatch, so
    there is no code path that would attempt a second live call for the
    same resumed task in the first place.
    """
    provider = _ScriptedProvider(
        [
            {
                "text": "Sending the update.",
                "tool_calls": [
                    {
                        "name": "connector_send_action",
                        "arguments": {
                            "request": {
                                "request_id": "ai-tool-request-idempotency",
                                "profile_id": _PROFILE_ID,
                                "action_type": "SEND",
                                "payload": {"message": "hello"},
                            },
                        },
                    }
                ],
            },
            {"text": "Update sent.", "tool_calls": []},
        ]
    )
    kernel, ai_engine, _connector = await _build_kernel(tmp_path, provider)
    try:
        human_token = await _human_token(kernel, _TENANT_A, "human_reviewer_a")
        await _register_profile(kernel, _TENANT_A, human_token)

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
            goal="Send an update to the connector",
        )
        await ai_engine.orchestrate_agent(task)

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

        assert len(captured_events) == 1, "expected exactly one real workflow.approval.decided event"
        record_after_first = await ai_engine.agent_orchestrator.get_task(task.task_id, _TENANT_A)
        assert record_after_first is not None
        assert record_after_first.status.value == "COMPLETED"

        # Re-deliver the exact same, real event a second time -- simulating
        # at-least-once event delivery -- directly to the handler (the same
        # seam `EventEngine` itself would call it through).
        await ai_engine._on_approval_decided(captured_events[0])  # noqa: SLF001

        # No second dispatch occurred: the task is still COMPLETED with the
        # same single resolved step, and conversation history still records
        # exactly one turn, not two.
        record_after_replay = await ai_engine.agent_orchestrator.get_task(task.task_id, _TENANT_A)
        assert record_after_replay is not None
        assert record_after_replay.status.value == "COMPLETED"
        assert len(record_after_replay.steps) == len(record_after_first.steps)

        history = await ai_engine.get_conversation_history(_TENANT_A, "conv-idempotency-1")
        assert len(history) == 1
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_rejected_mutating_tool_call_never_dispatches(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [
            {
                "text": "Sending the update.",
                "tool_calls": [
                    {
                        "name": "connector_send_action",
                        "arguments": {
                            "request": {
                                "request_id": "ai-tool-request-2",
                                "profile_id": _PROFILE_ID,
                                "action_type": "SEND",
                                "payload": {"message": "hello"},
                            },
                        },
                    }
                ],
            }
        ]
    )
    kernel, ai_engine, _connector = await _build_kernel(tmp_path, provider)
    try:
        human_token = await _human_token(kernel, _TENANT_A, "human_reviewer_a")
        await _register_profile(kernel, _TENANT_A, human_token)

        task = AgentTask(
            task_id="reject-task-1",
            tenant_id=_TENANT_A,
            user_id="user-does-not-matter",
            conversation_id="conv-reject-1",
            goal="Send an update to the connector",
        )
        await ai_engine.orchestrate_agent(task)

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

        # No connector dispatch ever succeeded -- the pipeline was never
        # reached for the rejected tool call.
        assert all(
            result.status.value != "SUCCESS"
            for step in record.steps
            for result in step.tool_results
        )
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_cross_tenant_agent_cannot_reach_another_tenants_connector_profile(tmp_path: Path) -> None:
    """T2: an agent orchestrated under tenant B must not be able to invoke a
    tool call resolving to tenant A's connector profile, even by supplying
    tenant A's exact (guessed) profile_id -- `ConnectorEngine.execute_action`
    binds `principal.tenant_id` authoritatively (M6.3-1) regardless of what
    the LLM-proposed tool arguments contain."""
    provider = _ScriptedProvider(
        [
            {
                "text": "Checking connector status.",
                "tool_calls": [
                    {
                        "name": "connector_read_status",
                        "arguments": {
                            "request": {
                                "request_id": "ai-tool-request-1",
                                "profile_id": _PROFILE_ID,
                                "action_type": "FETCH",
                                "payload": {},
                            },
                        },
                    }
                ],
            },
            {"text": "Done.", "tool_calls": []},
        ]
    )
    kernel, ai_engine, _connector = await _build_kernel(tmp_path, provider)
    try:
        human_token_a = await _human_token(kernel, _TENANT_A, "human_reviewer_a")
        await _register_profile(kernel, _TENANT_A, human_token_a, profile_id=_PROFILE_ID)

        # Tenant B never registers this profile_id -- it belongs solely to
        # tenant A. An agent task orchestrated under tenant B proposes a
        # tool call naming tenant A's exact profile_id.
        task = AgentTask(
            task_id="cross-tenant-task-1",
            tenant_id=_TENANT_B,
            user_id="user-does-not-matter",
            conversation_id="conv-cross-tenant-1",
            goal="Check the connector status",
        )
        result = await ai_engine.orchestrate_agent(task)

        # The task still reaches a terminal state, but the tool call itself
        # must have failed closed -- never a fabricated success, and never
        # tenant A's real driver response.
        tool_result = result.steps[0].tool_results[0]
        assert tool_result.status.value != "SUCCESS"
    finally:
        await kernel.shutdown()
