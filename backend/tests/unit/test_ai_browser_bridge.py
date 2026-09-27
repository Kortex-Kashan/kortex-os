"""Browser Completion Program (B6): the AI <-> Browser execution bridge.

Exercises the PAUSED_FOR_BROWSER_EXECUTION workflow in `AgentOrchestrator`
with the REAL production `BrowserExecutionBridgePort` — only the LLM, the
capability execution port and the event publisher are test doubles. The
end-to-end slice through the real Kernel/dispatcher/security stack lives in
`tests/integration/test_browser_ai_bridge.py`.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.engines.ai.agent import (
    AgentOrchestrator,
    AgentStatus,
    AgentStep,
    AgentTask,
    AlwaysApprovePolicy,
    AlwaysDenyPolicy,
    InMemoryAgentTaskStore,
    InMemoryLLMExecutionPort,
    PersistedAgentTaskRecord,
)
from kortex.engines.ai.browser_bridge import (
    BROWSER_GRANT_PENDING_TOPIC,
    BrowserExecutionBridgePort,
    BrowserOutcomeError,
)
from kortex.engines.ai.exceptions import AgentNotFoundError, AgentStateConflictError
from kortex.engines.ai.models import LLMRequest, LLMResponse
from kortex.engines.ai.persistence import StorageAgentTaskStore
from kortex.engines.ai.tools import (
    AIToolInvoker,
    InMemoryToolExecutionPort,
    ToolDefinition,
    ToolExecutionStatus,
    ToolRegistry,
    ToolResult,
)
from kortex.engines.storage.stores.data_store import RelationalDataStore

TENANT = "tenant-a"
OWNER = "user-owner"
READ_TOOL = "kortex_browser_read"
CLICK_TOOL = "kortex_browser_click"
TYPE_TOOL = "kortex_browser_type"
OTHER_TOOL = "other_tool"
TARGET = {"browser_profile_id": "profile-1", "surface_id": "surface-1", "navigation_generation": 3}


def _utc(offset_seconds: float = 0.0) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=offset_seconds)


class _FakeBrowserCapabilities:
    """Stands in for the Browser engine's mint-only handlers: returns the
    exact `{"grant", "execution_parameters"}` shape `engine.py` produces."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.grant_tenant = TENANT
        self.ttl_seconds = 20.0

    def handler(self, capability: str):  # type: ignore[no-untyped-def]
        def _mint(arguments: dict[str, object]) -> dict[str, object]:
            self.calls.append((capability, arguments))
            now = _utc()
            fields = {k: v for k, v in arguments.items() if k != "target"}
            return {
                "grant": {
                    "grant_id": f"grant-{uuid4().hex[:8]}",
                    "tenant_id": self.grant_tenant,
                    "principal_id": "kortex-ai-system",
                    "capability_name": capability,
                    "browser_profile_id": "profile-1",
                    "surface_id": "surface-1",
                    "navigation_generation": 3,
                    "canonicalized_parameters_hash": "ab" * 32,
                    "issued_at": now.isoformat(),
                    "expires_at": (now + datetime.timedelta(seconds=self.ttl_seconds)).isoformat(),
                    "signature": "cd" * 64,
                },
                "execution_parameters": {"capability": capability, **fields},
            }

        return _mint


class _CapturingPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, *, topic: str, payload: dict[str, Any], sender: str) -> None:
        self.events.append({"topic": topic, "payload": payload, "sender": sender})


class _CapturingContextPort:
    """Records the step trace the model is given for each reasoning step."""

    def __init__(self) -> None:
        self.seen_steps: list[list[AgentStep]] = []

    async def build_step_context(self, task: AgentTask, steps: list[AgentStep]) -> LLMRequest:
        self.seen_steps.append(list(steps))
        return LLMRequest(
            request_id=f"req-{uuid4().hex}",
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            conversation_id=task.conversation_id,
            prompt=task.goal,
        )


