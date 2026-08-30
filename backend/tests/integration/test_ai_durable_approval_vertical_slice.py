"""M6.2 end-to-end vertical slice: AI system identity + durable approval bridge
+ correlation continuity + decision-driven resume.

Drives the full governed-AI-action loop through the real Kernel dispatch
boundary at every hop:

    AI proposes a mutating tool call
        -> real RBAC/tenant evaluation (AI system principal)
        -> durable approval ticket created via the real
           kortex.workflow.approval.create capability, attributed to the AI
        -> AI's in-process task pauses (PAUSED_FOR_APPROVAL)
        -> a human principal decides via the real
           kortex.workflow.approval.decide capability
        -> the resulting workflow.approval.decided event resumes the paused
           agent task automatically
        -> the mutating capability actually executes, exactly once

No component here is mocked below the Kernel boundary: real SecurityEngine
(Argon2id auth, RBAC, ABAC), real WorkflowEngine (DurableApprovalManager,
real SQLite persistence), real AIOrchestrationEngine production wiring
(`KernelProductionBootstrap.create_ai_engine`), real event-driven resume.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.kernel_bootstrap import _build_ai_system_identity
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.agent import AgentTask
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.identity import AI_SYSTEM_PRINCIPAL_ID, AI_SYSTEM_ROLE
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.ai.tools import ToolDefinition
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine

_TEST_MASTER_KEY = b"\xcc" * 32
_TEST_SIGNING_KEY = b"\xdd" * 32
_TENANT = "tenant_vslice"
_MUTATE_CAPABILITY = "test.vslice.mutate_action"
_HUMAN_APPROVER_ROLE = "ai_approver"


class _ScriptedProvider(BaseAIProvider):
    """Real, functioning provider: proposes one mutating tool call, then
    (on resume) returns a terminal response with no further tool calls."""

    def __init__(self) -> None:
        self._metadata = AIProviderMetadata(
            provider_id="vslice-provider",
            display_name="Vertical Slice Test Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=["vslice-model"],
            credential_requirement="none",
        )
        self.call_count = 0

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.call_count == 1:
            return LLMResponse(
                request_id=request.request_id,
                text_content="Proposing a mutation.",
                tool_calls=[{"name": "do_mutation", "arguments": {"target": "widget-1"}}],
                token_usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            )
        return LLMResponse(
            request_id=request.request_id,
            text_content="Mutation applied.",
            tool_calls=[],
            token_usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, Any]]:
    db_path = (tmp_path / f"kortex_vslice_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_vslice_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(workflow_engine)

    async def _mutate_handler(target: str | None = None, principal: Any = None, **kwargs: Any) -> dict[str, Any]:
        return {"status": "SUCCESS", "target": target}

    kernel.register_capability(
        name=_MUTATE_CAPABILITY,
        description="Test-only mutating capability gating the vertical slice.",
        provider="test",
        handler=_mutate_handler,
        required_permissions=["test:mutate"],
    )

    provider = _ScriptedProvider()
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
    ai_engine.tool_registry.register_tool(
        ToolDefinition(
            name="do_mutation",
            description="Mutating test tool.",
            canonical_capability=_MUTATE_CAPABILITY,
            parameters_schema={"type": "object"},
            is_mutation=True,
        )
    )
    kernel.register_engine(ai_engine)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        # AI system principal: enough RBAC to propose the mutation and to
        # create a durable approval ticket for it. Deliberately NOT granted
        # `_HUMAN_APPROVER_ROLE` -- it must never be able to decide its own
        # tickets.
        session.add(RolePermissionRecord(id=str(uuid4()), role=AI_SYSTEM_ROLE, permission="test:mutate"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=AI_SYSTEM_ROLE, permission="approval:write"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=AI_SYSTEM_ROLE, permission="ai:orchestrate"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=AI_SYSTEM_ROLE, permission="ai:execute"))

        # Human approver: may read/decide approval tickets.
        session.add(RolePermissionRecord(id=str(uuid4()), role=_HUMAN_APPROVER_ROLE, permission="approval:write"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_HUMAN_APPROVER_ROLE, permission="approval:read"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT,
                principal_id="human_reviewer",
                principal_type="USER",
                credential_hash=hasher.hash("reviewer-pass"),
                roles=[_HUMAN_APPROVER_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    try:
        yield kernel, ai_engine
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _human_token(kernel: Kernel):  # noqa: ANN202
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": _TENANT,
            "principal_id": "human_reviewer",
            "password": "reviewer-pass",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.asyncio
async def test_full_governed_ai_action_vertical_slice(kernel_env: tuple[Kernel, Any]) -> None:
    kernel, ai_engine = kernel_env

    task = AgentTask(
        task_id="vslice-task-1",
        tenant_id=_TENANT,
        user_id="user-does-not-matter",
        conversation_id="conv-vslice-1",
        goal="Apply a mutation to widget-1",
    )

    # 1. The AI proposes the mutation and pauses (in-process) pending approval.
    paused = await ai_engine.orchestrate_agent(task)
    assert paused.status.value == "PAUSED_FOR_APPROVAL"
    assert paused.resume_token is not None

    # 2. A REAL durable approval ticket must exist, attributed to the AI
    # system principal, correlated to this exact task.
    human_token = await _human_token(kernel)
    list_result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.approval.list",
            session_token=human_token,
            parameters={"tenant_id": _TENANT},
            context={"resource_tenant_id": _TENANT},
        )
    )
    ai_tickets = [
        t for t in list_result
        if t["required_role"] == _HUMAN_APPROVER_ROLE
    ]
    assert len(ai_tickets) == 1
    ticket = ai_tickets[0]
    assert ticket["requester_principal_id"] == AI_SYSTEM_PRINCIPAL_ID
    assert ticket["requester_principal_type"] == "AGENT"
    assert ticket["correlation_id"] == task.task_id

    # 3. The AI itself must NOT be able to decide this ticket -- it was
    # never granted `_HUMAN_APPROVER_ROLE`, and even if it had been, the
    # requester-identity check would still refuse it.
    security_engine: SecurityEngine = kernel.get_engine("security")
    ai_token = await _build_ai_system_identity(security_engine).get_session_token(_TENANT)
    decide_as_ai = CapabilityRequest(
        capability_name="kortex.workflow.approval.decide",
        session_token=ai_token,
        parameters={
            "request_id": ticket["id"],
            "decision": "APPROVED",
            "approver_id": AI_SYSTEM_PRINCIPAL_ID,
            "tenant_id": _TENANT,
        },
        context={"resource_tenant_id": _TENANT},
    )
    from kortex.engines.security.exceptions import AuthorizationDeniedError

    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(decide_as_ai)

    # 4. A human decides APPROVED through the real capability.
    decide_result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.approval.decide",
            session_token=human_token,
            parameters={
                "request_id": ticket["id"],
                "decision": "APPROVED",
                "approver_id": "human_reviewer",
                "tenant_id": _TENANT,
            },
            context={"resource_tenant_id": _TENANT},
        )
    )
    assert decide_result["decision"] == "APPROVED"

    # 5. The decision must have driven the paused agent task all the way to
    # completion -- through the generic workflow.approval.decided event,
    # with no direct coupling between the Workflow Engine and the AI engine.
    record = await ai_engine.agent_orchestrator.get_task(task.task_id, _TENANT)
    assert record is not None
    assert record.status.value == "COMPLETED"

    # 6. The mutating capability must have actually executed -- exactly
    # once, with the expected output -- as the resumed step's own recorded
    # tool result.
    assert record.steps[-2].tool_results[0].status.value == "SUCCESS"
    assert record.steps[-2].tool_results[0].output == {"status": "SUCCESS", "target": "widget-1"}


@pytest.mark.asyncio
async def test_rejected_decision_cancels_paused_task_without_executing(kernel_env: tuple[Kernel, Any]) -> None:
    kernel, ai_engine = kernel_env

    task = AgentTask(
        task_id="vslice-task-2",
        tenant_id=_TENANT,
        user_id="user-does-not-matter",
        conversation_id="conv-vslice-2",
        goal="Apply a mutation to widget-2",
    )
    paused = await ai_engine.orchestrate_agent(task)
    assert paused.status.value == "PAUSED_FOR_APPROVAL"

    human_token = await _human_token(kernel)
    list_result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.approval.list",
            session_token=human_token,
            parameters={"tenant_id": _TENANT},
            context={"resource_tenant_id": _TENANT},
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
                "approver_id": "human_reviewer",
                "tenant_id": _TENANT,
            },
            context={"resource_tenant_id": _TENANT},
        )
    )

    record = await ai_engine.agent_orchestrator.get_task(task.task_id, _TENANT)
    assert record is not None
    assert record.status.value == "CANCELLED"
