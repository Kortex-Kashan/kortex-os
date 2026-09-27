"""Browser Completion Program (B6): the AI <-> Browser bridge, end to end
through the REAL Kernel, `CapabilityDispatcher`, `SecurityEngine`
(authentication + RBAC/ABAC), Browser engine and AI engine.

The only test doubles are the LLM provider and the desktop itself: this
test plays the desktop's role by calling the same two capabilities the Rust
bridge calls (`kortex.ai.agent.browser_execution.claim`,
`kortex.browser.report_execution`) through real dispatch, and verifies the
claimed Grant exactly the way the desktop does (signature + parameter hash).
The desktop-side execution itself is covered by `browser_bridge.rs`'s own
tests and by the live Windows preflight.

Provisioning note (a real, disclosed production gap this test makes
explicit, not a test convenience): the AI system principal can only reach
`kortex.browser.*` if an operator grants its role the `browser:*`
permissions AND a clearance of at least CONFIDENTIAL (every Browser
capability's classification). `kernel_bootstrap._build_ai_system_identity`
provisions INTERNAL clearance and no `browser:*` permissions, so in a
default install ABAC/RBAC deny every Browser call the AI makes.
"""

from __future__ import annotations

import asyncio
import datetime
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.kernel_bootstrap import register_browser_ai_tools
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.agent import AgentStatus
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.engine import AIOrchestrationEngine
from kortex.engines.ai.exceptions import AgentNotFoundError, AgentStateConflictError
from kortex.engines.ai.identity import AI_SYSTEM_PRINCIPAL_ID, AI_SYSTEM_ROLE, AISystemIdentity
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.browser.audit import BROWSER_EXECUTION_REPORTED, BROWSER_GRANT_MINTED
from kortex.engines.browser.engine import BrowserCapabilityEngine
from kortex.engines.browser.grant import canonicalize_and_hash, verify_grant
from kortex.engines.browser.models import BrowserCapabilityExecutionGrant
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, PrincipalType, RolePermissionRecord
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_MASTER_KEY = b"\x71" * 32
_SIGNING_KEY = b"\x72" * 32
_TENANT_A = "tenant-bridge-a"
_TENANT_B = "tenant-bridge-b"
_OWNER = "bridge-owner"
_BYSTANDER = "bridge-bystander"
_OTHER_TENANT_USER = "bridge-other-tenant"
_USER_ROLE = "BRIDGE_USER_ROLE"
_PASSWORD = "bridge-test-password"
_PAGE_TEXT = "Hello from the real page"
_TARGET = {"browser_profile_id": "profile-1", "surface_id": "surface-1", "navigation_generation": 4}


