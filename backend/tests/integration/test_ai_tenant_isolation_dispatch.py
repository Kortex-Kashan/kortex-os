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
from kortex.engines.ai.agent import AgentStatus, AgentTask, PersistedAgentTaskRecord
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
        for _permission in (
            "ai:generate",
            "ai:read",
            "ai:manage",
            "ai:governance",
            "ai:orchestrate",
            "audit:read",
        ):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission=_permission))
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


# ---------------------------------------------------------------------------
# Phase B / B1b -- tenant isolation on the governance and agent-task surface.
#
# The M6.1-1/M6.2-2 fixes above covered the five handlers that received an
# identity (generate_response, orchestrate_agent, resume_agent, invoke_tool,
# get_conversation_history). The eleven governance and agent-task handlers
# received none at all: their `tenant_id` argument came straight from
# `request.parameters` and was fully authoritative, so an authenticated
# caller in tenant B reached tenant A's policy, quota, decision records and
# agent tasks just by naming tenant A. RBAC did not help -- the attacker
# holds these permissions legitimately for their OWN tenant; the missing
# control was tenant binding, not permission.
#
# Every test below drives the real dispatch boundary and states the caller's
# REAL tenant in `context` (so ABAC passes exactly as it would for a
# legitimate request) while spoofing the tenant inside `parameters`. That is
# the actual attack shape, and nothing upstream of the handler rejects it.
# ---------------------------------------------------------------------------


async def _invoke(kernel: Kernel, capability: str, token: Any, real_tenant: str, **parameters: Any) -> Any:
    return await kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability,
            session_token=token,
            parameters=parameters,
            context={"resource_tenant_id": real_tenant},
        )
    )


@pytest.mark.asyncio
async def test_governance_policy_get_cannot_read_another_tenants_policy(kernel_env) -> None:
    """Tenant B asking for tenant A's policy is answered with tenant B's own."""
    kernel, _ai_engine, _provider = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    policy = await _invoke(kernel, "kortex.ai.governance.policy.get", token_b, _TENANT_B, tenant_id=_TENANT_A)

    assert policy["tenant_id"] == _TENANT_B
    assert policy["tenant_id"] != _TENANT_A


@pytest.mark.asyncio
async def test_governance_policy_upsert_cannot_rewrite_another_tenants_policy(kernel_env) -> None:
    """The highest-severity case in this group: without the fix, tenant B could
    disable tenant A's guardrails outright -- switching off PII redaction and
    mutation approval for a tenant it has no relationship with."""
    kernel, ai_engine, _provider = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    baseline_a = await ai_engine.governance_manager.get_policy(_TENANT_A)
    assert baseline_a.pii_redaction_enabled is True

    saved = await _invoke(
        kernel,
        "kortex.ai.governance.policy.upsert",
        token_b,
        _TENANT_B,
        policy={
            "id": str(uuid4()),
            "tenant_id": _TENANT_A,  # spoofed
            "pii_redaction_enabled": False,
            "require_human_approval_for_mutations": False,
            "banned_prompt_patterns": [],
            "blocked_tools": [],
        },
    )

    # The write landed on the caller's own tenant...
    assert saved["tenant_id"] == _TENANT_B

    # ...and tenant A's guardrails are untouched.
    after_a = await ai_engine.governance_manager.get_policy(_TENANT_A)
    assert after_a.pii_redaction_enabled is True
    assert after_a.require_human_approval_for_mutations is True


