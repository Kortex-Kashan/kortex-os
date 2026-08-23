"""Adversarial unit tests for the KORTEX OS AI Orchestration Engine — Milestone 7.

Tests are structured around the M7 specification's security invariants:
  1. Loop termination (step limit, timeout, repetition window)
  2. ResumeToken verification (mismatch, expiry, hash mismatch, replay)
  3. LLMOutputParser (malformed calls, missing name, bad pattern, oversized args)
  4. IApprovalPolicy separation (policy decides; M7 only responds to bool)
  5. IAgentContextPort separation (M4/M5 never directly called from agent.py)
  6. Cancellation token
  7. Multi-tenant identifier quarantine
  8. AST import quarantine (6 forbidden namespaces)
  9. No append_history() usage in agent.py
 10. Mutation probes (critical paths must be present)
 11. AlwaysApprovePolicy / AlwaysDenyPolicy reference fakes
 12. InMemoryLLMExecutionPort and InMemoryAgentContextPort fakes
 13. AgentOrchestrationError hierarchy
 14. ResumeToken issuance and round-trip
 15. Step callback invocation
 16. Concurrent task isolation
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from kortex.engines.ai.agent import (
    LOOP_DETECTION_WINDOW,
    AgentExecutionResult,
    AgentOrchestrator,
    AgentStatus,
    AgentStep,
    AgentTask,
    AlwaysApprovePolicy,
    AlwaysDenyPolicy,
    IAgentContextPort,
    IApprovalPolicy,
    ILLMExecutionPort,
    InMemoryAgentContextPort,
    InMemoryAgentTaskStore,
    InMemoryLLMExecutionPort,
    LLMOutputParser,
    ResumeToken,
    _hash_tool_calls,
    _issue_resume_token,
    _verify_resume_token,
)
from kortex.engines.ai.exceptions import (
    AgentCancelledError,
    AgentExecutionTimeoutError,
    AgentLoopDetectedError,
    AgentNotFoundError,
    AgentOrchestrationError,
    AgentStateConflictError,
    AgentStepLimitExceededError,
    AgentValidationError,
)
from kortex.engines.ai.models import LLMRequest, LLMResponse
from kortex.engines.ai.tools import (
    MAX_TOOL_ARGUMENTS_BYTES,
    AIToolInvoker,
    InMemoryToolExecutionPort,
    ToolCall,
    ToolDefinition,
    ToolRegistry,
)

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

_AGENT_PY = (
    Path(__file__).parent.parent.parent
    / "src" / "kortex" / "engines" / "ai" / "agent.py"
)


def _make_task(
    *,
    max_steps: int = 5,
    timeout_seconds: float = 30.0,
    require_approval: bool = False,
    task_id: str | None = None,
    tenant_id: str = "tenant-x",
    conversation_id: str = "conv-1",
    user_id: str = "user-1",
    goal: str = "Do something useful.",
) -> AgentTask:
    return AgentTask(
        task_id=task_id or f"task-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        goal=goal,
        require_human_approval_for_mutations=require_approval,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
    )


def _tool_call(name: str = "get_data", arguments: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(
        call_id=f"call-{uuid.uuid4().hex[:8]}",
        tool_name=name,
        arguments=arguments or {},
    )


def _llm_response(
    *,
    text: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    request_id: str | None = None,
) -> LLMResponse:
    return LLMResponse(
        request_id=request_id or f"req-{uuid.uuid4().hex[:8]}",
        text_content=text,
        tool_calls=tool_calls or [],
    )


def _terminal_response(request_id: str | None = None) -> LLMResponse:
    """A response with text and no tool calls — causes the loop to COMPLETE."""
    return _llm_response(text="All done.", request_id=request_id)


def _tool_response(name: str = "get_data", args: dict[str, Any] | None = None) -> LLMResponse:
    """A response that proposes one tool call."""
    return _llm_response(tool_calls=[{"name": name, "arguments": args or {}}])


def _make_invoker(
    *,
    handler: object = None,
    tool_name: str = "get_data",
) -> AIToolInvoker:
    """Build an AIToolInvoker wired to an InMemoryToolExecutionPort."""
    registry = ToolRegistry()
    registry.register_tool(
        ToolDefinition(
            name=tool_name,
            description="Test tool",
            parameters_schema={"type": "object", "properties": {}},
            canonical_capability=f"kortex.ai.tool.{tool_name}",
        )
    )

    async def _default_handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"result": "ok"}

    port = InMemoryToolExecutionPort()
    port.register_handler(f"kortex.ai.tool.{tool_name}", handler or _default_handler)
    return AIToolInvoker(registry=registry, execution_port=port)


def _make_orchestrator(
    responses: list[LLMResponse],
    *,
    policy: object = None,
    tool_name: str = "get_data",
    handler: object = None,
    task_store: object = None,
) -> AgentOrchestrator:
    """Build an orchestrator.

    `task_store` exists so a pause/resume round trip can share one store
    across two orchestrator instances, which is how production behaves: both
    processes read the same durable store. Omitting it gives each
    orchestrator its own private `InMemoryAgentTaskStore`.
    """
    invoker = _make_invoker(tool_name=tool_name, handler=handler)
    llm_port = InMemoryLLMExecutionPort(responses=responses)
    ctx_port = InMemoryAgentContextPort()
    approval = policy if policy is not None else AlwaysApprovePolicy()
    return AgentOrchestrator(
        tool_invoker=invoker,
        llm_port=llm_port,
        context_port=ctx_port,
        approval_policy=approval,
        task_store=task_store,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# §1 — AST Import Quarantine (6 forbidden namespaces)
# ---------------------------------------------------------------------------


def _collect_imports(path: Path) -> list[str]:
    """Return all top-level import module strings from the given Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