class _ScriptedModel(BaseAIProvider):
    """Turn 1 proposes one Browser tool call; turn 2 answers with whether
    the page text actually reached its context."""

    def __init__(self) -> None:
        self.next_call: dict[str, Any] = {}
        self.prompts: list[str] = []
        self._metadata = AIProviderMetadata(
            provider_id="bridge-test-provider",
            display_name="Bridge Test Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=["test-model"],
            credential_requirement="none",
        )

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        self.prompts.append(request.prompt)
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        if len(self.prompts) == 1:
            return LLMResponse(
                request_id=request.request_id, text_content="acting", tool_calls=[self.next_call], token_usage=usage
            )
        seen = _PAGE_TEXT in request.prompt
        return LLMResponse(
            request_id=request.request_id,
            text_content="SAW-PAGE" if seen else "NO-PAGE",
            tool_calls=[],
            token_usage=usage,
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True


@dataclass
class _Env:
    kernel: Kernel
    security: SecurityEngine
    browser: BrowserCapabilityEngine
    ai: AIOrchestrationEngine
    model: _ScriptedModel
    pending_events: list[dict[str, Any]]


def _operator_provisioned_ai_identity(security: SecurityEngine) -> AISystemIdentity:
    credentials: dict[str, str] = {}

    async def _provision(tenant_id: str) -> None:
        credentials.setdefault(tenant_id, secrets.token_urlsafe(24))
        await security.authentication_manager.provision_principal(
            tenant_id=tenant_id,
            principal_id=AI_SYSTEM_PRINCIPAL_ID,
            principal_type=PrincipalType.AGENT,
            credential=credentials[tenant_id],
            roles=[AI_SYSTEM_ROLE],
            attributes={"clearance_level": "CONFIDENTIAL"},
        )

    async def _authenticate(tenant_id: str) -> Any:
        principal = await security.authenticate(
            {
                "principal_type": PrincipalType.AGENT.value,
                "tenant_id": tenant_id,
                "principal_id": AI_SYSTEM_PRINCIPAL_ID,
                "credential": credentials[tenant_id],
            }
        )
        return await security.authentication_manager.issue_token(principal)

    return AISystemIdentity(provisioner=_provision, authenticator=_authenticate)


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[_Env]:
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{(tmp_path / 'bridge.db').as_posix()}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    storage = StorageEngine(base_directory=str(tmp_path / "storage"))
    security = SecurityEngine(master_key=_MASTER_KEY, signing_private_key=_SIGNING_KEY)
    browser = BrowserCapabilityEngine(grant_ttl_seconds=20)
    kernel.register_engine(storage)
    kernel.register_engine(security)
    kernel.register_engine(browser)

    model = _ScriptedModel()
    ai = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="production", enable_cloud_models=False)
    ).create_ai_engine(
        kernel_bridge=KernelBridgeAdapter(kernel),
        data_store=RelationalDataStore(db_manager),
        custom_providers=[model],
        registered_engines=list(kernel.get_all_engines().keys()),
        ai_identity=_operator_provisioned_ai_identity(security),
    )
    kernel.register_engine(ai)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    register_browser_ai_tools(kernel, ai.tool_registry)

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        for permission in ("browser:read", "browser:click", "browser:type", "browser:navigate"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=AI_SYSTEM_ROLE, permission=permission))
        for permission in ("ai:orchestrate", "ai:read"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_USER_ROLE, permission=permission))
        for tenant_id, principal_id in ((_TENANT_A, _OWNER), (_TENANT_A, _BYSTANDER), (_TENANT_B, _OTHER_TENANT_USER)):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    principal_type="USER",
                    enabled=True,
                    credential_hash=hasher.hash(_PASSWORD),
                    roles=[_USER_ROLE],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )

    await storage.data.execute_in_transaction(_seed)

    pending_events: list[dict[str, Any]] = []

    async def _capture(event: Any) -> None:
        pending_events.append(dict(event.payload))

    kernel.subscribe_event("browser.grant.pending", _capture, subscriber_name="test-desktop")

    try:
        yield _Env(kernel, security, browser, ai, model, pending_events)
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _token(env: _Env, tenant_id: str, principal_id: str) -> Any:
    principal = await env.security.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": _PASSWORD}
    )
    return await env.security.authentication_manager.issue_token(principal)


async def _invoke(env: _Env, capability: str, token: Any, tenant_id: str = _TENANT_A, **parameters: Any) -> Any:
    return await env.kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability,
            session_token=token,
            parameters=parameters,
            context={"resource_tenant_id": tenant_id},
        )
    )


async def _orchestrate(env: _Env, token: Any, task_id: str) -> Any:
    task = {
        "task_id": task_id,
        "tenant_id": _TENANT_A,
        "user_id": _OWNER,
        "conversation_id": f"conv-{task_id}",
        "goal": "Summarize the page",
    }
    return await _invoke(env, "kortex.ai.agent.orchestrate", token, task=task)


async def _wait_for_status(env: _Env, task_id: str, status: AgentStatus, timeout: float = 10.0) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        record = await env.ai.agent_orchestrator.get_task(task_id, _TENANT_A)
        if record is not None and record.status == status:
            return record
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"task {task_id} never reached {status}; last={getattr(record, 'status', None)}")
        await asyncio.sleep(0.05)