def _llm(*batches: list[dict[str, Any]]) -> InMemoryLLMExecutionPort:
    responses = [
        LLMResponse(request_id=f"r{i}", text_content="thinking", tool_calls=calls) for i, calls in enumerate(batches)
    ]
    return InMemoryLLMExecutionPort(responses=responses)


class _Env:
    def __init__(self, *batches: list[dict[str, Any]], approval: Any = None, store: Any = None) -> None:
        self.capabilities = _FakeBrowserCapabilities()
        self.other_calls: list[dict[str, object]] = []
        registry = ToolRegistry(
            [
                ToolDefinition(name=READ_TOOL, description="read", canonical_capability="kortex.browser.read"),
                ToolDefinition(
                    name=CLICK_TOOL, description="click", canonical_capability="kortex.browser.click", is_mutation=True
                ),
                ToolDefinition(
                    name=TYPE_TOOL, description="type", canonical_capability="kortex.browser.type", is_mutation=True
                ),
                ToolDefinition(name=OTHER_TOOL, description="other", canonical_capability="test.other"),
            ]
        )
        execution_port = InMemoryToolExecutionPort()
        for capability in ("kortex.browser.read", "kortex.browser.click", "kortex.browser.type"):
            execution_port.register_handler(capability, self.capabilities.handler(capability))
        execution_port.register_handler("test.other", lambda args: self.other_calls.append(args) or {"ok": True})
        self.publisher = _CapturingPublisher()
        self.port = BrowserExecutionBridgePort(tool_registry=registry, publisher=self.publisher)
        self.context = _CapturingContextPort()
        self.store = store if store is not None else InMemoryAgentTaskStore()
        self.orchestrator = AgentOrchestrator(
            tool_invoker=AIToolInvoker(registry=registry, execution_port=execution_port),
            llm_port=_llm(*batches),
            context_port=self.context,
            approval_policy=approval if approval is not None else AlwaysApprovePolicy(),
            task_store=self.store,
            browser_execution_port=self.port,
        )

    def task(self, task_id: str = "task-1") -> AgentTask:
        return AgentTask(task_id=task_id, tenant_id=TENANT, user_id=OWNER, conversation_id="conv-1", goal="g")

    async def pending(self, task_id: str = "task-1") -> Any:
        record = await self.store.get_task(task_id, TENANT)
        assert record is not None
        return record.browser_execution


def _read_call() -> dict[str, Any]:
    return {"name": READ_TOOL, "call_id": "call-read", "arguments": {"target": TARGET}}


READ_OUTCOME = {
    "result": {"status": "read", "capability_name": "kortex.browser.read", "text": "Hello page", "truncated": False}
}


# -- Pause -----------------------------------------------------------------------


async def test_minted_grant_pauses_the_task_and_announces_identifiers_only() -> None:
    env = _Env([_read_call()])
    result = await env.orchestrator.run_task(env.task())

    assert result.status == AgentStatus.PAUSED_FOR_BROWSER_EXECUTION
    assert [c.tool_name for c in result.pending_tool_calls] == [READ_TOOL]
    record = await env.store.get_task("task-1", TENANT)
    assert record is not None and record.status == AgentStatus.PAUSED_FOR_BROWSER_EXECUTION
    pending = record.browser_execution
    assert pending is not None
    assert pending.tenant_id == TENANT and pending.tool_call_id == "call-read"
    assert pending.claimed_by is None and pending.reported_result is None
    assert record.steps == [], "the paused step is not a completed step yet"

    assert len(env.publisher.events) == 1
    event = env.publisher.events[0]
    assert event["topic"] == BROWSER_GRANT_PENDING_TOPIC
    assert event["payload"]["audience_principal_id"] == OWNER
    assert event["payload"]["grant_id"] == pending.grant_id
    assert "grant" not in event["payload"] and "execution_parameters" not in event["payload"]