FORBIDDEN_NAMESPACES = [
    "kortex.core.kernel",
    "kortex.core.container",
    "kortex.engines.security",
    "kortex.engines.knowledge",
    "kortex.engines.ai.pipeline",
    "kortex.engines.ai.memory",
]


@pytest.mark.parametrize("forbidden", FORBIDDEN_NAMESPACES)
def test_agent_py_does_not_import_forbidden_namespace(forbidden: str) -> None:
    """AST quarantine: agent.py must not directly import any forbidden namespace."""
    imports = _collect_imports(_AGENT_PY)
    violations = [imp for imp in imports if imp == forbidden or imp.startswith(forbidden + ".")]
    assert violations == [], (
        f"agent.py illegally imports forbidden namespace {forbidden!r}: {violations}"
    )


def test_agent_py_does_not_call_append_history() -> None:
    """M7 must never call append_history() — persistence is the caller's job."""
    source = _AGENT_PY.read_text(encoding="utf-8")
    assert "append_history" not in source, (
        "agent.py contains a call to append_history(), violating the M7 persistence boundary."
    )


# ---------------------------------------------------------------------------
# §2 — Exception Hierarchy
# ---------------------------------------------------------------------------


def test_agent_orchestration_error_is_ai_orchestration_error() -> None:
    from kortex.engines.ai.exceptions import AIOrchestrationError
    assert issubclass(AgentOrchestrationError, AIOrchestrationError)


@pytest.mark.parametrize("exc_cls", [
    AgentValidationError,
    AgentExecutionTimeoutError,
    AgentStepLimitExceededError,
    AgentLoopDetectedError,
    AgentCancelledError,
])
def test_agent_exception_leaf_types_inherit_base(exc_cls: type) -> None:
    assert issubclass(exc_cls, AgentOrchestrationError)


def test_agent_validation_error_carries_task_id() -> None:
    exc = AgentValidationError(task_id="t-123", message="bad task")
    assert exc.task_id == "t-123"
    assert "bad task" in str(exc)


@pytest.mark.asyncio
async def test_agent_exception_messages_never_contain_task_goal_or_tenant_values() -> None:
    """M7 section 8 exception hygiene, exercised against the real raising paths.

    Distinctive sentinel values are planted in every caller-supplied field
    that could carry tenant data, then genuine validation failures are
    triggered. No sentinel VALUE may appear in the raised message — the word
    "tenant" as a field name is fine, the tenant's actual identifier is not.

    Replaces an earlier assertion of the form
    `assert "tenant" not in msg or "tenant" == "tenant"`, whose right-hand
    disjunct is a tautology: it passed unconditionally and therefore proved
    nothing, while reading as though it enforced this invariant.
    """
    sentinel_goal = "GOAL-SENTINEL-exfiltrate-payroll"
    sentinel_tenant = "TENANT-SENTINEL-acme-corp"
    sentinel_user = "USER-SENTINEL-alice"
    sentinel_conversation = "CONV-SENTINEL-9f3a"

    orchestrator = _make_orchestrator(responses=[])

    # Path 1: blank tenant_id rejected before the loop starts.
    blank_tenant_task = _make_task(
        tenant_id="   ",
        goal=sentinel_goal,
        user_id=sentinel_user,
        conversation_id=sentinel_conversation,
    )
    with pytest.raises(AgentValidationError) as exc_info:
        await orchestrator.run_task(blank_tenant_task)
    message = str(exc_info.value)
    assert sentinel_goal not in message
    assert sentinel_user not in message
    assert sentinel_conversation not in message

    # Path 2: blank conversation_id, with a real tenant value present.
    blank_conversation_task = _make_task(
        tenant_id=sentinel_tenant,
        conversation_id="   ",
        goal=sentinel_goal,
        user_id=sentinel_user,
    )
    with pytest.raises(AgentValidationError) as exc_info:
        await orchestrator.run_task(blank_conversation_task)
    message = str(exc_info.value)
    assert sentinel_tenant not in message
    assert sentinel_goal not in message
    assert sentinel_user not in message


# ---------------------------------------------------------------------------
# §3 — Domain Model Contracts
# ---------------------------------------------------------------------------


def test_agent_task_is_frozen() -> None:
    from pydantic import ValidationError
    task = _make_task()
    with pytest.raises((ValidationError, TypeError)):
        task.goal = "new goal"  # type: ignore[misc]


def test_agent_task_requires_non_empty_goal() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentTask(
            task_id="t-1", tenant_id="x", user_id="u", conversation_id="c",
            goal="",  # empty
        )


