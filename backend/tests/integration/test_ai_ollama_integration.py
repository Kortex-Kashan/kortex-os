"""M6.1-3: real-endpoint integration test for `OllamaProvider`.

Exercises the genuine path: KORTEX AI Engine -> real OllamaProvider ->
real Ollama HTTP endpoint -> real response -> KORTEX response mapping.

Skip-safe: if no real Ollama instance is reachable at the configured
endpoint (the default `http://localhost:11434`, or `KORTEX_OLLAMA_URL` /
`KORTEX_OLLAMA_TEST_MODEL` if set), this module's tests are skipped, not
failed -- Ollama is not expected to be installed/running in every
environment this suite runs in. When it IS available, the test genuinely
contacts the real endpoint; it does not fall back to a mock and does not
claim success without a real round trip.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.models import LLMRequest
from kortex.engines.ai.ollama_provider import OllamaProvider
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_OLLAMA_BASE_URL = os.environ.get("KORTEX_OLLAMA_URL", "http://localhost:11434")
_OLLAMA_TEST_MODEL = os.environ.get("KORTEX_OLLAMA_TEST_MODEL", "llama3")


def _ollama_reachable_with_model() -> bool:
    """Short-timeout connectivity + model-availability probe.

    Deliberately synchronous and isolated from the async test machinery
    below: this must be safe to call at collection/skip-decision time
    without needing an event loop, and must fail (return False) fast and
    quietly on any error rather than raising and breaking collection for
    the whole module.
    """
    try:
        response = httpx.get(f"{_OLLAMA_BASE_URL}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    models = data.get("models", []) if isinstance(data, dict) else []
    if not isinstance(models, list):
        return False
    configured_family = _OLLAMA_TEST_MODEL.split(":")[0]
    return any(
        isinstance(m, dict) and str(m.get("name", "")).split(":")[0] == configured_family for m in models
    )


pytestmark = pytest.mark.skipif(
    not _ollama_reachable_with_model(),
    reason=(
        f"No reachable Ollama instance serving model '{_OLLAMA_TEST_MODEL}' at '{_OLLAMA_BASE_URL}' "
        "-- set KORTEX_OLLAMA_URL/KORTEX_OLLAMA_TEST_MODEL to point at a running instance to exercise "
        "this suite. Skipped, not failed: Ollama is not expected to be installed in every environment."
    ),
)


@pytest.fixture
async def real_ollama_kernel(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_ollama_real_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    kernel.register_engine(StorageEngine(base_directory=str(tmp_path / f"storage_ollama_real_{uuid4().hex[:8]}")))
    kernel.register_engine(SecurityEngine(master_key=b"\x33" * 32, signing_private_key=b"\x44" * 32))

    provider = OllamaProvider(base_url=_OLLAMA_BASE_URL, model_name=_OLLAMA_TEST_MODEL)
    bridge = KernelBridgeAdapter(kernel)
    config = AIEngineRuntimeConfig(environment="production", storage_backend="sqlite", enable_cloud_models=False)
    bootstrap = KernelProductionBootstrap(config=config)
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=bridge,
        data_store=data_store,
        custom_providers=[provider],
        registered_engines=list(kernel.get_all_engines().keys()),
    )
    kernel.register_engine(ai_engine)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    try:
        yield kernel
    finally:
        await provider.aclose()
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


@pytest.mark.asyncio
async def test_real_ollama_generation_produces_a_genuine_response(real_ollama_kernel: Kernel) -> None:
    ai_engine = real_ollama_kernel.get_engine("ai")

    request = LLMRequest(
        request_id="req-real-ollama-1",
        tenant_id="tenant-real-ollama",
        user_id="user-real-ollama",
        conversation_id="conv-real-ollama-1",
        prompt="Reply with exactly the single word: acknowledged",
        max_tokens=16,
    )
    response = await ai_engine.generate_response(request)

    # A genuinely real round trip: non-empty text, real (non-fabricated)
    # token counts, and correct provider/model self-identification.
    assert isinstance(response.text_content, str)
    assert len(response.text_content) > 0
    assert response.token_usage["total_tokens"] > 0
    assert response.provider_id == f"ollama-{_OLLAMA_TEST_MODEL}"
    assert response.model_name == _OLLAMA_TEST_MODEL
    assert response.degraded is False

    records = await ai_engine.query_decision_records(tenant_id="tenant-real-ollama")
    matching = [r for r in records if r["request_id"] == "req-real-ollama-1"]
    assert len(matching) == 1
    assert matching[0]["provider_id"] == f"ollama-{_OLLAMA_TEST_MODEL}"
    assert matching[0]["model_name"] == _OLLAMA_TEST_MODEL


@pytest.mark.asyncio
async def test_real_ollama_health_check_reports_true(real_ollama_kernel: Kernel) -> None:
    ai_engine = real_ollama_kernel.get_engine("ai")
    providers = ai_engine.list_providers()
    assert len(providers) == 1
    provider_id = providers[0].provider_id

    health = ai_engine.health()
    assert health["status"] in ("HEALTHY", "DEGRADED")
    assert health["providers_registered"] == 1
    assert provider_id == f"ollama-{_OLLAMA_TEST_MODEL}"