async def test_default_serialization_never_exposes_execution_parameters() -> None:
    env = _Env(
        [
            {
                "name": TYPE_TOOL,
                "call_id": "c",
                "arguments": {"target": TARGET, "selector": {"role": "textbox"}, "ui_input_text": "hello there"},
            }
        ]
    )
    await env.orchestrator.run_task(env.task())
    record = await env.store.get_task("task-1", TENANT)
    assert record is not None and record.browser_execution is not None
    assert record.browser_execution.execution_parameters["ui_input_text"] == "hello there"
    dumped = record.model_dump(mode="json")
    assert "execution_parameters" not in dumped["browser_execution"]
    assert "grant" not in dumped["browser_execution"], "only the claim hands out the Grant"


async def test_calls_after_a_browser_call_in_the_same_batch_are_withheld() -> None:
    env = _Env([_read_call(), {"name": OTHER_TOOL, "call_id": "call-other", "arguments": {}}])
    await env.orchestrator.run_task(env.task())

    assert env.other_calls == [], "a call queued behind a pending Browser action must not run"
    pending = await env.pending()
    statuses = {r.call_id: r.status for r in pending.pending_step.tool_results}
    assert statuses["call-other"] == ToolExecutionStatus.NOT_EXECUTED


async def test_calls_before_a_browser_call_run_normally() -> None:
    env = _Env([{"name": OTHER_TOOL, "call_id": "call-other", "arguments": {"x": 1}}, _read_call()])
    await env.orchestrator.run_task(env.task())
    assert env.other_calls == [{"x": 1}]
    pending = await env.pending()
    assert pending.pending_step.tool_results[0].status == ToolExecutionStatus.SUCCESS


async def test_a_grant_for_another_tenant_is_refused_and_never_deferred() -> None:
    env = _Env([_read_call()])
    env.capabilities.grant_tenant = "tenant-other"
    result = await env.orchestrator.run_task(env.task())

    assert result.status == AgentStatus.COMPLETED
    assert env.publisher.events == []
    refused = result.steps[0].tool_results[0]
    assert refused.status == ToolExecutionStatus.EXECUTION_ERROR
    assert refused.output["error_code"] == "GRANT_INVALID"  # type: ignore[index]
    assert refused.output["executed"] == "no"  # type: ignore[index]


async def test_a_mutation_still_pauses_for_approval_before_any_grant_is_minted() -> None:
    env = _Env(
        [
            {
                "name": CLICK_TOOL,
                "call_id": "call-click",
                "arguments": {"target": TARGET, "selector": {"role": "button"}},
            }
        ],
        approval=AlwaysDenyPolicy(),
    )
    result = await env.orchestrator.run_task(env.task())

    assert result.status == AgentStatus.PAUSED_FOR_APPROVAL
    assert env.capabilities.calls == [], "no Grant may exist before approval"
    assert env.publisher.events == []

    # Approved: the preapproved call mints, and only now does the task
    # pause for desktop execution.
    assert result.resume_token is not None
    resumed = await env.orchestrator.resume_task(env.task(), result.resume_token, result.pending_tool_calls)
    assert resumed.status == AgentStatus.PAUSED_FOR_BROWSER_EXECUTION
    assert [c for c, _ in env.capabilities.calls] == ["kortex.browser.click"]


# -- Claim -----------------------------------------------------------------------


async def test_owner_claims_once_and_parameters_are_cleared_by_the_claim() -> None:
    env = _Env([_read_call()])
    await env.orchestrator.run_task(env.task())
    pending = await env.pending()

    claimed = await env.orchestrator.claim_browser_execution("task-1", TENANT, pending.grant_id, OWNER)
    assert claimed.grant["grant_id"] == pending.grant_id
    assert claimed.execution_parameters == {"capability": "kortex.browser.read"}

    after = await env.pending()
    assert after.claimed_by == OWNER and after.execution_parameters == {}
    with pytest.raises(AgentStateConflictError):
        await env.orchestrator.claim_browser_execution("task-1", TENANT, pending.grant_id, OWNER)


