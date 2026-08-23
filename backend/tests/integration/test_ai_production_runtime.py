"""End-to-end integration tests for AI Engine Production Runtime (Milestone 9.4).

Tests verify the complete production lifecycle:
Kernel -> KernelBridgeAdapter -> KernelProductionBootstrap -> AIOrchestrationEngine -> Capability Invocation
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.bootstrap import (
    AIEngineRuntimeConfig,
    KernelProductionBootstrap,
)
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.models import (
    AIProviderMetadata,
    LLMRequest,
    LLMResponse,
)
from kortex.engines.storage.stores.data_store import RelationalDataStore

TENANT_ID = "tenant-e2e"
CONVERSATION_ID = "conv-e2e-1"


class IntegrationTestProvider(BaseAIProvider):
    """Predictable AI Provider for end-to-end runtime integration testing."""

    def __init__(self, provider_id: str = "ollama-local") -> None:
        self._provider_id = provider_id
        self._metadata = AIProviderMetadata(
            provider_id=provider_id,
            display_name=f"Integration Provider ({provider_id})",
            vendor="local",
            endpoint_type="local_host",
            supported_models=["llama-3-8b"],
            credential_requirement="none",
        )

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def supported_models(self) -> list[str]:
        return ["llama-3-8b"]

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            request_id=request.request_id,
            text_content=f"Integration answer for prompt: {request.prompt}",
            tool_calls=[],
            token_usage={},
            execution_time_ms=0.0,
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.5, 0.5] for _ in texts]

    async def health_check(self) -> bool:
        return True


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, RelationalDataStore]]:
    """Construct an initialized Kernel and isolated SQLite database for integration testing."""
    db_path = (tmp_path / "kortex_integration.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    data_store = RelationalDataStore(db_manager)

    # Register Storage & Security Engines into Kernel so dependency graph is fully satisfied
    from kortex.engines.security.engine import SecurityEngine
    from kortex.engines.storage.engine import StorageEngine

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage_data"))
    security_engine = SecurityEngine(
        master_key=b"0" * 32,
        signing_private_key=b"1" * 32,
    )
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    try:
        yield kernel, data_store
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


@pytest.mark.asyncio
async def test_production_runtime_bootstrap_and_kernel_lifecycle(
    kernel_env: tuple[Kernel, RelationalDataStore],
) -> None:
    """Verify full production bootstrap, engine registration, kernel boot, and capability execution."""
    kernel, data_store = kernel_env

    # 1. Create Kernel Bridge Adapter
    bridge = KernelBridgeAdapter(kernel)

    # 2. Bootstrap AI Engine with production configuration
    config = AIEngineRuntimeConfig(
        environment="production",
        storage_backend="sqlite",
        enable_cloud_models=False,
    )
    bootstrap = KernelProductionBootstrap(config=config)

    provider = IntegrationTestProvider("ollama-local")
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=bridge,
        data_store=data_store,
        custom_providers=[provider],
        registered_engines=list(kernel.get_all_engines().keys()),
    )

    # 3. Register AI Engine with Kernel
    kernel.register_engine(ai_engine)

    # 4. Boot Kernel
    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    assert ai_engine.state.name in ("READY", "RUNNING")

    # 5. Execute capability through Kernel registry handler
    handler = kernel._registry_engine.get_raw_handler_for_testing("kortex.ai.response.generate")
    llm_req = LLMRequest(
        request_id="req-e2e-1",
        tenant_id=TENANT_ID,
        user_id="user-e2e",
        conversation_id=CONVERSATION_ID,
        prompt="What is the capital of France?",
    )
    result = await handler(request=llm_req)

    assert isinstance(result, LLMResponse)
    assert result.request_id == "req-e2e-1"
    assert "Integration answer" in result.text_content

    # 6. Verify durable conversation persistence
    turns = await ai_engine.memory_manager.get_turns(TENANT_ID, CONVERSATION_ID)
    assert len(turns) == 1
    assert turns[0].sequence == 1
    assert "capital of France" in turns[0].user_content
    assert "Integration answer" in turns[0].assistant_content

    # 7. Check diagnostics & health
    diag = ai_engine.diagnostics()
    assert diag["engine"] == "ai"
    assert len(diag["providers"]) == 1

    health = ai_engine.health()
    assert health["engine"] == "ai"
    assert health["providers_registered"] == 1
    assert health["status"] == "HEALTHY"

    # 8. Graceful shutdown
    await kernel.shutdown()
    assert kernel.state == KernelState.STOPPED