def test_agent_task_max_steps_bounded() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentTask(
            task_id="t-1", tenant_id="x", user_id="u", conversation_id="c",
            goal="g", max_steps=0,
        )
    with pytest.raises(ValidationError):
        AgentTask(
            task_id="t-1", tenant_id="x", user_id="u", conversation_id="c",
            goal="g", max_steps=31,
        )


def test_agent_task_timeout_bounded() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentTask(
            task_id="t-1", tenant_id="x", user_id="u", conversation_id="c",
            goal="g", timeout_seconds=0.5,
        )
    with pytest.raises(ValidationError):
        AgentTask(
            task_id="t-1", tenant_id="x", user_id="u", conversation_id="c",
            goal="g", timeout_seconds=601.0,
        )


def test_agent_execution_result_is_frozen() -> None:
    from pydantic import ValidationError
    result = AgentExecutionResult(
        task_id="t", tenant_id="n", status=AgentStatus.COMPLETED
    )
    with pytest.raises((ValidationError, TypeError)):
        result.status = AgentStatus.FAILED  # type: ignore[misc]


def test_agent_step_is_frozen() -> None:
    from pydantic import ValidationError
    step = AgentStep(step_number=1)
    with pytest.raises((ValidationError, TypeError)):
        step.step_number = 2  # type: ignore[misc]


def test_resume_token_is_frozen() -> None:
    from pydantic import ValidationError
    token = ResumeToken(
        task_id="t", step_count_at_pause=0, pending_call_hash="abc",
        issued_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T01:00:00+00:00",
    )
    with pytest.raises((ValidationError, TypeError)):
        token.task_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# §4 — Multi-tenant Identifier Quarantine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_task_rejects_blank_tenant_id() -> None:
    orch = _make_orchestrator([_terminal_response()])
    task = _make_task(tenant_id="   ")
    with pytest.raises(AgentValidationError):
        await orch.run_task(task)


@pytest.mark.asyncio
async def test_run_task_rejects_blank_conversation_id() -> None:
    orch = _make_orchestrator([_terminal_response()])
    task = _make_task(conversation_id="   ")
    with pytest.raises(AgentValidationError):
        await orch.run_task(task)