@pytest.mark.parametrize(
    "tenant,grant,principal",
    [
        ("tenant-other", None, OWNER),
        (TENANT, "wrong-grant", OWNER),
        (TENANT, None, "same-tenant-other-user"),
    ],
)
async def test_claim_is_refused_for_any_mismatched_binding(tenant: str, grant: str | None, principal: str) -> None:
    env = _Env([_read_call()])
    await env.orchestrator.run_task(env.task())
    pending = await env.pending()
    with pytest.raises(AgentNotFoundError):
        await env.orchestrator.claim_browser_execution("task-1", tenant, grant or pending.grant_id, principal)
    assert (await env.pending()).claimed_by is None


async def test_claim_after_grant_expiry_is_refused() -> None:
    env = _Env([_read_call()])
    await env.orchestrator.run_task(env.task())
    pending = await env.pending()
    with pytest.raises(AgentStateConflictError):
        await env.orchestrator.claim_browser_execution(
            "task-1", TENANT, pending.grant_id, OWNER, now=pending.grant_expires_at
        )


# -- Report + resume ----------------------------------------------------------------


async def _claimed(env: _Env) -> Any:
    await env.orchestrator.run_task(env.task())
    pending = await env.pending()
    await env.orchestrator.claim_browser_execution("task-1", TENANT, pending.grant_id, OWNER)
    return await env.pending()


async def test_reported_outcome_resumes_with_the_real_result_without_re_executing() -> None:
    env = _Env([_read_call()])
    pending = await _claimed(env)
    result = env.port.classify_outcome(pending, READ_OUTCOME)

    assert await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, result)
    final = await env.orchestrator.resume_with_browser_result("task-1", TENANT)

    assert final.status == AgentStatus.COMPLETED
    assert len(env.capabilities.calls) == 1, "resume must never re-invoke the Browser capability"
    step = final.steps[0]
    assert step.tool_results[0].output["page_text"] == "Hello page"  # type: ignore[index]
    assert step.tool_results[0].output["executed"] == "yes"  # type: ignore[index]
    # The model's next reasoning step saw the REAL outcome, not a Grant.
    last_context = env.context.seen_steps[-1]
    assert last_context[0].tool_results[0].output["page_text"] == "Hello page"  # type: ignore[index]


async def test_a_duplicate_report_is_a_no_op_and_a_second_resume_fails() -> None:
    env = _Env([_read_call()])
    pending = await _claimed(env)
    result = env.port.classify_outcome(pending, READ_OUTCOME)
    assert await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, result)
    assert not await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, result)

    await env.orchestrator.resume_with_browser_result("task-1", TENANT)
    with pytest.raises(AgentStateConflictError):
        await env.orchestrator.resume_with_browser_result("task-1", TENANT)


async def test_a_modified_second_report_cannot_replace_the_recorded_outcome() -> None:
    env = _Env([_read_call()])
    pending = await _claimed(env)
    first = env.port.classify_outcome(pending, READ_OUTCOME)
    denied = env.port.classify_outcome(pending, {"error": {"kind": "grantInvalid"}})
    assert await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, first)
    assert not await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, denied)
    stored = (await env.pending()).reported_result
    assert stored.output["page_text"] == "Hello page"  # type: ignore[union-attr,index]


async def test_a_report_after_the_task_resumed_is_refused_and_changes_nothing() -> None:
    env = _Env([_read_call()])
    pending = await _claimed(env)
    result = env.port.classify_outcome(pending, READ_OUTCOME)
    await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, result)
    final = await env.orchestrator.resume_with_browser_result("task-1", TENANT)
    assert final.status == AgentStatus.COMPLETED

    with pytest.raises(AgentNotFoundError):
        await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, result)
    record = await env.store.get_task("task-1", TENANT)
    assert record is not None and record.status == AgentStatus.COMPLETED
    assert len(env.capabilities.calls) == 1


