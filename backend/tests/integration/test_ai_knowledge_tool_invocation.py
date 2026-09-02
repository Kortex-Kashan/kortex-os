"""M7.5-W3 canonical vertical slice: AI Studio agent tool calls reaching the
Knowledge Engine.

A third, independent proof (after Connector, M7.3, and Document, M7.4) that
the AI tool-invocation chain generalizes to an unrelated target engine with
zero chain-level changes, driving the *real* production tool definition
(`kernel_bootstrap.register_knowledge_ai_tools`) against the *real*
Knowledge Engine, end to end:

    AI proposes `knowledge_search` (read, no approval)
        -> AIToolInvoker -> KernelToolExecutionPort -> CapabilityDispatcher
        -> KnowledgeEngine.search -> real seeded graph node -> conversation
        history

    cross-tenant: an agent orchestrated under tenant B, searching text that
    only matches tenant A's seeded node, gets an empty result -- the first
    real-dispatch proof of the M7.5-W1 tenant-derivation fix reached through
    the AI path specifically (T1/T11 in the M7.5 planning report's threat
    model)

Unlike `test_ai_connector_tool_invocation.py`/`test_ai_document_tool_
invocation.py`, there is no mutation/approval-flow test here: M7.5's AI
tool surface is deliberately read-only (see the M7.5 planning report §9,
§17 Q1) -- no approval, resume, rejection, or duplicate-event-idempotency
path exists for `knowledge_search` to exercise.

No component here is mocked below the Kernel boundary: real SecurityEngine,
real KnowledgeEngine, real AIOrchestrationEngine production wiring.

A final, standalone test proves T10 (planning report §16): a knowledge
search result can genuinely carry more text than a connector response, and
the existing, generic `ToolResult.to_context_entry` truncation is what
bounds it -- not a new mitigation this milestone invented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.kernel_bootstrap import _build_ai_system_identity, register_knowledge_ai_tools
from kortex.core.db import DatabaseEngineManager
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.agent import AgentTask
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.identity import AI_SYSTEM_ROLE
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.ai.tools import MAX_TOOL_OUTPUT_CHARS, ToolExecutionStatus, ToolResult
from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.knowledge.models import KnowledgeNode, KnowledgeQueryResult
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32
_TENANT_A = "tenant_ai_knowledge_a"
_TENANT_B = "tenant_ai_knowledge_b"


class _ScriptedProvider(BaseAIProvider):
    """Real, functioning provider proposing a scripted sequence of tool calls."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._metadata = AIProviderMetadata(
            provider_id="knowledge-vslice-provider",
            display_name="Knowledge Vertical Slice Test Provider",
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


async def _build_kernel(tmp_path: Path, provider: BaseAIProvider) -> tuple[Kernel, Any, KnowledgeEngine]:
    db_path = (tmp_path / f"kortex_ai_knowledge_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_ai_knowledge_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    knowledge_engine = KnowledgeEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(knowledge_engine)

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
    # Register the REAL M7.5 production tool definition -- not a test double.
    register_knowledge_ai_tools(ai_engine.tool_registry)
    kernel.register_engine(ai_engine)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    async def _seed_rbac(session: AsyncSession) -> None:
        for perm in ("ai:orchestrate", "ai:execute", "knowledge:read"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=AI_SYSTEM_ROLE, permission=perm))

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    return kernel, ai_engine, knowledge_engine