@pytest.mark.asyncio
async def test_run_task_rejects_blank_task_id() -> None:
    """Pydantic min_length=1 prevents blank task_id at construction time."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentTask(
            task_id="",
            tenant_id="x", user_id="u", conversation_id="c", goal="g",
        )


# ---------------------------------------------------------------------------
# §5 — LLMOutputParser
# ---------------------------------------------------------------------------


@pytest.fixture
def parser() -> LLMOutputParser:
    return LLMOutputParser()


def test_parser_empty_tool_calls_returns_empty(parser: LLMOutputParser) -> None:
    result = parser.parse_tool_calls(_llm_response(text="done"))
    assert result == []


def test_parser_valid_call_returns_tool_call(parser: LLMOutputParser) -> None:
    response = _llm_response(tool_calls=[{"name": "get_data", "arguments": {"q": 1}}])
    calls = parser.parse_tool_calls(response)
    assert len(calls) == 1
    assert calls[0].tool_name == "get_data"
    assert calls[0].arguments == {"q": 1}


def test_parser_auto_generates_call_id_when_absent(parser: LLMOutputParser) -> None:
    response = _llm_response(tool_calls=[{"name": "do_it"}])
    calls = parser.parse_tool_calls(response)
    assert calls[0].call_id.startswith("call-")


def test_parser_preserves_call_id_when_present(parser: LLMOutputParser) -> None:
    response = _llm_response(tool_calls=[{"name": "do_it", "call_id": "my-id-42"}])
    calls = parser.parse_tool_calls(response)
    assert calls[0].call_id == "my-id-42"


def test_parser_missing_name_raises(parser: LLMOutputParser) -> None:
    from kortex.engines.ai.exceptions import ToolValidationError
    response = _llm_response(tool_calls=[{"arguments": {}}])
    with pytest.raises(ToolValidationError, match="missing or blank"):
        parser.parse_tool_calls(response)


def test_parser_blank_name_raises(parser: LLMOutputParser) -> None:
    from kortex.engines.ai.exceptions import ToolValidationError
    response = _llm_response(tool_calls=[{"name": "   "}])
    with pytest.raises(ToolValidationError, match="missing or blank"):
        parser.parse_tool_calls(response)


def test_parser_invalid_name_pattern_raises(parser: LLMOutputParser) -> None:
    from kortex.engines.ai.exceptions import ToolValidationError
    response = _llm_response(tool_calls=[{"name": "bad name!"}])
    with pytest.raises(ToolValidationError, match="invalid name"):
        parser.parse_tool_calls(response)


def test_parser_arguments_not_dict_raises(parser: LLMOutputParser) -> None:
    from kortex.engines.ai.exceptions import ToolValidationError
    response = _llm_response(tool_calls=[{"name": "tool", "arguments": ["list"]}])
    with pytest.raises(ToolValidationError, match="must be a dict"):
        parser.parse_tool_calls(response)


def test_parser_raw_call_not_dict_raises(parser: LLMOutputParser) -> None:
    from kortex.engines.ai.exceptions import ToolValidationError
    # LLMResponse.tool_calls is list[dict], so we force a non-dict entry via model_construct
    response = LLMResponse.model_construct(
        request_id="r1", text_content="", tool_calls=["not-a-dict"]
    )
    with pytest.raises(ToolValidationError, match="must be a dict"):
        parser.parse_tool_calls(response)


def test_parser_oversized_arguments_raises(parser: LLMOutputParser) -> None:
    from kortex.engines.ai.exceptions import ToolValidationError
    # Build a payload that exceeds MAX_TOOL_ARGUMENTS_BYTES
    big_value = "x" * (MAX_TOOL_ARGUMENTS_BYTES + 100)
    response = _llm_response(tool_calls=[{"name": "tool", "arguments": {"k": big_value}}])
    with pytest.raises(ToolValidationError, match="exceed"):
        parser.parse_tool_calls(response)


def test_parser_absent_arguments_defaults_to_empty_dict(parser: LLMOutputParser) -> None:
    response = _llm_response(tool_calls=[{"name": "tool"}])
    calls = parser.parse_tool_calls(response)
    assert calls[0].arguments == {}


def test_parser_extract_thought_returns_none_for_empty(parser: LLMOutputParser) -> None:
    response = _llm_response(text="   ")
    assert parser.extract_thought(response) is None


def test_parser_extract_thought_returns_stripped_text(parser: LLMOutputParser) -> None:
    response = _llm_response(text="  I think I should call get_data.  ")
    assert parser.extract_thought(response) == "I think I should call get_data."


def test_parser_malformed_at_index_1_raises_with_index(parser: LLMOutputParser) -> None:
    from kortex.engines.ai.exceptions import ToolValidationError
    response = _llm_response(tool_calls=[
        {"name": "ok_tool"},
        {"name": ""},   # malformed
    ])
    with pytest.raises(ToolValidationError, match="index 1"):
        parser.parse_tool_calls(response)


# ---------------------------------------------------------------------------
# §6 — Happy Path Execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_task_completes_with_text_response() -> None:
    orch = _make_orchestrator([_terminal_response()])
    task = _make_task()
    result = await orch.run_task(task)
    assert result.status == AgentStatus.COMPLETED
    assert result.final_response == "All done."
    assert result.task_id == task.task_id
    assert result.tenant_id == task.tenant_id


@pytest.mark.asyncio
async def test_run_task_tool_then_complete() -> None:
    """One tool call followed by a terminal response → COMPLETED with two steps."""
    orch = _make_orchestrator([
        _tool_response("get_data"),
        _terminal_response(),
    ])
    task = _make_task(max_steps=5)
    result = await orch.run_task(task)
    assert result.status == AgentStatus.COMPLETED
    assert result.total_steps == 2


@pytest.mark.asyncio
async def test_run_task_step_count_increments_correctly() -> None:
    orch = _make_orchestrator([
        _tool_response("get_data"),
        _tool_response("get_data"),
        _terminal_response(),
    ])
    task = _make_task(max_steps=10)
    result = await orch.run_task(task)
    assert result.total_steps == 3


# ---------------------------------------------------------------------------
# §7 — Step Budget Enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_steps_terminates_with_step_limit_exceeded() -> None:
    """A model that always emits tool calls must eventually hit STEP_LIMIT_EXCEEDED.

    We set max_steps=2, which is below LOOP_DETECTION_WINDOW (3), so the budget
    check fires before the loop detector can accumulate enough identical hashes.
    """
    orch = _make_orchestrator([_tool_response("get_data")] * 20)
    task = _make_task(max_steps=2)
    result = await orch.run_task(task)
    assert result.status == AgentStatus.STEP_LIMIT_EXCEEDED


@pytest.mark.asyncio
async def test_step_limit_one_terminates_immediately() -> None:
    orch = _make_orchestrator([_tool_response("get_data")] * 5)
    task = _make_task(max_steps=1)
    result = await orch.run_task(task)
    assert result.status == AgentStatus.STEP_LIMIT_EXCEEDED


@pytest.mark.asyncio
async def test_step_limit_error_message_contains_limit() -> None:
    orch = _make_orchestrator([_tool_response("get_data")] * 10)
    task = _make_task(max_steps=2)
    result = await orch.run_task(task)
    assert result.error_message is not None
    assert "2" in result.error_message


# ---------------------------------------------------------------------------
# §8 — Loop Detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_detection_triggers_after_window() -> None:
    """Identical call batches repeated LOOP_DETECTION_WINDOW times → LOOP_DETECTED."""
    responses = [_tool_response("get_data")] * (LOOP_DETECTION_WINDOW + 2)
    orch = _make_orchestrator(responses)
    task = _make_task(max_steps=30)
    result = await orch.run_task(task)
    assert result.status == AgentStatus.LOOP_DETECTED


@pytest.mark.asyncio
async def test_loop_detection_does_not_trigger_with_varied_calls() -> None:
    """Different tool calls on each turn must not trigger loop detection."""
    responses = [
        _tool_response("get_data"),
        _tool_response("save_data"),
        _tool_response("list_data"),
        _terminal_response(),
    ]
    # Build a ToolRegistry with multiple tools
    registry = ToolRegistry()
    for name in ("get_data", "save_data", "list_data"):
        registry.register_tool(
            ToolDefinition(
                name=name,
                description="t",
                parameters_schema={"type": "object", "properties": {}},
                canonical_capability=f"kortex.ai.tool.{name}",
            )
        )
    port = InMemoryToolExecutionPort()
    for name in ("get_data", "save_data", "list_data"):
        async def _h(args: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}
        port.register_handler(f"kortex.ai.tool.{name}", _h)
    full_invoker = AIToolInvoker(registry=registry, execution_port=port)

    llm_port = InMemoryLLMExecutionPort(responses=responses)
    ctx_port = InMemoryAgentContextPort()
    orch2 = AgentOrchestrator(
        tool_invoker=full_invoker,
        llm_port=llm_port,
        context_port=ctx_port,
        approval_policy=AlwaysApprovePolicy(),
    )
    task = _make_task(max_steps=20)
    result = await orch2.run_task(task)
    assert result.status == AgentStatus.COMPLETED


@pytest.mark.asyncio
async def test_loop_detection_message_contains_window_count() -> None:
    responses = [_tool_response("get_data")] * 10
    orch = _make_orchestrator(responses)
    task = _make_task(max_steps=30)
    result = await orch.run_task(task)
    assert result.error_message is not None
    assert str(LOOP_DETECTION_WINDOW) in result.error_message


# ---------------------------------------------------------------------------
# §9 — Timeout Enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_terminates_with_timed_out() -> None:
    """A very slow LLM response with a very tight timeout → TIMED_OUT."""

    class SlowLLMPort:
        async def generate_step(self, request: LLMRequest) -> LLMResponse:
            await asyncio.sleep(10)  # Sleeps far beyond the 1s task timeout
            return _terminal_response()

    invoker = _make_invoker()
    ctx_port = InMemoryAgentContextPort()
    orch = AgentOrchestrator(
        tool_invoker=invoker,
        llm_port=SlowLLMPort(),
        context_port=ctx_port,
        approval_policy=AlwaysApprovePolicy(),
    )
    task = _make_task(timeout_seconds=1.0)
    result = await orch.run_task(task)
    assert result.status == AgentStatus.TIMED_OUT


# ---------------------------------------------------------------------------
# §10 — Cancellation Token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_token_stops_loop() -> None:
    """Setting the cancellation token before run_task returns CANCELLED."""
    token = asyncio.Event()
    token.set()  # Pre-set before calling run_task

    orch = _make_orchestrator([_terminal_response()])
    task = _make_task()
    result = await orch.run_task(task, cancellation_token=token)
    assert result.status == AgentStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancellation_token_not_set_runs_normally() -> None:
    token = asyncio.Event()  # not set

    orch = _make_orchestrator([_terminal_response()])
    task = _make_task()
    result = await orch.run_task(task, cancellation_token=token)
    assert result.status == AgentStatus.COMPLETED


# ---------------------------------------------------------------------------
# §11 — IApprovalPolicy Separation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_always_deny_policy_pauses_task() -> None:
    """AlwaysDenyPolicy must produce PAUSED_FOR_APPROVAL with a ResumeToken."""
    orch = _make_orchestrator([_tool_response("get_data")], policy=AlwaysDenyPolicy())
    task = _make_task()
    result = await orch.run_task(task)
    assert result.status == AgentStatus.PAUSED_FOR_APPROVAL
    assert result.resume_token is not None
    assert len(result.pending_tool_calls) == 1


@pytest.mark.asyncio
async def test_always_approve_policy_proceeds_without_pause() -> None:
    orch = _make_orchestrator(
        [_tool_response("get_data"), _terminal_response()],
        policy=AlwaysApprovePolicy(),
    )
    task = _make_task()
    result = await orch.run_task(task)
    assert result.status == AgentStatus.COMPLETED
    assert result.resume_token is None


@pytest.mark.asyncio
async def test_orchestrator_does_not_read_require_approval_flag_directly() -> None:
    """AgentOrchestrator must delegate to IApprovalPolicy, not read the task flag.
    Even if require_human_approval_for_mutations=True, AlwaysApprovePolicy overrides."""
    task = _make_task(require_approval=True)  # flag=True but policy=always-approve
    orch = _make_orchestrator(
        [_tool_response("get_data"), _terminal_response()],
        policy=AlwaysApprovePolicy(),
    )
    result = await orch.run_task(task)
    # Must be COMPLETED, not PAUSED — policy controls, not the task flag
    assert result.status == AgentStatus.COMPLETED


# ---------------------------------------------------------------------------
# §12 — ResumeToken Issuance & Verification
# ---------------------------------------------------------------------------


def test_issue_resume_token_fields_populated() -> None:
    calls = [_tool_call("get_data")]
    token = _issue_resume_token("task-1", step_count=3, pending_calls=calls)
    assert token.task_id == "task-1"
    assert token.step_count_at_pause == 3
    assert len(token.pending_call_hash) == 64  # SHA-256 hex
    assert token.issued_at < token.expires_at


def test_resume_token_hash_matches_canonical_serialization() -> None:
    calls = [_tool_call("get_data", {"k": "v"})]
    token = _issue_resume_token("t", 0, calls)
    expected = hashlib.sha256(
        json.dumps(
            [{"tool_name": c.tool_name, "arguments": c.arguments} for c in calls],
            sort_keys=True, ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    assert token.pending_call_hash == expected


def test_verify_resume_token_accepts_valid_token() -> None:
    calls = [_tool_call("get_data")]
    task = _make_task()
    token = _issue_resume_token(task.task_id, 3, calls)
    # Must not raise
    _verify_resume_token(token, task, step_count=3, approved_calls=calls)


def test_verify_resume_token_rejects_wrong_task_id() -> None:
    calls = [_tool_call("get_data")]
    task = _make_task(task_id="real-task")
    other_task = _make_task(task_id="other-task")
    token = _issue_resume_token(other_task.task_id, 0, calls)
    with pytest.raises(AgentValidationError, match="task_id"):
        _verify_resume_token(token, task, step_count=0, approved_calls=calls)


def test_verify_resume_token_rejects_wrong_step_count() -> None:
    calls = [_tool_call("get_data")]
    task = _make_task()
    token = _issue_resume_token(task.task_id, step_count=2, pending_calls=calls)
    with pytest.raises(AgentValidationError, match="step_count"):
        _verify_resume_token(token, task, step_count=99, approved_calls=calls)


def test_verify_resume_token_rejects_wrong_call_hash() -> None:
    calls_original = [_tool_call("get_data")]
    calls_forged = [_tool_call("delete_everything")]
    task = _make_task()
    token = _issue_resume_token(task.task_id, 0, calls_original)
    with pytest.raises(AgentValidationError, match="pending_call_hash"):
        _verify_resume_token(token, task, step_count=0, approved_calls=calls_forged)


def test_verify_resume_token_rejects_expired_token() -> None:
    calls = [_tool_call("get_data")]
    task = _make_task()
    # Issue a token with an expires_at in the past
    now = datetime.datetime.now(datetime.UTC)
    past = now - datetime.timedelta(seconds=1)
    expired_token = ResumeToken(
        task_id=task.task_id,
        step_count_at_pause=0,
        pending_call_hash=_hash_tool_calls(calls),
        issued_at=(now - datetime.timedelta(hours=2)).isoformat(),
        expires_at=past.isoformat(),
    )
    with pytest.raises(AgentValidationError, match="expired"):
        _verify_resume_token(expired_token, task, step_count=0, approved_calls=calls)


def test_verify_resume_token_rejects_bad_expires_at_format() -> None:
    calls = [_tool_call("get_data")]
    task = _make_task()
    bad_token = ResumeToken(
        task_id=task.task_id,
        step_count_at_pause=0,
        pending_call_hash=_hash_tool_calls(calls),
        issued_at="2026-01-01T00:00:00+00:00",
        expires_at="not-a-date",
    )
    with pytest.raises(AgentValidationError, match="unparsable"):
        _verify_resume_token(bad_token, task, step_count=0, approved_calls=calls)


# ---------------------------------------------------------------------------
# §13 — resume_task() Round-Trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_task_completes_after_approval() -> None:
    """Full pause → approve → complete round trip.

    The two orchestrators share one task store, which is what production
    does: the resuming process reads the same durable store the pausing
    process wrote to. A resume against a store with no record is refused
    outright — see `test_resume_task_refuses_when_no_persisted_record_exists`.
    """
    shared_store = InMemoryAgentTaskStore()

    # Step 1: run_task pauses
    orch = _make_orchestrator(
        [_tool_response("get_data")], policy=AlwaysDenyPolicy(), task_store=shared_store
    )
    task = _make_task()
    paused = await orch.run_task(task)
    assert paused.status == AgentStatus.PAUSED_FOR_APPROVAL
    assert paused.resume_token is not None

    # Step 2: resume_task with same approved calls and valid token
    orch2 = _make_orchestrator(
        [_terminal_response()], policy=AlwaysApprovePolicy(), task_store=shared_store
    )
    resumed = await orch2.resume_task(
        task=task,
        resume_token=paused.resume_token,
        approved_tool_calls=paused.pending_tool_calls,
    )
    assert resumed.status == AgentStatus.COMPLETED


@pytest.mark.asyncio
async def test_resume_task_refuses_when_no_persisted_record_exists() -> None:
    """A cryptographically valid token is not, by itself, authority to resume.

    The atomic PAUSED_FOR_APPROVAL -> RESUMING claim is the single-use
    guarantee; without a stored record there is nothing to claim, so the
    resume must fail rather than proceed on the token alone.
    """
    orch = _make_orchestrator([_tool_response("get_data")], policy=AlwaysDenyPolicy())
    task = _make_task()
    paused = await orch.run_task(task)
    assert paused.resume_token is not None

    # A different process whose store has no record of this task.
    orch_without_record = _make_orchestrator([_terminal_response()])
    with pytest.raises(AgentNotFoundError):
        await orch_without_record.resume_task(
            task=task,
            resume_token=paused.resume_token,
            approved_tool_calls=paused.pending_tool_calls,
        )


@pytest.mark.asyncio
async def test_approval_token_cannot_be_replayed_after_a_successful_resume() -> None:
    """One human approval authorizes exactly one execution of the approved calls.

    Without this, an unexpired token could re-run already-approved mutating
    tool calls repeatedly — precisely what the approval workflow exists to
    prevent for critical operations.
    """
    shared_store = InMemoryAgentTaskStore()
    orch = _make_orchestrator(
        [_tool_response("get_data")], policy=AlwaysDenyPolicy(), task_store=shared_store
    )
    task = _make_task()
    paused = await orch.run_task(task)
    assert paused.resume_token is not None

    first = _make_orchestrator(
        [_terminal_response()], policy=AlwaysApprovePolicy(), task_store=shared_store
    )
    resumed = await first.resume_task(
        task=task,
        resume_token=paused.resume_token,
        approved_tool_calls=paused.pending_tool_calls,
    )
    assert resumed.status == AgentStatus.COMPLETED

    # Replaying the very same token against the same store must be refused.
    second = _make_orchestrator(
        [_terminal_response()], policy=AlwaysApprovePolicy(), task_store=shared_store
    )
    with pytest.raises(AgentStateConflictError):
        await second.resume_task(
            task=task,
            resume_token=paused.resume_token,
            approved_tool_calls=paused.pending_tool_calls,
        )


@pytest.mark.asyncio
async def test_resume_task_rejects_forged_approved_calls() -> None:
    """Caller cannot swap approved_tool_calls to bypass the hash check."""
    orch = _make_orchestrator([_tool_response("get_data")], policy=AlwaysDenyPolicy())
    task = _make_task()
    paused = await orch.run_task(task)
    token = paused.resume_token
    assert token is not None

    forged_calls = [_tool_call("delete_all")]
    orch2 = _make_orchestrator([_terminal_response()])
    with pytest.raises(AgentValidationError, match="pending_call_hash"):
        await orch2.resume_task(
            task=task,
            resume_token=token,
            approved_tool_calls=forged_calls,
        )


@pytest.mark.asyncio
async def test_resume_task_rejects_expired_token() -> None:
    orch = _make_orchestrator([_tool_response("get_data")], policy=AlwaysDenyPolicy())
    task = _make_task()
    paused = await orch.run_task(task)
    token = paused.resume_token
    assert token is not None

    # Manufacture an expired clone of the token
    now = datetime.datetime.now(datetime.UTC)
    expired = ResumeToken(
        task_id=token.task_id,
        step_count_at_pause=token.step_count_at_pause,
        pending_call_hash=token.pending_call_hash,
        issued_at=(now - datetime.timedelta(hours=2)).isoformat(),
        expires_at=(now - datetime.timedelta(seconds=1)).isoformat(),
    )
    orch2 = _make_orchestrator([_terminal_response()])
    with pytest.raises(AgentValidationError, match="expired"):
        await orch2.resume_task(
            task=task,
            resume_token=expired,
            approved_tool_calls=paused.pending_tool_calls,
        )


@pytest.mark.asyncio
async def test_resume_task_rejects_mismatched_task_id() -> None:
    orch = _make_orchestrator([_tool_response("get_data")], policy=AlwaysDenyPolicy())
    task = _make_task(task_id="original-task")
    paused = await orch.run_task(task)
    token = paused.resume_token
    assert token is not None

    different_task = _make_task(task_id="different-task")
    orch2 = _make_orchestrator([_terminal_response()])
    with pytest.raises(AgentValidationError, match="task_id"):
        await orch2.resume_task(
            task=different_task,
            resume_token=token,
            approved_tool_calls=paused.pending_tool_calls,
        )


# ---------------------------------------------------------------------------
# §14 — Step Callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_callback_called_for_each_step() -> None:
    step_records: list[AgentStep] = []

    async def _callback(step: AgentStep) -> None:
        step_records.append(step)

    orch = _make_orchestrator([
        _tool_response("get_data"),
        _terminal_response(),
    ])
    task = _make_task(max_steps=10)
    await orch.run_task(task, step_callback=_callback)
    # Step 1: tool call + results; Step 2: final text response
    assert len(step_records) == 2
    assert step_records[0].step_number == 1
    assert step_records[1].step_number == 2


@pytest.mark.asyncio
async def test_step_callback_none_does_not_crash() -> None:
    orch = _make_orchestrator([_terminal_response()])
    task = _make_task()
    result = await orch.run_task(task, step_callback=None)
    assert result.status == AgentStatus.COMPLETED


# ---------------------------------------------------------------------------
# §15 — Protocol Conformance Checks (runtime_checkable)
# ---------------------------------------------------------------------------


def test_in_memory_llm_port_satisfies_protocol() -> None:
    port = InMemoryLLMExecutionPort()
    assert isinstance(port, ILLMExecutionPort)


def test_in_memory_context_port_satisfies_protocol() -> None:
    port = InMemoryAgentContextPort()
    assert isinstance(port, IAgentContextPort)


def test_always_approve_policy_satisfies_protocol() -> None:
    policy = AlwaysApprovePolicy()
    assert isinstance(policy, IApprovalPolicy)


def test_always_deny_policy_satisfies_protocol() -> None:
    policy = AlwaysDenyPolicy()
    assert isinstance(policy, IApprovalPolicy)


# ---------------------------------------------------------------------------
# §16 — AgentExecutionResult Invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_result_has_no_resume_token() -> None:
    orch = _make_orchestrator([_terminal_response()])
    result = await orch.run_task(_make_task())
    assert result.resume_token is None


@pytest.mark.asyncio
async def test_paused_result_has_resume_token() -> None:
    orch = _make_orchestrator([_tool_response("get_data")], policy=AlwaysDenyPolicy())
    result = await orch.run_task(_make_task())
    assert result.status == AgentStatus.PAUSED_FOR_APPROVAL
    assert result.resume_token is not None
    assert result.resume_token.task_id == result.task_id


@pytest.mark.asyncio
async def test_result_task_id_and_tenant_id_match_task() -> None:
    task = _make_task(task_id="specific-task", tenant_id="specific-tenant")
    orch = _make_orchestrator([_terminal_response()])
    result = await orch.run_task(task)
    assert result.task_id == "specific-task"
    assert result.tenant_id == "specific-tenant"


# ---------------------------------------------------------------------------
# §17 — Mutation Probes (critical guard-rails must be present in source)
# ---------------------------------------------------------------------------


def test_step_budget_check_exists_in_source() -> None:
    """Mutation probe: step budget check must exist in agent.py."""
    source = _AGENT_PY.read_text(encoding="utf-8")
    assert "max_steps" in source, "Step budget guard-rail missing from agent.py"


def test_loop_detection_check_exists_in_source() -> None:
    """Mutation probe: loop detection must be present."""
    source = _AGENT_PY.read_text(encoding="utf-8")
    assert "LOOP_DETECTION_WINDOW" in source, "Loop detection guard-rail missing from agent.py"


def test_resume_token_verification_exists_in_source() -> None:
    """Mutation probe: token verification must be called in resume_task."""
    source = _AGENT_PY.read_text(encoding="utf-8")
    assert "_verify_resume_token" in source, "ResumeToken verification missing from agent.py"


def test_output_parser_call_exists_in_source() -> None:
    """Mutation probe: LLM output must be parsed via the dedicated parser."""
    source = _AGENT_PY.read_text(encoding="utf-8")
    assert "parse_tool_calls" in source, "LLMOutputParser.parse_tool_calls not called in agent.py"


def test_identifier_validation_exists_in_source() -> None:
    """Mutation probe: multi-tenant identifier validation must be present."""
    source = _AGENT_PY.read_text(encoding="utf-8")
    assert "_validate_task_identifiers" in source, "Identifier validation missing from agent.py"


def test_no_persistence_calls_in_source() -> None:
    """The agent module must never persist conversation history itself.

    M7 shipped with no persistence at all. M11 then gave the orchestrator a
    durable `IAgentTaskStore` for *task* state, so "zero writes" is no
    longer the boundary — but conversation history remains M4's table,
    written only via `AIMemoryManager.append_history` by the M8 facade.
    This asserts the boundary that actually holds today.
    """
    source = _AGENT_PY.read_text(encoding="utf-8")
    assert "append_history" not in source
    assert "save_steps" not in source


def test_agent_writes_only_through_the_task_store_port() -> None:
    """Every durable write in `agent.py` must go through `IAgentTaskStore`.

    Guards the port boundary directly: a future edit that reaches for a
    session, engine, or raw SQL — instead of the injected store — fails
    here rather than silently bypassing the persistence adapter.
    """
    source = _AGENT_PY.read_text(encoding="utf-8")

    forbidden_infrastructure = (
        "sqlalchemy",
        "AsyncSession",
        "session.add",
        "session.execute",
        "execute_in_transaction",
        "IDataStore",
        "SELECT ",
        "INSERT ",
        "UPDATE ",
    )
    for marker in forbidden_infrastructure:
        assert marker not in source, f"agent.py must not touch infrastructure directly: {marker}"

    # The writes it does perform are all task-store port calls.
    write_calls = re.findall(r"self\._task_store\.(\w+)\(", source)
    assert set(write_calls) <= {
        "save_task",
        "update_task",
        "get_task",
        "claim_task_for_resumption",
        "cancel_task",
        "list_tasks",
    }, f"unexpected task-store method used: {sorted(set(write_calls))}"
    assert "update_task" in write_calls, "expected the orchestrator to persist task state"


# ---------------------------------------------------------------------------
# §18 — __init__.py exports all M7 symbols
# ---------------------------------------------------------------------------


def test_init_exports_m7_symbols() -> None:
    import kortex.engines.ai as pkg

    for name in [
        "AgentTask", "AgentStep", "AgentStatus", "AgentExecutionResult",
        "AgentOrchestrator", "ResumeToken", "LLMOutputParser",
        "ILLMExecutionPort", "IAgentContextPort", "IApprovalPolicy",
        "InMemoryLLMExecutionPort", "InMemoryAgentContextPort",
        "AlwaysApprovePolicy", "AlwaysDenyPolicy",
        "AgentOrchestrationError", "AgentValidationError",
        "AgentExecutionTimeoutError", "AgentStepLimitExceededError",
        "AgentLoopDetectedError", "AgentCancelledError",
        "LOOP_DETECTION_WINDOW", "RESUME_TOKEN_TTL_SECONDS",
    ]:
        assert hasattr(pkg, name), f"kortex.engines.ai missing M7 export: {name}"