async def test_a_report_naming_another_task_is_refused() -> None:
    env = _Env([_read_call()], [_read_call()])
    await env.orchestrator.run_task(env.task("task-1"))
    await env.orchestrator.run_task(env.task("task-2"))
    grant_1 = (await env.pending("task-1")).grant_id
    await env.orchestrator.claim_browser_execution("task-1", TENANT, grant_1, OWNER)
    result = env.port.classify_outcome(await env.pending("task-1"), READ_OUTCOME)
    with pytest.raises(AgentNotFoundError):
        await env.orchestrator.record_browser_execution_report("task-2", TENANT, grant_1, OWNER, result)
    assert (await env.pending("task-2")).reported_result is None


async def test_report_from_anyone_but_the_claimer_is_refused() -> None:
    env = _Env([_read_call()])
    pending = await _claimed(env)
    result = env.port.classify_outcome(pending, READ_OUTCOME)
    with pytest.raises(AgentNotFoundError):
        await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, "intruder", result)


async def test_report_for_an_unclaimed_execution_is_refused() -> None:
    env = _Env([_read_call()])
    await env.orchestrator.run_task(env.task())
    pending = await env.pending()
    result = env.port.classify_outcome(pending, READ_OUTCOME)
    with pytest.raises(AgentNotFoundError):
        await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, result)


async def test_late_report_is_refused() -> None:
    env = _Env([_read_call()])
    pending = await _claimed(env)
    result = env.port.classify_outcome(pending, READ_OUTCOME)
    with pytest.raises(AgentStateConflictError):
        await env.orchestrator.record_browser_execution_report(
            "task-1",
            TENANT,
            pending.grant_id,
            OWNER,
            result,
            now=pending.report_deadline + datetime.timedelta(seconds=1),
        )


async def test_a_report_cannot_retarget_another_call() -> None:
    env = _Env([_read_call()])
    pending = await _claimed(env)
    forged = ToolResult(
        call_id="someone-elses-call", tool_name="other_tool", status=ToolExecutionStatus.SUCCESS, output={}
    )
    await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, forged)
    stored = (await env.pending()).reported_result
    assert stored.call_id == "call-read" and stored.tool_name == READ_TOOL


async def test_report_after_cancellation_is_refused() -> None:
    env = _Env([_read_call()])
    pending = await _claimed(env)
    assert await env.orchestrator.cancel_task("task-1", TENANT)
    result = env.port.classify_outcome(pending, READ_OUTCOME)
    with pytest.raises(AgentNotFoundError):
        await env.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, result)


# -- Disjoint pause reasons ------------------------------------------------------------


async def test_an_approval_resume_can_never_resume_a_browser_pause() -> None:
    env = _Env([_read_call()])
    await env.orchestrator.run_task(env.task())
    record = await env.store.get_task("task-1", TENANT)
    from kortex.engines.ai.agent import _issue_resume_token

    token = _issue_resume_token("task-1", 0, [])
    with pytest.raises(AgentStateConflictError):
        await env.orchestrator.resume_task(env.task(), token, [])
    assert (await env.store.get_task("task-1", TENANT)).status == record.status  # type: ignore[union-attr]


async def test_a_browser_report_can_never_resume_an_approval_pause() -> None:
    env = _Env(
        [{"name": CLICK_TOOL, "call_id": "c", "arguments": {"target": TARGET, "selector": {"role": "button"}}}],
        approval=AlwaysDenyPolicy(),
    )
    await env.orchestrator.run_task(env.task())
    fake = ToolResult(call_id="c", tool_name=CLICK_TOOL, status=ToolExecutionStatus.SUCCESS, output={})
    with pytest.raises(AgentNotFoundError):
        await env.orchestrator.record_browser_execution_report("task-1", TENANT, "any-grant", OWNER, fake)
    with pytest.raises(AgentStateConflictError):
        await env.orchestrator.resume_with_browser_result("task-1", TENANT)