@pytest.mark.asyncio
async def test_quota_update_cannot_raise_another_tenants_budget(kernel_env) -> None:
    """Rewriting another tenant's quota is a cost attack against that tenant."""
    kernel, ai_engine, _provider = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    quota_manager = ai_engine.governance_manager.quota_manager
    before_a = await quota_manager.get_or_create_quota(_TENANT_A)

    saved = await _invoke(
        kernel,
        "kortex.ai.governance.quota.update",
        token_b,
        _TENANT_B,
        quota={
            "tenant_id": _TENANT_A,  # spoofed
            "daily_token_limit": 999_999_999,
            "last_reset_date": "2026-01-01",
        },
    )

    assert saved["tenant_id"] == _TENANT_B

    after_a = await quota_manager.get_or_create_quota(_TENANT_A)
    assert after_a.daily_token_limit == before_a.daily_token_limit
    assert after_a.daily_token_limit != 999_999_999


@pytest.mark.asyncio
async def test_quota_get_cannot_read_another_tenants_usage(kernel_env) -> None:
    """A quota reading discloses another tenant's AI usage volume."""
    kernel, _ai_engine, _provider = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    quota = await _invoke(kernel, "kortex.ai.governance.quota.get", token_b, _TENANT_B, tenant_id=_TENANT_A)

    assert quota["tenant_id"] == _TENANT_B


@pytest.mark.asyncio
async def test_decision_record_query_cannot_read_another_tenants_records(kernel_env) -> None:
    """Decision records carry prompts and reasoning -- the highest-value read here.

    Tenant A really generates first, so there is genuine data to steal; the
    assertion is that tenant B's query naming tenant A returns none of it.
    """
    kernel, _ai_engine, _provider = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_ai_iso_a", "pass-a")
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    await _invoke(
        kernel,
        "kortex.ai.response.generate",
        token_a,
        _TENANT_A,
        request=LLMRequest(
            request_id="req-iso-audit-secret",
            tenant_id=_TENANT_A,
            user_id="user_ai_iso_a",
            conversation_id="conv-iso-audit-secret",
            prompt="tenant A's confidential prompt",
        ),
    )

    # Tenant A sees its own record -- proves the record exists to be stolen.
    own = await _invoke(kernel, "kortex.ai.governance.audit.query", token_a, _TENANT_A, tenant_id=_TENANT_A)
    assert any(r["request_id"] == "req-iso-audit-secret" for r in own)

    stolen = await _invoke(kernel, "kortex.ai.governance.audit.query", token_b, _TENANT_B, tenant_id=_TENANT_A)
    assert not any(r["request_id"] == "req-iso-audit-secret" for r in stolen)


@pytest.mark.asyncio
async def test_agent_task_status_and_list_cannot_reach_another_tenants_task(kernel_env) -> None:
    """Agent task records carry the goal and every reasoning step."""
    kernel, _ai_engine, _provider = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_ai_iso_a", "pass-a")
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    await _invoke(
        kernel,
        "kortex.ai.agent.orchestrate",
        token_a,
        _TENANT_A,
        task=AgentTask(
            task_id="task-iso-secret",
            tenant_id=_TENANT_A,
            user_id="user_ai_iso_a",
            conversation_id="conv-iso-agent",
            goal="tenant A's confidential goal",
        ),
    )

    own = await _invoke(
        kernel, "kortex.ai.agent.status", token_a, _TENANT_A, task_id="task-iso-secret", tenant_id=_TENANT_A
    )
    assert own is not None

    stolen = await _invoke(
        kernel, "kortex.ai.agent.status", token_b, _TENANT_B, task_id="task-iso-secret", tenant_id=_TENANT_A
    )
    assert stolen is None

    listed = await _invoke(kernel, "kortex.ai.agent.list", token_b, _TENANT_B, tenant_id=_TENANT_A)
    assert all(record.task_id != "task-iso-secret" for record in listed)