def _desktop_verifies(env: _Env, claimed: dict[str, Any]) -> BrowserCapabilityExecutionGrant:
    """What `browser_grant.rs` does before executing: signature/expiry, and
    the parameter hash recomputed from the target (taken from the Grant
    itself) plus the claimed execution parameters."""
    grant = BrowserCapabilityExecutionGrant.model_validate(claimed["grant"])
    key = bytes.fromhex(env.browser.__dict__["_signing_public_key"].hex())
    assert verify_grant(grant, crypto_provider=LocalCrypto(), verification_public_key=key).is_valid
    fields = {k: v for k, v in claimed["execution_parameters"].items() if k != "capability"}
    target = {
        "browser_profile_id": grant.browser_profile_id,
        "surface_id": grant.surface_id,
        "navigation_generation": grant.navigation_generation,
    }
    assert (
        canonicalize_and_hash(grant.capability_name, {"target": target, **fields})
        == grant.canonicalized_parameters_hash
    )
    return grant


async def test_ai_read_reaches_the_desktop_and_the_ai_receives_the_real_page_text(env: _Env) -> None:
    env.model.next_call = {"name": "kortex_browser_read", "call_id": "call-read", "arguments": {"target": _TARGET}}
    owner = await _token(env, _TENANT_A, _OWNER)

    paused = await _orchestrate(env, owner, "task-read")
    assert paused.status == AgentStatus.PAUSED_FOR_BROWSER_EXECUTION

    assert len(env.pending_events) == 1
    notice = env.pending_events[0]
    assert notice["tenant_id"] == _TENANT_A and notice["audience_principal_id"] == _OWNER
    assert set(notice) == {
        "tenant_id",
        "audience_principal_id",
        "task_id",
        "grant_id",
        "capability_name",
        "browser_profile_id",
        "surface_id",
        "grant_expires_at",
    }, "the notification carries identifiers only"

    claimed = await _invoke(
        env, "kortex.ai.agent.browser_execution.claim", owner, task_id="task-read", grant_id=notice["grant_id"]
    )
    grant = _desktop_verifies(env, claimed)
    assert grant.tenant_id == _TENANT_A and grant.capability_name == "kortex.browser.read"

    accepted = await _invoke(
        env,
        "kortex.browser.report_execution",
        owner,
        task_id="task-read",
        grant_id=notice["grant_id"],
        execution_outcome={
            "result": {
                "status": "read",
                "capability_name": "kortex.browser.read",
                "text": _PAGE_TEXT,
                "truncated": False,
            }
        },
    )
    assert accepted == {"accepted": True}

    record = await _wait_for_status(env, "task-read", AgentStatus.COMPLETED)
    assert record.steps[0].tool_results[0].output["page_text"] == _PAGE_TEXT
    assert record.steps[-1].response_text == "SAW-PAGE", "the model's next step saw the real page text"
    assert record.browser_execution is None

    minted = await env.security.audit_manager.get_audit_entries(_TENANT_A, action=BROWSER_GRANT_MINTED)
    reported = await env.security.audit_manager.get_audit_entries(_TENANT_A, action=BROWSER_EXECUTION_REPORTED)
    assert len(minted) == 1 and len(reported) == 1
    assert reported[0].actor_id == _OWNER and reported[0].actor_type == "HUMAN"
    assert reported[0].context["grant_id"] == notice["grant_id"]
    assert reported[0].context["status"] == "read"
    everything_audited = str(await env.security.audit_manager.get_audit_entries(_TENANT_A, limit=1000))
    assert _PAGE_TEXT not in everything_audited, "page content must never be written to the audit log"


