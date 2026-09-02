"""M6.1-1 regression suite: tenant-isolation fix on `generate_response`.

Prior to this fix, `generate_response` declared no `principal` parameter, so
the Kernel dispatcher never injected a verified identity into it — tenant
scope for governance, quota, persistence, and audit came entirely from the
caller-constructed `LLMRequest.tenant_id` field, with nothing cross-checking
it against the authenticated caller's real tenant. This is the same class of
gap M6.0-3 closed on 12 Workflow Engine handlers.

Every test here drives the real Kernel capability-dispatch boundary — real
`SecurityEngine` authentication, real RBAC, real `kernel.invoke_capability`
— not a raw-handler shortcut and not a direct in-process call to
`generate_response`, mirroring the M6.0-3 adversarial-test methodology.
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
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\xee" * 32
_TEST_SIGNING_KEY = b"\xff" * 32
_ROLE = "AI_TENANT_ISOLATION_TEST_ROLE"
_TENANT_A = "tenant_a_ai_iso"
_TENANT_B = "tenant_b_ai_iso"


class _RecordingProvider(BaseAIProvider):
    """Real, functioning test provider — proves generation actually ran, not merely
    that dispatch resolved. Deliberately not a no-op: records every request it sees."""

    def __init__(self) -> None:
        self._metadata = AIProviderMetadata(
            provider_id="test-provider-iso",
            display_name="Tenant Isolation Test Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=["test-model"],
            credential_requirement="none",
        )
        self.seen_requests: list[LLMRequest] = []

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        self.seen_requests.append(request)
        return LLMResponse(
            request_id=request.request_id,
            text_content=f"answer to: {request.prompt}",
            tool_calls=[],
            token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            execution_time_ms=1.0,
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, Any, _RecordingProvider]]:
    db_path = (tmp_path / f"kortex_ai_iso_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_ai_iso_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    bridge = KernelBridgeAdapter(kernel)
    config = AIEngineRuntimeConfig(environment="production", storage_backend="sqlite", enable_cloud_models=False)
    bootstrap = KernelProductionBootstrap(config=config)
    provider = _RecordingProvider()
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=bridge,
        data_store=data_store,
        custom_providers=[provider],
        registered_engines=list(kernel.get_all_engines().keys()),
    )
    kernel.register_engine(ai_engine)

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="ai:generate"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_A,
                principal_id="user_ai_iso_a",
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
                principal_id="user_ai_iso_b",
                principal_type="USER",
                credential_hash=hasher.hash("pass-b"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        await session.flush()

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    try:
        yield kernel, ai_engine, provider
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _token(kernel: Kernel, tenant_id: str, principal_id: str, password: str):
    security_engine: SecurityEngine = kernel.get_engine("security")
    auth = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": password}
    )
    return await security_engine.authentication_manager.issue_token(auth)


@pytest.mark.asyncio
async def test_generate_response_forces_principal_tenant_not_spoofed_request_tenant(kernel_env) -> None:
    """A principal authenticated in tenant B cannot cause a generation to be attributed
    to tenant A merely by setting LLMRequest.tenant_id="tenant_a_ai_iso"."""
    kernel, ai_engine, provider = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    spoofed_request = LLMRequest(
        request_id="req-iso-1",
        tenant_id=_TENANT_A,  # spoofed: caller is actually tenant B
        user_id="user_ai_iso_b",
        conversation_id="conv-iso-1",
        prompt="hello from an attacker",
    )

    response = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.ai.response.generate",
            session_token=token_b,
            parameters={"request": spoofed_request},
            context={"resource_tenant_id": _TENANT_B},
        )
    )
    assert response.text_content == "answer to: hello from an attacker"

    # The provider itself must have seen the CORRECTED tenant, not the spoofed one --
    # proves the fix applies before context composition / provider execution, not
    # only at the governance/audit layer.
    assert len(provider.seen_requests) == 1
    assert provider.seen_requests[0].tenant_id == _TENANT_B
    assert provider.seen_requests[0].tenant_id != _TENANT_A

    # Conversation history must be recorded under the REAL tenant (B), never the
    # spoofed one (A).
    turns_b = await ai_engine.memory_manager.get_turns(_TENANT_B, "conv-iso-1")
    assert len(turns_b) == 1
    turns_a = await ai_engine.memory_manager.get_turns(_TENANT_A, "conv-iso-1")
    assert len(turns_a) == 0

    # The audit record must be queryable under tenant B, and must not appear
    # under tenant A.
    records_b = await ai_engine.query_decision_records(tenant_id=_TENANT_B)
    assert any(r["request_id"] == "req-iso-1" for r in records_b)
    records_a = await ai_engine.query_decision_records(tenant_id=_TENANT_A)
    assert not any(r["request_id"] == "req-iso-1" for r in records_a)


@pytest.mark.asyncio
async def test_generate_response_quota_cannot_be_charged_to_another_tenant(kernel_env) -> None:
    """Real token usage from a spoofed-tenant-A request must debit tenant B's quota
    (the real caller), never tenant A's."""
    kernel, ai_engine, _provider = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    quota_manager = ai_engine.governance_manager.quota_manager
    quota_a_before = await quota_manager.get_or_create_quota(_TENANT_A)
    quota_b_before = await quota_manager.get_or_create_quota(_TENANT_B)

    spoofed_request = LLMRequest(
        request_id="req-iso-quota-1",
        tenant_id=_TENANT_A,
        user_id="user_ai_iso_b",
        conversation_id="conv-iso-quota-1",
        prompt="consume my quota, attacker",
    )
    await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.ai.response.generate",
            session_token=token_b,
            parameters={"request": spoofed_request},
            context={"resource_tenant_id": _TENANT_B},
        )
    )

    quota_a_after = await quota_manager.get_or_create_quota(_TENANT_A)
    quota_b_after = await quota_manager.get_or_create_quota(_TENANT_B)

    assert quota_a_after.daily_tokens_consumed == quota_a_before.daily_tokens_consumed
    assert quota_b_after.daily_tokens_consumed > quota_b_before.daily_tokens_consumed


@pytest.mark.asyncio
async def test_legitimate_same_tenant_generation_still_works(kernel_env) -> None:
    """Regression guard: a principal generating under its own, correctly-stated
    tenant is entirely unaffected by the fix."""
    kernel, ai_engine, provider = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_ai_iso_a", "pass-a")

    request = LLMRequest(
        request_id="req-iso-legit-1",
        tenant_id=_TENANT_A,
        user_id="user_ai_iso_a",
        conversation_id="conv-iso-legit-1",
        prompt="hello from a legitimate tenant A caller",
    )
    response = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.ai.response.generate",
            session_token=token_a,
            parameters={"request": request},
            context={"resource_tenant_id": _TENANT_A},
        )
    )
    assert response.text_content == "answer to: hello from a legitimate tenant A caller"
    assert provider.seen_requests[0].tenant_id == _TENANT_A

    turns = await ai_engine.memory_manager.get_turns(_TENANT_A, "conv-iso-legit-1")
    assert len(turns) == 1

    records = await ai_engine.query_decision_records(tenant_id=_TENANT_A)
    assert any(r["request_id"] == "req-iso-legit-1" for r in records)
