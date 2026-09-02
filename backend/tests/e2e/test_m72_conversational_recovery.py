"""M7.2 — the required conversational-recovery acceptance test.

Exercises the two E2E scenarios that specifically depend on surviving a
real application restart (the other two required scenarios -- the approval
flow and the rejection flow -- are already proven end-to-end, through the
real event-driven auto-resume chain, by
`tests/integration/test_ai_durable_approval_vertical_slice.py`; this file
does not duplicate that coverage):

    NORMAL CONVERSATION
          |            (kortex.ai.agent.orchestrate -> COMPLETED,
          |             the same capability the desktop Chat tab calls for
          |             every message -- see apps/desktop/src/features/
          |             ai-studio/chat-api.ts)
    DURABLE HISTORY WRITTEN
          |            (kortex.ai.conversation.history.get returns it,
          |             within the same process -- a sanity check before
          |             the real assertion below)
    RESTART APPLICATION
          |            (the FastAPI `app.state.kernel` lifespan is torn
          |             down and rebuilt from scratch, exactly as
          |             `test_m71_cold_start.py`'s own restart does)
    CONVERSATION RECOVERED
                       (kortex.ai.conversation.history.get, called through
                        a brand-new Kernel/AI engine instance with zero
                        in-memory state carried over, still returns the
                        exact same turn -- proving the desktop's
                        `useConversation` hook can genuinely rebuild its
                        transcript after a restart, not merely within one
                        long-lived process)

Does not manually start uvicorn (`TestClient` drives the real FastAPI
`app`, including its real `lifespan` -> `build_and_boot_kernel()` -> real
engine boot sequence, exactly as `test_m71_cold_start.py`). Isolates both
`KORTEX_STORAGE_DIR` and `KORTEX_DATABASE_URL` to a `tmp_path`-scoped
SQLite file, kept identical across the restart -- the same requirement
`test_m71_cold_start.py` documents for the same reason.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.main import app
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine

pytestmark = pytest.mark.e2e

_MASTER_KEY = "0x" + ("cc" * 32)
_SIGNING_KEY = "0x" + ("dd" * 32)
_TENANT = "acme"
_ROLE = "chat-e2e-role"


class _RecoveryTestProvider(BaseAIProvider):
    """A real, functioning provider that never depends on Ollama being
    reachable -- registered directly on the real production-booted AI
    engine on each launch below, exactly as `test_ai_studio_api_http.py`'s
    own `_HttpTestProvider` does for the same reason."""

    def __init__(self) -> None:
        self._metadata = AIProviderMetadata(
            provider_id="recovery-test-provider",
            display_name="Recovery Test Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=["recovery-test-model"],
            credential_requirement="none",
        )

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
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


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv(
        "KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'conversational_recovery.db').as_posix()}"
    )
    monkeypatch.setenv("KORTEX_MASTER_KEY", _MASTER_KEY)
    monkeypatch.setenv("KORTEX_AUTH_SIGNING_PRIVATE_KEY", _SIGNING_KEY)


def _invoke(client: TestClient, capability_name: str, parameters: dict[str, Any], token: str | None = None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        "/capabilities/invoke",
        json={"requestId": "req-1", "capabilityName": capability_name, "parameters": parameters},
        headers=headers,
    )


async def _seed_principal_and_login(client: TestClient) -> str:
    kernel = app.state.kernel
    storage: StorageEngine = kernel.get_engine("storage")
    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id="perm-orchestrate", role=_ROLE, permission="ai:orchestrate"))
        session.add(RolePermissionRecord(id="perm-read", role=_ROLE, permission="ai:read"))
        session.add(
            PrincipalRecord(
                id="principal-owner",
                tenant_id=_TENANT,
                principal_id="owner",
                principal_type="USER",
                enabled=True,
                credential_hash=hasher.hash("correct horse battery staple"),
                roles=[_ROLE],
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await storage.data.execute_in_transaction(_seed)

    login = _invoke(
        client,
        "kortex.security.auth.authenticate",
        {
            "credentials": {
                "principal_type": "USER",
                "tenant_id": _TENANT,
                "principal_id": "owner",
                "password": "correct horse battery staple",
            }
        },
    )
    assert login.status_code == 200, login.text
    token = login.json()["sessionToken"]
    assert token
    return token


@pytest.mark.usefixtures("isolated_env")
def test_normal_conversation_survives_a_real_application_restart() -> None:
    conversation_id = "conv-e2e-recovery-1"

    # --- Launch 1: normal conversation ----------------------------------
    with TestClient(app) as client:
        kernel = app.state.kernel
        ai_engine = kernel.get_engine("ai")
        ai_engine.register_provider(_RecoveryTestProvider())

        token = asyncio.run(_seed_principal_and_login(client))

        # NORMAL CONVERSATION -- the exact capability the desktop Chat tab
        # calls for every message (see `chat-api.ts::sendAgentMessage`),
        # sent as a plain JSON dict body, exactly as the desktop's
        # `invokeCapability` transport sends it.
        sent = _invoke(
            client,
            "kortex.ai.agent.orchestrate",
            {
                "task": {
                    "task_id": "task-e2e-recovery-1",
                    "tenant_id": _TENANT,
                    "user_id": "owner",
                    "conversation_id": conversation_id,
                    "goal": "What is the capital of France?",
                }
            },
            token=token,
        )
        assert sent.status_code == 200, sent.text
        result = sent.json()["payload"]["result"]
        assert result["status"] == "COMPLETED"
        assert result["final_response"] is not None

        # DURABLE HISTORY WRITTEN -- sanity check within the same process,
        # before the real cross-restart assertion below.
        history_before_restart = _invoke(
            client,
            "kortex.ai.conversation.history.get",
            {"tenant_id": _TENANT, "conversation_id": conversation_id},
            token=token,
        )
        assert history_before_restart.status_code == 200, history_before_restart.text
        turns_before = history_before_restart.json()["payload"]["result"]
        assert len(turns_before) == 1
        assert turns_before[0]["user_content"] == "What is the capital of France?"

    # --- RESTART APPLICATION ---------------------------------------------
    # Exiting the `with TestClient(app)` block above already ran the real
    # `_lifespan` shutdown path. Entering a fresh one re-runs `_lifespan`
    # startup -> `build_and_boot_kernel()` from scratch -- a brand-new
    # Kernel and AI engine, nothing carried over in memory -- while
    # `KORTEX_DATABASE_URL` remains identical, exactly as a real process
    # restart looks from the backend's own perspective.
    with TestClient(app) as client:
        kernel = app.state.kernel
        ai_engine = kernel.get_engine("ai")
        ai_engine.register_provider(_RecoveryTestProvider())

        # A fresh session: the prior one lived only in the torn-down
        # kernel's in-memory session store.
        login = _invoke(
            client,
            "kortex.security.auth.authenticate",
            {
                "credentials": {
                    "principal_type": "USER",
                    "tenant_id": _TENANT,
                    "principal_id": "owner",
                    "password": "correct horse battery staple",
                }
            },
        )
        assert login.status_code == 200, login.text
        token_after_restart = login.json()["sessionToken"]

        # CONVERSATION RECOVERED -- the real assertion this test exists
        # for: the turn written before "restart" is still readable through
        # a completely fresh Kernel/AI engine instance, proving durability
        # never depended on anything held only in memory.
        history_after_restart = _invoke(
            client,
            "kortex.ai.conversation.history.get",
            {"tenant_id": _TENANT, "conversation_id": conversation_id},
            token=token_after_restart,
        )
        assert history_after_restart.status_code == 200, history_after_restart.text
        turns_after = history_after_restart.json()["payload"]["result"]
        assert len(turns_after) == 1
        assert turns_after[0]["user_content"] == "What is the capital of France?"
        assert turns_after[0]["assistant_content"] == turns_before[0]["assistant_content"]