async def test_a_mutation_mints_nothing_until_approved_then_executes_through_the_bridge(env: _Env) -> None:
    env.model.next_call = {
        "name": "kortex_browser_click",
        "call_id": "call-click",
        "arguments": {"target": _TARGET, "selector": {"role": "button", "accessible_name": "Buy"}},
    }
    owner = await _token(env, _TENANT_A, _OWNER)

    paused = await _orchestrate(env, owner, "task-click")
    assert paused.status == AgentStatus.PAUSED_FOR_APPROVAL
    assert env.pending_events == []
    assert await env.security.audit_manager.get_audit_entries(_TENANT_A, action=BROWSER_GRANT_MINTED) == []

    resumed = await _invoke(
        env,
        "kortex.ai.agent.resume",
        owner,
        task={
            "task_id": "task-click",
            "tenant_id": _TENANT_A,
            "user_id": _OWNER,
            "conversation_id": "conv-task-click",
            "goal": "Summarize the page",
        },
        resume_token=paused.resume_token.model_dump(),
        approved_tool_calls=[c.model_dump() for c in paused.pending_tool_calls],
    )
    assert resumed.status == AgentStatus.PAUSED_FOR_BROWSER_EXECUTION
    notice = env.pending_events[0]
    claimed = await _invoke(
        env, "kortex.ai.agent.browser_execution.claim", owner, task_id="task-click", grant_id=notice["grant_id"]
    )
    assert claimed["execution_parameters"] == {
        "capability": "kortex.browser.click",
        "selector": {"role": "button", "accessible_name": "Buy", "node_ref": None},
    }
    _desktop_verifies(env, claimed)
    await _invoke(
        env,
        "kortex.browser.report_execution",
        owner,
        task_id="task-click",
        grant_id=notice["grant_id"],
        execution_outcome={"result": {"status": "click", "capability_name": "kortex.browser.click"}},
    )
    record = await _wait_for_status(env, "task-click", AgentStatus.COMPLETED)
    click_result = next(r for s in record.steps for r in s.tool_results if r.call_id == "call-click")
    assert click_result.output["outcome"] == "CLICKED"


async def test_nobody_but_the_task_owner_can_claim(env: _Env) -> None:
    env.model.next_call = {"name": "kortex_browser_read", "call_id": "c", "arguments": {"target": _TARGET}}
    owner = await _token(env, _TENANT_A, _OWNER)
    await _orchestrate(env, owner, "task-claim")
    grant_id = env.pending_events[0]["grant_id"]

    bystander = await _token(env, _TENANT_A, _BYSTANDER)
    with pytest.raises(AgentNotFoundError):
        await _invoke(
            env, "kortex.ai.agent.browser_execution.claim", bystander, task_id="task-claim", grant_id=grant_id
        )

    other = await _token(env, _TENANT_B, _OTHER_TENANT_USER)
    with pytest.raises(AgentNotFoundError):
        await _invoke(
            env,
            "kortex.ai.agent.browser_execution.claim",
            other,
            tenant_id=_TENANT_B,
            task_id="task-claim",
            grant_id=grant_id,
        )

    claimed = await _invoke(
        env, "kortex.ai.agent.browser_execution.claim", owner, task_id="task-claim", grant_id=grant_id
    )
    assert claimed["grant"]["grant_id"] == grant_id
    with pytest.raises(AgentStateConflictError):
        await _invoke(env, "kortex.ai.agent.browser_execution.claim", owner, task_id="task-claim", grant_id=grant_id)


async def test_forged_reports_never_resume_the_task(env: _Env) -> None:
    env.model.next_call = {"name": "kortex_browser_read", "call_id": "c", "arguments": {"target": _TARGET}}
    owner = await _token(env, _TENANT_A, _OWNER)
    await _orchestrate(env, owner, "task-forge")
    grant_id = env.pending_events[0]["grant_id"]
    await _invoke(env, "kortex.ai.agent.browser_execution.claim", owner, task_id="task-forge", grant_id=grant_id)
    forged_outcome = {
        "result": {"status": "read", "capability_name": "kortex.browser.read", "text": "FORGED", "truncated": False}
    }

    # A same-tenant user who is not the claimer reports: accepted on the
    # wire (the response never reveals correlation), refused by the owner.
    bystander = await _token(env, _TENANT_A, _BYSTANDER)
    assert await _invoke(
        env,
        "kortex.browser.report_execution",
        bystander,
        task_id="task-forge",
        grant_id=grant_id,
        execution_outcome=forged_outcome,
    ) == {"accepted": True}

    # An in-process publisher that is not the Browser engine.
    await env.kernel.publish_event(
        "browser.execution.reported",
        {
            "tenant_id": _TENANT_A,
            "reporter_principal_id": _OWNER,
            "task_id": "task-forge",
            "grant_id": grant_id,
            "execution_outcome": forged_outcome,
        },
        sender="not-the-browser-engine",
    )
    await asyncio.sleep(0.2)

    record = await env.ai.agent_orchestrator.get_task("task-forge", _TENANT_A)
    assert record is not None and record.status == AgentStatus.PAUSED_FOR_BROWSER_EXECUTION
    assert record.browser_execution is not None and record.browser_execution.reported_result is None