# -- Expiry ------------------------------------------------------------------------


async def test_unclaimed_expiry_cancels_the_task_as_certainly_not_executed() -> None:
    env = _Env([_read_call()])
    await env.orchestrator.run_task(env.task())
    pending = await env.pending()

    assert not await env.orchestrator.expire_browser_execution(
        "task-1", TENANT, now=pending.grant_expires_at - datetime.timedelta(seconds=1)
    )
    assert await env.orchestrator.expire_browser_execution("task-1", TENANT, now=pending.grant_expires_at)

    record = await env.store.get_task("task-1", TENANT)
    assert record is not None and record.status == AgentStatus.CANCELLED and record.browser_execution is None
    outcome = record.steps[-1].tool_results[0].output
    assert outcome["executed"] == "no" and outcome["classification"] == "NOT_EXECUTED"  # type: ignore[index]


async def test_claimed_but_unreported_mutation_expires_as_outcome_unknown() -> None:
    env = _Env([{"name": CLICK_TOOL, "call_id": "c", "arguments": {"target": TARGET, "selector": {"role": "button"}}}])
    await env.orchestrator.run_task(env.task())
    pending = await env.pending()
    await env.orchestrator.claim_browser_execution("task-1", TENANT, pending.grant_id, OWNER)

    # Past Grant expiry but within the report deadline: still waiting.
    assert not await env.orchestrator.expire_browser_execution("task-1", TENANT, now=pending.grant_expires_at)
    assert await env.orchestrator.expire_browser_execution("task-1", TENANT, now=pending.report_deadline)

    record = await env.store.get_task("task-1", TENANT)
    outcome = record.steps[-1].tool_results[0].output  # type: ignore[union-attr]
    assert outcome["executed"] == "unknown" and outcome["classification"] == "OUTCOME_UNKNOWN"  # type: ignore[index]
    assert "Do NOT repeat it" in outcome["guidance"]  # type: ignore[index]


async def test_a_reported_execution_is_never_expired() -> None:
    env = _Env([_read_call()])
    pending = await _claimed(env)
    await env.orchestrator.record_browser_execution_report(
        "task-1", TENANT, pending.grant_id, OWNER, env.port.classify_outcome(pending, READ_OUTCOME)
    )
    assert not await env.orchestrator.expire_browser_execution(
        "task-1", TENANT, now=pending.report_deadline + datetime.timedelta(hours=1)
    )


async def test_report_and_expiry_race_resolves_to_exactly_one_winner() -> None:
    env = _Env([_read_call()])
    pending = await _claimed(env)
    await env.orchestrator.expire_browser_execution("task-1", TENANT, now=pending.report_deadline)
    with pytest.raises(AgentNotFoundError):
        await env.orchestrator.record_browser_execution_report(
            "task-1", TENANT, pending.grant_id, OWNER, env.port.classify_outcome(pending, READ_OUTCOME)
        )