@pytest.mark.asyncio
async def test_read_only_knowledge_tool_executes_without_approval_and_records_history(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [
            {
                "text": "Searching the knowledge base.",
                "tool_calls": [
                    {
                        "name": "knowledge_search",
                        "arguments": {
                            "query": {"query_id": "ai-kq-1", "query_text": "Widget"},
                        },
                    }
                ],
            },
            {"text": "Found the Widget Assembly entry.", "tool_calls": []},
        ]
    )
    kernel, ai_engine, knowledge_engine = await _build_kernel(tmp_path, provider)
    try:
        knowledge_engine.graph.add_node(
            KnowledgeNode(node_id="node-a", tenant_id=_TENANT_A, entity_type="Concept", label="Widget Assembly")
        )

        task = AgentTask(
            task_id="knowledge-read-task-1",
            tenant_id=_TENANT_A,
            user_id="user-does-not-matter",
            conversation_id="conv-knowledge-read-1",
            goal="What do we know about Widget Assembly?",
        )
        result = await ai_engine.orchestrate_agent(task)

        assert result.status.value == "COMPLETED"
        tool_result = result.steps[0].tool_results[0]
        assert tool_result.status.value == "SUCCESS"
        output: KnowledgeQueryResult = tool_result.output
        node_ids = [n["node_id"] if isinstance(n, dict) else n.node_id for n in output.matching_nodes]
        assert node_ids == ["node-a"]

        history = await ai_engine.get_conversation_history(_TENANT_A, "conv-knowledge-read-1")
        assert len(history) == 1
        assert history[0].assistant_content == "Found the Widget Assembly entry."
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_cross_tenant_agent_cannot_search_another_tenants_knowledge(tmp_path: Path) -> None:
    """T1/T11: an agent orchestrated under tenant B, searching text that only
    matches tenant A's seeded node, must get an empty result -- proving the
    M7.5-W1 tenant-derivation fix holds through the AI path specifically.
    The tool schema itself carries no `tenant_id` field at all (M7.5-W3), so
    there is no argument for the LLM to even attempt spoofing; this test
    proves the resulting behavior end to end regardless."""
    provider = _ScriptedProvider(
        [
            {
                "text": "Searching the knowledge base.",
                "tool_calls": [
                    {
                        "name": "knowledge_search",
                        "arguments": {
                            "query": {"query_id": "ai-kq-cross-tenant", "query_text": "Widget"},
                        },
                    }
                ],
            },
            {"text": "No matching entries were found.", "tool_calls": []},
        ]
    )
    kernel, ai_engine, knowledge_engine = await _build_kernel(tmp_path, provider)
    try:
        # Tenant A owns this node; tenant B's agent must never see it.
        knowledge_engine.graph.add_node(
            KnowledgeNode(node_id="node-a", tenant_id=_TENANT_A, entity_type="Concept", label="Widget Assembly")
        )

        task = AgentTask(
            task_id="knowledge-cross-tenant-task-1",
            tenant_id=_TENANT_B,
            user_id="user-does-not-matter",
            conversation_id="conv-knowledge-cross-tenant-1",
            goal="What do we know about Widget Assembly?",
        )
        result = await ai_engine.orchestrate_agent(task)

        assert result.status.value == "COMPLETED"
        tool_result = result.steps[0].tool_results[0]
        assert tool_result.status.value == "SUCCESS"
        output: KnowledgeQueryResult = tool_result.output
        assert list(output.matching_nodes) == []
        assert list(output.matching_records) == []
    finally:
        await kernel.shutdown()


def test_large_knowledge_search_result_is_bounded_before_entering_conversation_history() -> None:
    """T10 (planning report §16): a knowledge search result can genuinely
    carry substantially more text than a connector response (many matching
    records/nodes, each with real label/content text) -- proves the
    existing, generic `ToolResult.to_context_entry` truncation actually
    bounds a result shaped like `knowledge_search`'s real output. Not a new
    mitigation this milestone built; the same backstop M7.3/M7.4 already
    rely on, verified here against this tool's own result shape rather than
    assumed to apply."""
    large_nodes = [
        KnowledgeNode(
            node_id=f"node-{i}",
            tenant_id=_TENANT_A,
            entity_type="Concept",
            label="X" * 2000,
        )
        for i in range(100)
    ]
    result = KnowledgeQueryResult(
        query_id="req-large",
        matching_nodes=large_nodes,
        execution_time_ms=1.0,
    )

    tool_result = ToolResult(
        call_id="call-large",
        tool_name="knowledge_search",
        status=ToolExecutionStatus.SUCCESS,
        output=result,
    )

    context_entry = tool_result.to_context_entry()

    assert len(context_entry) < MAX_TOOL_OUTPUT_CHARS + 1000  # payload + marker/status overhead
    assert "TRUNCATED" in context_entry