async def test_malformed_report_is_refused_by_the_report_capability(env: _Env) -> None:
    env.model.next_call = {"name": "kortex_browser_read", "call_id": "c", "arguments": {"target": _TARGET}}
    owner = await _token(env, _TENANT_A, _OWNER)
    await _orchestrate(env, owner, "task-malformed")
    grant_id = env.pending_events[0]["grant_id"]
    await _invoke(env, "kortex.ai.agent.browser_execution.claim", owner, task_id="task-malformed", grant_id=grant_id)

    for outcome in ({}, {"result": {"no_status": 1}}, {"result": {"status": "read"}, "error": {"kind": "x"}}):
        with pytest.raises(ValidationError):
            await _invoke(
                env,
                "kortex.browser.report_execution",
                owner,
                task_id="task-malformed",
                grant_id=grant_id,
                execution_outcome=outcome,
            )
    oversized = {
        "result": {
            "status": "read",
            "capability_name": "kortex.browser.read",
            "text": "x" * 300_000,
            "truncated": False,
        }
    }
    with pytest.raises(ValidationError):
        await _invoke(
            env,
            "kortex.browser.report_execution",
            owner,
            task_id="task-malformed",
            grant_id=grant_id,
            execution_outcome=oversized,
        )
    record = await env.ai.agent_orchestrator.get_task("task-malformed", _TENANT_A)
    assert record is not None and record.status == AgentStatus.PAUSED_FOR_BROWSER_EXECUTION


async def test_the_sweep_expires_an_unclaimed_execution_as_not_executed(env: _Env) -> None:
    env.model.next_call = {"name": "kortex_browser_read", "call_id": "c", "arguments": {"target": _TARGET}}
    owner = await _token(env, _TENANT_A, _OWNER)
    await _orchestrate(env, owner, "task-expire")

    await env.ai.sweep_browser_executions(now=datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5))

    record = await env.ai.agent_orchestrator.get_task("task-expire", _TENANT_A)
    assert record is not None and record.status == AgentStatus.CANCELLED
    assert record.steps[-1].tool_results[0].output["classification"] == "NOT_EXECUTED"


async def test_default_ai_clearance_cannot_reach_browser_capabilities(env: _Env) -> None:
    """Evidence for the disclosed provisioning gap: with the INTERNAL
    clearance `kernel_bootstrap` provisions by default, ABAC denies the AI's
    Browser call (CONFIDENTIAL) — fail-closed, no Grant, no pause."""
    ai_token = await env.ai.agent_orchestrator._tool_invoker._execution_port._ai_identity.get_session_token(_TENANT_A)  # type: ignore[attr-defined]

    async def _downgrade(session: AsyncSession) -> None:
        from sqlalchemy import update

        await session.execute(
            update(PrincipalRecord)
            .where(PrincipalRecord.principal_id == AI_SYSTEM_PRINCIPAL_ID, PrincipalRecord.tenant_id == _TENANT_A)
            .values(attributes={"clearance_level": "INTERNAL"})
        )

    await env.kernel.get_engine("storage").data.execute_in_transaction(_downgrade)
    from kortex.engines.security.exceptions import AuthorizationDeniedError

    with pytest.raises(AuthorizationDeniedError, match=r"(?i)clearance"):
        await _invoke(env, "kortex.browser.read", ai_token, target=_TARGET)


async def test_bridge_infrastructure_capabilities_are_never_ai_tools(env: _Env) -> None:
    names = {tool.canonical_capability for tool in env.ai.tool_registry.list_tools()}
    assert "kortex.browser.read" in names
    assert "kortex.browser.report_execution" not in names
    assert "kortex.ai.agent.browser_execution.claim" not in names
    assert "kortex.browser.grant_verification_key" not in names