@pytest.mark.asyncio
async def test_agent_cancel_cannot_cancel_another_tenants_task(kernel_env) -> None:
    """Cancelling another tenant's agent task is a denial of service, not a read.

    The task is seeded directly in RUNNING state rather than produced by
    `orchestrate_agent`: a task that ran to completion is already
    un-cancellable for *every* caller (`InMemoryAgentTaskStore.cancel_task`
    returns False on any terminal status), which would make this test pass
    whether or not the tenant binding works. A genuinely cancellable task is
    what makes the assertion mean something -- and the final check proves
    tenant A can still cancel its own.
    """
    kernel, ai_engine, _provider = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_ai_iso_a", "pass-a")
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    await ai_engine.agent_orchestrator.task_store.save_task(
        PersistedAgentTaskRecord(
            task=AgentTask(
                task_id="task-iso-cancel",
                tenant_id=_TENANT_A,
                user_id="user_ai_iso_a",
                conversation_id="conv-iso-cancel",
                goal="tenant A's long-running task",
            ),
            status=AgentStatus.RUNNING,
        )
    )

    cancelled = await _invoke(
        kernel, "kortex.ai.agent.cancel", token_b, _TENANT_B, task_id="task-iso-cancel", tenant_id=_TENANT_A
    )
    assert cancelled is False

    still_running = await ai_engine.agent_orchestrator.get_task("task-iso-cancel", _TENANT_A)
    assert still_running is not None
    assert still_running.status == AgentStatus.RUNNING

    # The owning tenant can still cancel it -- the control is tenant binding,
    # not a blanket refusal.
    own_cancel = await _invoke(
        kernel, "kortex.ai.agent.cancel", token_a, _TENANT_A, task_id="task-iso-cancel", tenant_id=_TENANT_A
    )
    assert own_cancel is True


@pytest.mark.asyncio
async def test_guardrail_check_cannot_borrow_another_tenants_permissive_policy(kernel_env) -> None:
    """A caller must not escape its own guardrails by naming a tenant whose
    policy is more permissive."""
    kernel, ai_engine, _provider = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    own_policy = await ai_engine.governance_manager.get_policy(_TENANT_B)
    await ai_engine.governance_manager.set_policy(
        own_policy.model_copy(update={"banned_prompt_patterns": ["forbidden-phrase"]})
    )

    result = await _invoke(
        kernel,
        "kortex.ai.governance.guardrail.check",
        token_b,
        _TENANT_B,
        text="this contains a forbidden-phrase",
        tenant_id=_TENANT_A,  # spoofed: tenant A bans nothing
    )

    # Tenant B's own ban applied despite the request naming tenant A.
    assert result["passed"] is False


@pytest.mark.asyncio
async def test_governance_approval_cannot_be_injected_into_another_tenants_queue(kernel_env) -> None:
    kernel, _ai_engine, _provider = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_ai_iso_b", "pass-b")

    created = await _invoke(
        kernel,
        "kortex.ai.governance.approval.create",
        token_b,
        _TENANT_B,
        tenant_id=_TENANT_A,  # spoofed
        task_id="task-iso-approval",
        goal="approve my action",
        proposed_calls=[],
    )

    assert created["tenant_id"] == _TENANT_B


@pytest.mark.asyncio
async def test_legitimate_same_tenant_governance_access_still_works(kernel_env) -> None:
    """Regression guard: deriving the tenant from the verified identity must be
    invisible to a caller that correctly states its own tenant."""
    kernel, _ai_engine, _provider = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_ai_iso_a", "pass-a")

    policy = await _invoke(kernel, "kortex.ai.governance.policy.get", token_a, _TENANT_A, tenant_id=_TENANT_A)
    assert policy["tenant_id"] == _TENANT_A

    quota = await _invoke(kernel, "kortex.ai.governance.quota.get", token_a, _TENANT_A, tenant_id=_TENANT_A)
    assert quota["tenant_id"] == _TENANT_A

    saved = await _invoke(
        kernel,
        "kortex.ai.governance.quota.update",
        token_a,
        _TENANT_A,
        quota={"tenant_id": _TENANT_A, "daily_token_limit": 555_000, "last_reset_date": "2026-01-01"},
    )
    assert saved["tenant_id"] == _TENANT_A
    assert saved["daily_token_limit"] == 555_000