# -- Outcome classification ------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome,expected_status,executed,classification,code",
    [
        (
            {"error": {"kind": "policyDenied", "reason": "PrivateNetwork"}},
            "DENIED",
            "no",
            "SECURITY_FAILURE",
            "POLICY_DENIED",
        ),
        ({"error": {"kind": "grantInvalid"}}, "DENIED", "no", "SECURITY_FAILURE", "GRANT_INVALID"),
        ({"error": {"kind": "staleReference"}}, "EXECUTION_ERROR", "no", "REQUIRES_REOBSERVATION", "STALE_REFERENCE"),
        (
            {"error": {"kind": "uiaFailed", "detail": {"kind": "selectorNotFound"}}},
            "EXECUTION_ERROR",
            "no",
            "REQUIRES_REOBSERVATION",
            "TARGET_NOT_FOUND",
        ),
        (
            {"error": {"kind": "uiaFailed", "detail": {"kind": "selectorAmbiguous", "match_count": 2}}},
            "EXECUTION_ERROR",
            "no",
            "REQUIRES_REOBSERVATION",
            "TARGET_AMBIGUOUS",
        ),
        (
            {"error": {"kind": "uiaFailed", "detail": {"kind": "containmentFailed"}}},
            "DENIED",
            "no",
            "SECURITY_FAILURE",
            "FORBIDDEN",
        ),
        (
            {"error": {"kind": "uiaFailed", "detail": {"kind": "poolExhausted"}}},
            "EXECUTION_ERROR",
            "no",
            "RESOURCE_EXHAUSTED",
            "INTERNAL_ERROR",
        ),
        ({"error": {"kind": "timeout"}}, "TIMEOUT", "unknown", "OUTCOME_UNKNOWN", "TIMEOUT"),
        (
            {"error": {"kind": "uiaFailed", "detail": {"kind": "comFailure", "message": "x"}}},
            "EXECUTION_ERROR",
            "unknown",
            "OUTCOME_UNKNOWN",
            "INTERNAL_ERROR",
        ),
        ({"error": {"kind": "somethingNew"}}, "EXECUTION_ERROR", "unknown", "OUTCOME_UNKNOWN", "INTERNAL_ERROR"),
        ({"error": {"kind": "surfaceNotFound"}}, "EXECUTION_ERROR", "no", "TERMINAL", "SURFACE_NOT_FOUND"),
        ({"error": {"kind": "grantExpired"}}, "EXECUTION_ERROR", "no", "NOT_EXECUTED", "GRANT_EXPIRED"),
    ],
)
async def test_click_outcomes_are_classified_fail_closed(
    outcome: dict[str, Any], expected_status: str, executed: str, classification: str, code: str
) -> None:
    env = _Env([{"name": CLICK_TOOL, "call_id": "c", "arguments": {"target": TARGET, "selector": {"role": "button"}}}])
    await env.orchestrator.run_task(env.task())
    result = env.port.classify_outcome(await env.pending(), outcome)
    assert result.status.value == expected_status
    assert result.output["executed"] == executed  # type: ignore[index]
    assert result.output["classification"] == classification  # type: ignore[index]
    assert result.output["error_code"] == code  # type: ignore[index]


async def test_uncertain_read_failure_is_not_reported_as_an_unknown_side_effect() -> None:
    env = _Env([_read_call()])
    await env.orchestrator.run_task(env.task())
    result = env.port.classify_outcome(await env.pending(), {"error": {"kind": "timeout"}})
    assert result.output["executed"] == "no"  # type: ignore[index]


@pytest.mark.parametrize(
    "outcome",
    [
        {},
        {"result": {}, "error": {}},
        {"result": "x"},
        {"result": {"status": "click", "capability_name": "kortex.browser.read"}},
        {"result": {"status": "read", "capability_name": "kortex.browser.click"}},
        {"result": {"status": "read", "capability_name": "kortex.browser.read", "text": 5, "truncated": False}},
        {"error": {"no_kind": True}},
    ],
)
async def test_malformed_outcomes_are_rejected_never_guessed(outcome: dict[str, Any]) -> None:
    env = _Env([_read_call()])
    await env.orchestrator.run_task(env.task())
    with pytest.raises(BrowserOutcomeError):
        env.port.classify_outcome(await env.pending(), outcome)


async def test_oversized_read_text_is_bounded() -> None:
    env = _Env([_read_call()])
    await env.orchestrator.run_task(env.task())
    huge = {
        "result": {"status": "read", "capability_name": "kortex.browser.read", "text": "x" * 80_000, "truncated": False}
    }
    result = env.port.classify_outcome(await env.pending(), huge)
    assert len(result.output["page_text"]) == 50_000  # type: ignore[index]
    assert result.output["truncated"] is True  # type: ignore[index]


