"""Phase C (AI Studio functional stabilization): `kortex.ai.conversation.list`
through the real Kernel Capability Enforcement Boundary.

Drives the actual dispatch path (real Storage + Security Engines, real
authentication, real RBAC, a real -- but network-free -- AI provider) so
tenant/user isolation is proven against the genuine verified-identity
mechanism `_authoritative_tenant_id`/`list_conversations` rely on, not a
hand-constructed execution context a real caller could never produce.

Mirrors the harness shape `test_ai_provider_test_connection_dispatch.py`
and `tests/e2e/test_m72_conversational_recovery.py` already use for the
same reason.
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
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\xab" * 32
_TEST_SIGNING_KEY = b"\xcd" * 32
_ROLE = "AI_CONVERSATION_LIST_ROLE"
_TENANT_A = "tenant_a_conv_list"
_TENANT_B = "tenant_b_conv_list"


class _EchoProvider(BaseAIProvider):
    """A real, functioning, network-free provider — registered directly on
    the production-booted AI engine, exactly as the M7.2 e2e recovery
    test's own `_RecoveryTestProvider` does, so `kortex.ai.agent.orchestrate`
    genuinely writes durable conversation turns without needing any
    external network mock."""

    def __init__(self) -> None:
        self._metadata = AIProviderMetadata(
            provider_id="echo-test-provider",
            display_name="Echo Test Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=["echo-test-model"],
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


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_conv_list_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_conv_list_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="production", storage_backend="sqlite", enable_cloud_models=False)
    )
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=KernelBridgeAdapter(kernel),  # type: ignore[arg-type]
        data_store=data_store,
        custom_providers=[_EchoProvider()],
        registered_engines=list(kernel.get_all_engines().keys()),
        secret_getter=security_engine.get_secret,
        secret_putter=security_engine.put_secret,
    )
    kernel.register_engine(ai_engine)

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        for permission in ("ai:orchestrate", "ai:read"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission=permission))
        for tenant, principal in (
            (_TENANT_A, "alice"),
            (_TENANT_A, "bob"),
            (_TENANT_B, "carol"),
        ):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant,
                    principal_id=principal,
                    principal_type="USER",
                    credential_hash=hasher.hash(f"pass-{principal}"),
                    roles=[_ROLE],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )
        await session.flush()

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    await storage_engine.data.execute_in_transaction(_seed)

    try:
        yield kernel
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _token(kernel: Kernel, tenant_id: str, principal_id: str) -> Any:
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": f"pass-{principal_id}",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _invoke(kernel: Kernel, capability: str, token: Any, tenant: str, **parameters: Any) -> Any:
    return await kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability,
            session_token=token,
            parameters=parameters,
            context={"resource_tenant_id": tenant},
        )
    )


async def _send_message(
    kernel: Kernel, token: Any, tenant_id: str, user_id: str, conversation_id: str, goal: str
) -> None:
    result = await _invoke(
        kernel,
        "kortex.ai.agent.orchestrate",
        token,
        tenant_id,
        task={
            "task_id": str(uuid4()),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "goal": goal,
        },
    )
    assert result.status == "COMPLETED", result


# ---------------------------------------------------------------------------
# Core behavior: single/multiple conversations, ordering, titles, timestamps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversations_is_empty_for_a_user_with_no_history(kernel_env: Kernel) -> None:
    token = await _token(kernel_env, _TENANT_A, "alice")

    result = await _invoke(kernel_env, "kortex.ai.conversation.list", token, _TENANT_A)

    assert result == []


@pytest.mark.asyncio
async def test_list_conversations_returns_one_conversation_with_title_and_timestamps(kernel_env: Kernel) -> None:
    token = await _token(kernel_env, _TENANT_A, "alice")
    await _send_message(kernel_env, token, _TENANT_A, "alice", "conv-1", "What is the capital of France?")

    [summary] = await _invoke(kernel_env, "kortex.ai.conversation.list", token, _TENANT_A)

    assert summary.conversation_id == "conv-1"
    assert summary.title == "What is the capital of France?"
    assert summary.first_activity_at is not None
    assert summary.last_activity_at is not None


@pytest.mark.asyncio
async def test_list_conversations_multiple_ordered_most_recently_active_first(kernel_env: Kernel) -> None:
    token = await _token(kernel_env, _TENANT_A, "alice")
    await _send_message(kernel_env, token, _TENANT_A, "alice", "conv-older", "the first conversation I started")
    await _send_message(kernel_env, token, _TENANT_A, "alice", "conv-newer", "a brand new second conversation")

    summaries = await _invoke(kernel_env, "kortex.ai.conversation.list", token, _TENANT_A)

    assert [s.conversation_id for s in summaries] == ["conv-newer", "conv-older"]


@pytest.mark.asyncio
async def test_existing_history_get_remains_intact_alongside_the_new_list_capability(kernel_env: Kernel) -> None:
    """Requirement: `kortex.ai.conversation.history.get`'s existing
    behavior (tenant + explicit conversation_id, unchanged signature) must
    not regress now that `kortex.ai.conversation.list` exists alongside it."""
    token = await _token(kernel_env, _TENANT_A, "alice")
    await _send_message(kernel_env, token, _TENANT_A, "alice", "conv-1", "What is the capital of France?")

    turns = await _invoke(
        kernel_env,
        "kortex.ai.conversation.history.get",
        token,
        _TENANT_A,
        tenant_id=_TENANT_A,
        conversation_id="conv-1",
    )

    assert len(turns) == 1
    assert turns[0].user_content == "What is the capital of France?"
    assert "answer to:" in turns[0].assistant_content
    # This is the same object shape/attribute access `list_conversations`
    # returns too -- confirming both capabilities keep returning the
    # existing, in-process `ConversationTurn`/`ConversationSummary`
    # Pydantic objects, not a shape that changed for either.


# ---------------------------------------------------------------------------
# Security / tenancy: the actual point of Phase C's requirement 4
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversations_never_leaks_across_tenants(kernel_env: Kernel) -> None:
    token_a = await _token(kernel_env, _TENANT_A, "alice")
    token_c = await _token(kernel_env, _TENANT_B, "carol")
    await _send_message(kernel_env, token_a, _TENANT_A, "alice", "conv-a", "tenant A's private conversation")
    await _send_message(kernel_env, token_c, _TENANT_B, "carol", "conv-c", "tenant B's private conversation")

    summaries_a = await _invoke(kernel_env, "kortex.ai.conversation.list", token_a, _TENANT_A)
    summaries_c = await _invoke(kernel_env, "kortex.ai.conversation.list", token_c, _TENANT_B)

    assert [s.conversation_id for s in summaries_a] == ["conv-a"]
    assert [s.conversation_id for s in summaries_c] == ["conv-c"]
    assert "tenant B" not in str(summaries_a)
    assert "tenant A" not in str(summaries_c)


@pytest.mark.asyncio
async def test_list_conversations_never_leaks_across_users_within_one_tenant(kernel_env: Kernel) -> None:
    token_alice = await _token(kernel_env, _TENANT_A, "alice")
    token_bob = await _token(kernel_env, _TENANT_A, "bob")
    await _send_message(kernel_env, token_alice, _TENANT_A, "alice", "conv-alice", "Alice's private question")
    await _send_message(kernel_env, token_bob, _TENANT_A, "bob", "conv-bob", "Bob's private question")

    summaries_alice = await _invoke(kernel_env, "kortex.ai.conversation.list", token_alice, _TENANT_A)
    summaries_bob = await _invoke(kernel_env, "kortex.ai.conversation.list", token_bob, _TENANT_A)

    assert [s.conversation_id for s in summaries_alice] == ["conv-alice"]
    assert [s.conversation_id for s in summaries_bob] == ["conv-bob"]
    assert "Bob" not in str(summaries_alice)
    assert "Alice" not in str(summaries_bob)


@pytest.mark.asyncio
async def test_orchestrate_agent_forged_user_id_never_appears_in_the_impersonated_users_conversation_list(
    kernel_env: Kernel,
) -> None:
    """Closeout security fix: `task.user_id` is caller-supplied data, just
    like `task.tenant_id` -- before this fix, only `tenant_id` was
    re-bound to the verified principal, so an authenticated caller (bob)
    could set `user_id="alice"` in the task payload and have the resulting
    conversation turn durably recorded under alice's identity. Because
    `kortex.ai.conversation.list` scopes strictly by the VERIFIED
    principal, this made the forged conversation appear in alice's own
    "Recent Conversations" -- a real cross-user identity forgery, not a
    theoretical one. Proves the fix: the conversation is attributed to
    bob (the real, verified caller), never to alice, regardless of what
    `user_id` bob's request claims."""
    token_alice = await _token(kernel_env, _TENANT_A, "alice")
    token_bob = await _token(kernel_env, _TENANT_A, "bob")

    # bob's own genuine token, but the TASK PAYLOAD claims to be alice --
    # exactly the forgeable-caller-data class `tenant_id` forgery already
    # covers below, now for `user_id`.
    await _send_message(kernel_env, token_bob, _TENANT_A, "alice", "conv-forged", "pretending to be alice")

    summaries_alice = await _invoke(kernel_env, "kortex.ai.conversation.list", token_alice, _TENANT_A)
    summaries_bob = await _invoke(kernel_env, "kortex.ai.conversation.list", token_bob, _TENANT_A)

    assert [s.conversation_id for s in summaries_alice] == [], "the forged conversation must never reach alice"
    assert [s.conversation_id for s in summaries_bob] == ["conv-forged"], "it must be attributed to the real caller"


