"""M7.2: real Kernel capability-dispatch coverage for
`kortex.ai.agent.orchestrate`/`kortex.ai.agent.resume`/`kortex.ai.agent.status`.

Closes a real, confirmed gap found while planning AI Studio's conversational
completion: every existing test that exercises these three capabilities
calls `AIOrchestrationEngine.orchestrate_agent`/`resume_agent`/`get_agent_task`
directly, in-process — `test_ai_durable_approval_vertical_slice.py` calls
`ai_engine.orchestrate_agent(task)` directly, and `test_ai_engine.py` only
asserts the two capabilities' `required_permissions` strings, never
dispatches either. Nothing previously proved a caller must go through real
authentication/RBAC/tenant-derivation to reach them, or that the
JSON-shaped (dict, not live object) parameters a desktop client actually
sends survive the trip — mirrors `test_ai_tenant_isolation_dispatch.py`'s
exact methodology, extended to agent orchestration/resume.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x91" * 32
_TEST_SIGNING_KEY = b"\x92" * 32
_ROLE = "AI_AGENT_DISPATCH_TEST_ROLE"
_TENANT_A = "tenant_a_agent_dispatch"
_TENANT_B = "tenant_b_agent_dispatch"


class _ScriptedProvider(BaseAIProvider):
    """Real, functioning provider. `mutate=True` proposes one mutating tool
    call on the first turn (pausing for approval) and a terminal response on
    the second (resume) turn; `mutate=False` returns a terminal response
    immediately (no pause)."""

    def __init__(self, *, mutate: bool) -> None:
        self._mutate = mutate
        self._metadata = AIProviderMetadata(
            provider_id="agent-dispatch-test-provider",
            display_name="Agent Dispatch Test Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=["test-model"],
            credential_requirement="none",
        )
        self.call_count = 0

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self._mutate and self.call_count == 1:
            return LLMResponse(
                request_id=request.request_id,
                text_content="Proposing a mutation.",
                tool_calls=[{"name": "do_mutation", "arguments": {"target": "widget-dispatch"}}],
                token_usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            )
        return LLMResponse(
            request_id=request.request_id,
            text_content=f"answer to: {request.prompt}",
            tool_calls=[],
            token_usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True


async def _kernel_env(tmp_path: Path, *, mutate: bool) -> AsyncIterator[tuple[Kernel, _ScriptedProvider]]:
    db_path = (tmp_path / f"kortex_agent_dispatch_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    async def _mutate_handler(target: str | None = None, principal: Any = None, **kwargs: Any) -> dict[str, Any]:
        return {"status": "SUCCESS", "target": target}

    kernel.register_capability(
        name="test.agent_dispatch.mutate_action",
        description="Test-only mutating capability.",
        provider="test",
        handler=_mutate_handler,
        required_permissions=["test:mutate"],
    )

    provider = _ScriptedProvider(mutate=mutate)
    bridge = KernelBridgeAdapter(kernel)
    config = AIEngineRuntimeConfig(environment="production", enable_cloud_models=False)
    bootstrap = KernelProductionBootstrap(config=config)
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=bridge,
        data_store=data_store,
        custom_providers=[provider],
        registered_engines=list(kernel.get_all_engines().keys()),
    )
    if mutate:
        from kortex.engines.ai.tools import ToolDefinition

        ai_engine.tool_registry.register_tool(
            ToolDefinition(
                name="do_mutation",
                description="Mutating test tool.",
                canonical_capability="test.agent_dispatch.mutate_action",
                parameters_schema={"type": "object"},
                is_mutation=True,
            )
        )
    kernel.register_engine(ai_engine)

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="ai:orchestrate"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="ai:read"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_A,
                principal_id="user_agent_dispatch_a",
                principal_type="USER",
                credential_hash=hasher.hash("pass-a"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_B,
                principal_id="user_agent_dispatch_b",
                principal_type="USER",
                credential_hash=hasher.hash("pass-b"),
                roles=[],  # deliberately no ai:orchestrate -- RBAC-denial fixture
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    await storage_engine.data.execute_in_transaction(_seed_rbac)

    try:
        yield kernel, provider
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


@pytest.fixture
async def kernel_env_no_approval(tmp_path: Path) -> AsyncIterator[tuple[Kernel, _ScriptedProvider]]:
    async for pair in _kernel_env(tmp_path, mutate=False):
        yield pair


@pytest.fixture
async def kernel_env_with_approval(tmp_path: Path) -> AsyncIterator[tuple[Kernel, _ScriptedProvider]]:
    async for pair in _kernel_env(tmp_path, mutate=True):
        yield pair


async def _token(kernel: Kernel, tenant_id: str, principal_id: str, password: str) -> Any:
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": password}
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.asyncio
async def test_orchestrate_agent_via_real_dispatch_with_dict_task_completes(
    kernel_env_no_approval: tuple[Kernel, _ScriptedProvider],
) -> None:
    """Exactly what a desktop client sends: a plain JSON dict for `task`,
    not a live `AgentTask` — proves the M7.2 dispatch-coercion fix applies
    here too, and that orchestration is reachable through real
    authentication/RBAC, not just as a direct in-process call."""
    kernel, provider = kernel_env_no_approval
    token_a = await _token(kernel, _TENANT_A, "user_agent_dispatch_a", "pass-a")

    raw_task = {
        "task_id": "agent-dispatch-task-1",
        "tenant_id": _TENANT_A,
        "user_id": "user_agent_dispatch_a",
        "conversation_id": "conv-agent-dispatch-1",
        "goal": "Say hello",
    }
    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.ai.agent.orchestrate",
            session_token=token_a,
            parameters={"task": raw_task},
            context={"resource_tenant_id": _TENANT_A},
        )
    )

    assert result.status.value == "COMPLETED"
    assert result.final_response == "answer to: Goal: Say hello"
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_orchestrate_agent_via_real_dispatch_forces_principal_tenant(
    kernel_env_no_approval: tuple[Kernel, _ScriptedProvider],
) -> None:
    """A principal authenticated in tenant B cannot cause an agent task to
    be attributed to tenant A merely by setting AgentTask.tenant_id."""
    kernel, provider = kernel_env_no_approval
    storage_engine: StorageEngine = kernel.get_engine("storage")

    # The shared fixture's tenant-B principal deliberately has no
    # ai:orchestrate (it exists for the RBAC-denial test below) -- seed a
    # second, orchestration-capable tenant-B principal for this test only.
    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE + "_B2", permission="ai:orchestrate"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE + "_B2", permission="ai:read"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_B,
                principal_id="user_agent_dispatch_b2",
                principal_type="USER",
                credential_hash=hasher.hash("pass-b2"),
                roles=[_ROLE + "_B2"],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await storage_engine.data.execute_in_transaction(_seed)
    token_b = await _token(kernel, _TENANT_B, "user_agent_dispatch_b2", "pass-b2")

    spoofed_task = {
        "task_id": "agent-dispatch-spoof-1",
        "tenant_id": _TENANT_A,  # spoofed: caller is actually tenant B
        "user_id": "user_agent_dispatch_b2",
        "conversation_id": "conv-agent-dispatch-spoof-1",
        "goal": "attacker goal",
    }
    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.ai.agent.orchestrate",
            session_token=token_b,
            parameters={"task": spoofed_task},
            context={"resource_tenant_id": _TENANT_B},
        )
    )

    assert result.tenant_id == _TENANT_B
    assert result.tenant_id != _TENANT_A

    status_result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.ai.agent.status",
            session_token=token_b,
            parameters={"task_id": "agent-dispatch-spoof-1", "tenant_id": _TENANT_B},
            context={"resource_tenant_id": _TENANT_B},
        )
    )
    assert status_result is not None
    assert status_result.task.tenant_id == _TENANT_B


@pytest.mark.asyncio
async def test_orchestrate_agent_via_real_dispatch_denied_without_permission(
    kernel_env_no_approval: tuple[Kernel, _ScriptedProvider],
) -> None:
    kernel, _provider = kernel_env_no_approval
    token_b = await _token(kernel, _TENANT_B, "user_agent_dispatch_b", "pass-b")

    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.ai.agent.orchestrate",
                session_token=token_b,
                parameters={
                    "task": {
                        "task_id": "agent-dispatch-denied-1",
                        "tenant_id": _TENANT_B,
                        "user_id": "user_agent_dispatch_b",
                        "conversation_id": "conv-agent-dispatch-denied-1",
                        "goal": "should be denied",
                    }
                },
                context={"resource_tenant_id": _TENANT_B},
            )
        )


@pytest.mark.asyncio
async def test_orchestrate_then_resume_agent_via_real_dispatch_with_dict_parameters_completes(
    kernel_env_with_approval: tuple[Kernel, _ScriptedProvider],
) -> None:
    """The full round trip a desktop client actually performs: orchestrate
    (dict task) -> real PAUSED_FOR_APPROVAL result, JSON-serializable
    resume_token/pending_tool_calls -> resume (those same values fed back
    as plain dicts, exactly as they would arrive after a JSON round trip
    through /capabilities/invoke) -> COMPLETED. Does not exercise the
    durable-approval-ticket/human-decision loop itself (already fully
    proven end to end by `test_ai_durable_approval_vertical_slice.py`) —
    this test's job is narrower: prove `kortex.ai.agent.resume` itself is
    reachable through real dispatch with dict-shaped parameters, which no
    existing test proved before M7.2."""
    kernel, provider = kernel_env_with_approval
    token_a = await _token(kernel, _TENANT_A, "user_agent_dispatch_a", "pass-a")

    raw_task = {
        "task_id": "agent-dispatch-resume-1",
        "tenant_id": _TENANT_A,
        "user_id": "user_agent_dispatch_a",
        "conversation_id": "conv-agent-dispatch-resume-1",
        "goal": "Apply a mutation",
    }
    paused = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.ai.agent.orchestrate",
            session_token=token_a,
            parameters={"task": raw_task},
            context={"resource_tenant_id": _TENANT_A},
        )
    )
    assert paused.status.value == "PAUSED_FOR_APPROVAL"
    assert paused.resume_token is not None
    assert len(paused.pending_tool_calls) == 1

    # Simulate the JSON round trip a real desktop client would perform:
    # dump to plain dicts, exactly what /capabilities/invoke would hand
    # back and the client would echo forward for resume.
    resume_token_dict = paused.resume_token.model_dump()
    approved_calls_dicts = [call.model_dump() for call in paused.pending_tool_calls]

    resumed = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.ai.agent.resume",
            session_token=token_a,
            parameters={
                "task": raw_task,
                "resume_token": resume_token_dict,
                "approved_tool_calls": approved_calls_dicts,
            },
            context={"resource_tenant_id": _TENANT_A},
        )
    )

    assert resumed.status.value == "COMPLETED"
    assert provider.call_count == 2