# -- Durable store ---------------------------------------------------------------------


@pytest.fixture
async def sqlite_store(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_path = (tmp_path / "agent_tasks.db").as_posix()
    manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await manager.connect()
    await manager.create_all_tables()
    try:
        yield manager
    finally:
        await manager.disconnect()


async def test_pause_survives_a_process_restart_and_can_still_complete(sqlite_store: Any) -> None:
    first = _Env([_read_call()], store=StorageAgentTaskStore(RelationalDataStore(sqlite_store)))
    await first.orchestrator.run_task(first.task())
    pending = await first.pending()
    assert pending.execution_parameters == {"capability": "kortex.browser.read"}

    # A brand-new orchestrator + store over the same database: nothing
    # in-memory carries over, only the durable row.
    second = _Env([], store=StorageAgentTaskStore(RelationalDataStore(sqlite_store)))
    claimed = await second.orchestrator.claim_browser_execution("task-1", TENANT, pending.grant_id, OWNER)
    assert claimed.execution_parameters == {"capability": "kortex.browser.read"}
    result = second.port.classify_outcome(await second.pending(), READ_OUTCOME)
    assert await second.orchestrator.record_browser_execution_report("task-1", TENANT, pending.grant_id, OWNER, result)
    final = await second.orchestrator.resume_with_browser_result("task-1", TENANT)
    assert final.status == AgentStatus.COMPLETED
    assert final.steps[0].tool_results[0].output["page_text"] == "Hello page"  # type: ignore[index]


async def test_durable_store_cancels_a_browser_pause_and_lists_it_across_tenants(sqlite_store: Any) -> None:
    store = StorageAgentTaskStore(RelationalDataStore(sqlite_store))
    env = _Env([_read_call()], store=store)
    await env.orchestrator.run_task(env.task())
    paused = await store.list_tasks_by_status(AgentStatus.PAUSED_FOR_BROWSER_EXECUTION)
    assert [r.task.task_id for r in paused] == ["task-1"]
    assert await store.cancel_task("task-1", TENANT), "a Browser-paused task must be cancellable"
    assert await store.list_tasks_by_status(AgentStatus.PAUSED_FOR_BROWSER_EXECUTION) == []


async def test_durable_compare_and_update_refuses_a_stale_version(sqlite_store: Any) -> None:
    store = StorageAgentTaskStore(RelationalDataStore(sqlite_store))
    env = _Env([_read_call()], store=store)
    await env.orchestrator.run_task(env.task())
    record = await store.get_task("task-1", TENANT)
    assert record is not None
    stale = record.model_copy(update={"version": record.version + 1})
    assert await store.compare_and_update_task(stale, record.version, AgentStatus.PAUSED_FOR_BROWSER_EXECUTION)
    assert not await store.compare_and_update_task(stale, record.version, AgentStatus.PAUSED_FOR_BROWSER_EXECUTION)
    assert not await store.compare_and_update_task(
        stale.model_copy(update={"version": stale.version + 1}), stale.version, AgentStatus.PAUSED_FOR_APPROVAL
    )


async def test_orchestrator_without_a_browser_port_keeps_pre_b6_behavior() -> None:
    env = _Env([_read_call()])
    plain = AgentOrchestrator(
        tool_invoker=env.orchestrator._tool_invoker,
        llm_port=_llm([_read_call()]),
        context_port=env.context,
        approval_policy=AlwaysApprovePolicy(),
    )
    result = await plain.run_task(env.task())
    assert result.status == AgentStatus.COMPLETED
    assert "grant" in result.steps[0].tool_results[0].output  # type: ignore[operator]


def test_persisted_record_model_accepts_no_browser_execution() -> None:
    record = PersistedAgentTaskRecord(
        task=AgentTask(task_id="t", tenant_id=TENANT, user_id=OWNER, conversation_id="c", goal="g"),
        status=AgentStatus.RUNNING,
    )
    assert record.browser_execution is None