@pytest.mark.asyncio
async def test_list_conversations_rejects_a_forged_tenant_context(kernel_env: Kernel) -> None:
    """A caller cannot list another tenant's conversations by forging the
    dispatch context's `resource_tenant_id`. This is rejected at the
    dispatcher's own authorization step (`CapabilityDispatcher.dispatch`
    compares `resource_tenant_id` against the verified principal's tenant
    before the handler ever runs) — a stronger guarantee than merely
    "the handler ignores it", since a mismatched claim never reaches
    `list_conversations` at all."""
    token_carol = await _token(kernel_env, _TENANT_B, "carol")

    # carol's own, genuinely issued token, but the request CONTEXT claims
    # tenant A -- the forgeable part of a request, not the verified part.
    with pytest.raises(AuthorizationDeniedError):
        await _invoke(kernel_env, "kortex.ai.conversation.list", token_carol, _TENANT_A)


@pytest.mark.asyncio
async def test_list_conversations_denies_an_unauthenticated_caller(kernel_env: Kernel) -> None:
    with pytest.raises(AuthenticationError):
        await kernel_env.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.ai.conversation.list",
                session_token=None,
                parameters={},
                context={"resource_tenant_id": _TENANT_A},
            )
        )


@pytest.mark.asyncio
async def test_list_conversations_denies_a_caller_without_ai_read_permission(kernel_env: Kernel) -> None:
    hasher = PasswordHasher()
    storage_engine: StorageEngine = kernel_env.get_engine("storage")

    async def _seed_unprivileged(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_A,
                principal_id="no-read-permission-user",
                principal_type="USER",
                credential_hash=hasher.hash("pass-no-read-permission-user"),
                roles=[],  # no ai:read grant at all
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        await session.flush()

    await storage_engine.data.execute_in_transaction(_seed_unprivileged)
    token = await _token(kernel_env, _TENANT_A, "no-read-permission-user")

    with pytest.raises(AuthorizationDeniedError):
        await _invoke(kernel_env, "kortex.ai.conversation.list", token, _TENANT_A)
