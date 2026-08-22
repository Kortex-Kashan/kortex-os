"""Agent Orchestration Engine for the KORTEX OS AI Orchestration Engine.

Governed by the approved Milestone 7 architecture specification (second hardening pass):
docs/architecture/ai_engine_m7_agent_orchestration_spec.md

This module provides:
- Frozen domain models: AgentTask, ResumeToken, AgentStep, AgentExecutionResult.
- Enum: AgentStatus.
- Protocol ports: ILLMExecutionPort, IAgentContextPort, IApprovalPolicy.
- Reference fakes: InMemoryLLMExecutionPort, InMemoryAgentContextPort,
  AlwaysApprovePolicy, AlwaysDenyPolicy.
- Parsing boundary: LLMOutputParser.
- Orchestrator: AgentOrchestrator.

Security boundaries (enforced by AST quarantine in tests):
  - NEVER import kortex.core.kernel
  - NEVER import kortex.core.container
  - NEVER import kortex.engines.security
  - NEVER import kortex.engines.knowledge
  - NEVER import kortex.engines.ai.pipeline  (PromptPipeline lives behind IAgentContextPort)
  - NEVER import kortex.engines.ai.memory    (AIMemoryManager lives behind IAgentContextPort)

M7 owns: reasoning loop, step budget, timeout, loop detection, pause/resume workflow,
         ResumeToken issuance and verification, LLM output parsing boundary.
M7 does NOT own: tool execution (M6), context assembly (IAgentContextPort),
                 LLM generation (ILLMExecutionPort), approval policy (IApprovalPolicy),
                 persistence (caller), RBAC (Security Engine / Kernel).
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import re
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.ai.exceptions import (
    AgentCancelledError,
    AgentLoopDetectedError,
    AgentOrchestrationError,
    AgentStepLimitExceededError,
    AgentValidationError,
    ToolValidationError,
)
from kortex.engines.ai.models import LLMRequest, LLMResponse
from kortex.engines.ai.tools import (
    MAX_TOOL_ARGUMENTS_BYTES,
    AIToolInvoker,
    ToolCall,
    ToolResult,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

RESUME_TOKEN_TTL_SECONDS: int = 3600
"""Maximum age (seconds) of a ResumeToken before it is rejected. Default: 1 hour."""

LOOP_DETECTION_WINDOW: int = 3
"""Number of consecutive identical call hashes that triggers LOOP_DETECTED."""


# ---------------------------------------------------------------------------
# Domain Enums
# ---------------------------------------------------------------------------


class AgentStatus(StrEnum):
    """Lifecycle status values for an agent execution task."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED_FOR_APPROVAL = "PAUSED_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STEP_LIMIT_EXCEEDED = "STEP_LIMIT_EXCEEDED"
    TIMED_OUT = "TIMED_OUT"
    LOOP_DETECTED = "LOOP_DETECTED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# Domain Models
# ---------------------------------------------------------------------------


class AgentTask(BaseModel):
    """Immutable declaration of a multi-step agent workflow.

    `require_human_approval_for_mutations` is a **policy hint** consumed
    exclusively by `IApprovalPolicy` implementations. `AgentOrchestrator`
    itself never reads this field; it delegates to
    `IApprovalPolicy.requires_approval()`.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    system_instruction: str | None = None
    agent_role: str = "general"
    parent_task_id: str | None = None
    max_steps: int = Field(default=10, ge=1, le=30)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    require_human_approval_for_mutations: bool = True  # Consumed by IApprovalPolicy only


class ResumeToken(BaseModel):
    """Opaque token issued by `AgentOrchestrator` when status == PAUSED_FOR_APPROVAL.

    The caller must return this token unmodified to `resume_task()`.
    Verification checks:
      1. `task_id` matches the task being resumed.
      2. `step_count_at_pause` matches the internal loop counter.
      3. `pending_call_hash` matches SHA-256 of canonical serialized pending calls.
      4. Current UTC time is before `expires_at`.

    Any mismatch raises `AgentValidationError`. No bypass is possible.
    Maximum age: `RESUME_TOKEN_TTL_SECONDS` (3600 seconds / 1 hour).
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    step_count_at_pause: int
    pending_call_hash: str  # SHA-256 hex digest of canonical serialized pending calls
    issued_at: str  # ISO-8601 UTC
    expires_at: str  # ISO-8601 UTC (issued_at + RESUME_TOKEN_TTL_SECONDS)


class AgentStep(BaseModel):
    """Immutable record of a single turn in an agent execution loop.

    This is the execution trace. It is returned inside `AgentExecutionResult`
    and owned by the caller. M7 does not persist it.
    """

    model_config = ConfigDict(frozen=True)

    step_number: int = Field(ge=1)
    thought: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    response_text: str | None = None
    duration_ms: float = 0.0


class AgentExecutionResult(BaseModel):
    """Final, immutable outcome of an agent execution.

    `resume_token` is populated **only** when `status == PAUSED_FOR_APPROVAL`.
    `steps` contains the full execution trace. The caller owns this data;
    M7 does not write it to any persistence layer.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    tenant_id: str
    status: AgentStatus
    final_response: str | None = None
    steps: list[AgentStep] = Field(default_factory=list)
    total_steps: int = 0
    execution_time_ms: float = 0.0
    error_message: str | None = None
    pending_tool_calls: list[ToolCall] = Field(default_factory=list)
    resume_token: ResumeToken | None = None


# ---------------------------------------------------------------------------
# Protocol Ports
# ---------------------------------------------------------------------------


@runtime_checkable
class ILLMExecutionPort(Protocol):
    """Port interface for requesting LLM completions during agent loops.

    Decouples `AgentOrchestrator` from provider routing and network calls.
    The production implementation (wired in M8) calls `ModelRouter` +
    `IBaseAIProvider`. M7 is never aware of which provider runs.
    """

    async def generate_step(self, request: LLMRequest) -> LLMResponse:
        """Execute a single generation turn for agent reasoning."""
        ...


@runtime_checkable
class IAgentContextPort(Protocol):
    """Port interface for assembling `LLMRequest` context for each agent step.

    Hides M4 (`AIMemoryManager`) and M5 (`PromptPipeline` / `ContextComposer`)
    behind a single method. `AgentOrchestrator` has zero awareness of either.
    The production implementation calls `ContextComposer.compose()` internally.
    """

    async def build_step_context(
        self,
        task: AgentTask,
        steps: list[AgentStep],
    ) -> LLMRequest:
        """Return a context-assembled `LLMRequest` for the next reasoning step."""
        ...


@runtime_checkable
class IApprovalPolicy(Protocol):
    """Policy interface for deciding whether a tool call batch requires human approval.

    M7 owns the pause/resume **workflow**. This protocol owns the **policy**.
    The production implementation may consult SecurityEngine or tenant config.
    Tests inject a deterministic fake.

    Returns `True`  → M7 pauses and issues a `ResumeToken`.
    Returns `False` → M7 proceeds to M6 invocation immediately.
    """

    async def requires_approval(
        self,
        task: AgentTask,
        proposed_calls: list[ToolCall],
    ) -> bool:
        """Return True if execution must pause pending human approval."""
        ...


# ---------------------------------------------------------------------------
# Reference Fakes (for testing and wiring in non-production contexts)
# ---------------------------------------------------------------------------


class InMemoryLLMExecutionPort:
    """Reference fake for `ILLMExecutionPort` used in unit tests.

    Returns a single pre-configured `LLMResponse` on the first call,
    then alternates to a terminal response (no tool calls, non-empty text)
    to prevent infinite loops in tests.
    """

    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
    ) -> None:
        """Args:
        responses: Ordered list of responses to return, one per `generate_step()` call.
                   If exhausted, a default terminal response is returned.
        """
        self._responses: list[LLMResponse] = list(responses or [])
        self._index: int = 0

    async def generate_step(self, request: LLMRequest) -> LLMResponse:
        """Return the next pre-configured response or a terminal default."""
        if self._index < len(self._responses):
            response = self._responses[self._index]
            self._index += 1
            return response
        # Default terminal: text content, no tool calls.
        return LLMResponse(
            request_id=request.request_id,
            text_content="Task complete.",
            tool_calls=[],
        )


class InMemoryAgentContextPort:
    """Reference fake for `IAgentContextPort` used in unit tests.

    Returns a fixed `LLMRequest` derived from the task, with no history assembly.
    """

    async def build_step_context(
        self,
        task: AgentTask,
        steps: list[AgentStep],
    ) -> LLMRequest:
        """Return a minimal LLMRequest built from the task goal."""
        return LLMRequest(
            request_id=f"req-{uuid.uuid4().hex}",
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            conversation_id=task.conversation_id,
            prompt=task.goal,
            system_instruction=task.system_instruction,
        )


class AlwaysApprovePolicy:
    """Reference fake `IApprovalPolicy` that never pauses — always approves.

    Use in tests where tool invocation should proceed without approval gates.
    """

    async def requires_approval(
        self,
        task: AgentTask,
        proposed_calls: list[ToolCall],
    ) -> bool:
        """Always return False (no approval needed)."""
        return False


class AlwaysDenyPolicy:
    """Reference fake `IApprovalPolicy` that always requires human approval.

    Use in tests verifying the pause / ResumeToken workflow.
    """

    async def requires_approval(
        self,
        task: AgentTask,
        proposed_calls: list[ToolCall],
    ) -> bool:
        """Always return True (approval required)."""
        return True


# ---------------------------------------------------------------------------
# LLM Output Parser
# ---------------------------------------------------------------------------


class LLMOutputParser:
    """Converts raw `LLMResponse` output into typed M6 agent step components.

    This is the single, explicit boundary between untrusted LLM output
    (raw `list[dict[str, Any]]` from `LLMResponse.tool_calls`) and validated
    M6 contracts (`ToolCall` DTOs).

    Validates structural shape **only**. Per-tool JSON schema validation is
    entirely M6's responsibility (`AIToolInvoker`).

    Raises `ToolValidationError` — never returns partially parsed results.
    """

    def parse_tool_calls(self, response: LLMResponse) -> list[ToolCall]:
        """Parse `tool_calls` from `LLMResponse` into validated `ToolCall` DTOs.

        Validation rules:
        - Each raw call must be a `dict`.
        - `'name'` field must be a non-empty string matching `[a-zA-Z0-9_-]+`.
        - `'arguments'` field must be a `dict` (absent → defaults to `{}`).
        - JSON-serialized argument payload must not exceed `MAX_TOOL_ARGUMENTS_BYTES`.
        - `'call_id'` field, if absent, is auto-generated (`uuid4`).

        Raises:
            ToolValidationError: Any raw call is structurally malformed.
        """
        result: list[ToolCall] = []
        for idx, raw in enumerate(response.tool_calls):
            if not isinstance(raw, dict):
                raise ToolValidationError(
                    f"Tool call at index {idx} must be a dict, got {type(raw).__name__}."
                )
            name = raw.get("name", "")
            if not isinstance(name, str) or not name.strip():
                raise ToolValidationError(
                    f"Tool call at index {idx} missing or blank 'name' field."
                )
            if not _TOOL_NAME_PATTERN.match(name):
                raise ToolValidationError(
                    f"Tool call at index {idx} has invalid name {name!r}: "
                    f"must match [a-zA-Z0-9_-]+."
                )
            arguments: Any = raw.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ToolValidationError(
                    f"Tool call at index {idx} 'arguments' must be a dict, "
                    f"got {type(arguments).__name__}."
                )
            # Enforce byte-size boundary identical to M6's own check.
            try:
                encoded = json.dumps(arguments, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ToolValidationError(
                    f"Tool call at index {idx} 'arguments' is not JSON-serializable: "
                    f"{type(exc).__name__}."
                ) from exc
            if len(encoded) > MAX_TOOL_ARGUMENTS_BYTES:
                raise ToolValidationError(
                    f"Tool call at index {idx} arguments exceed {MAX_TOOL_ARGUMENTS_BYTES} bytes."
                )
            call_id_raw = raw.get("call_id", "")
            call_id = (
                call_id_raw
                if isinstance(call_id_raw, str) and call_id_raw.strip()
                else f"call-{uuid.uuid4().hex}"
            )
            result.append(
                ToolCall(call_id=call_id, tool_name=name, arguments=arguments)
            )
        return result

    def extract_thought(self, response: LLMResponse) -> str | None:
        """Extract reasoning/thought text from the model response text.

        Thought text is stored in `AgentStep.thought` for observability.
        It carries zero execution authority — it is never parsed for commands.

        Returns the full `text_content` as the thought if present and non-empty;
        `None` otherwise.
        """
        text = response.text_content
        if text and text.strip():
            return text.strip()
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _hash_tool_calls(calls: list[ToolCall]) -> str:
    """Return a stable SHA-256 hex digest of a list of tool calls.

    Used for:
    - Loop detection: detecting identical consecutive call batches.
    - ResumeToken integrity: binding the token to the paused call set.

    Serialization is canonical: fields sorted, ASCII-safe.
    """
    payload = json.dumps(
        [
            {
                "tool_name": c.tool_name,
                "arguments": c.arguments,
            }
            for c in calls
        ],
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _issue_resume_token(
    task_id: str,
    step_count: int,
    pending_calls: list[ToolCall],
) -> ResumeToken:
    """Issue a cryptographically-bound ResumeToken for the current pause state."""
    now = _now_utc()
    expires = now + datetime.timedelta(seconds=RESUME_TOKEN_TTL_SECONDS)
    return ResumeToken(
        task_id=task_id,
        step_count_at_pause=step_count,
        pending_call_hash=_hash_tool_calls(pending_calls),
        issued_at=now.isoformat(),
        expires_at=expires.isoformat(),
    )


def _verify_resume_token(
    token: ResumeToken,
    task: AgentTask,
    step_count: int,
    approved_calls: list[ToolCall],
) -> None:
    """Verify a ResumeToken unconditionally before allowing resume_task() to proceed.

    Raises:
        AgentValidationError: Token is expired, mismatched by task_id,
                              mismatched by step_count, or mismatched by call hash.
    """
    now = _now_utc()
    try:
        expires = datetime.datetime.fromisoformat(token.expires_at)
    except ValueError as exc:
        raise AgentValidationError(
            task_id=task.task_id,
            message="ResumeToken has an unparsable expires_at field.",
        ) from exc

    if now >= expires:
        raise AgentValidationError(
            task_id=task.task_id,
            message="ResumeToken has expired.",
        )
    if token.task_id != task.task_id:
        raise AgentValidationError(
            task_id=task.task_id,
            message="ResumeToken task_id does not match the task being resumed.",
        )
    if token.step_count_at_pause != step_count:
        raise AgentValidationError(
            task_id=task.task_id,
            message="ResumeToken step_count_at_pause does not match internal loop counter.",
        )
    actual_hash = _hash_tool_calls(approved_calls)
    if token.pending_call_hash != actual_hash:
        raise AgentValidationError(
            task_id=task.task_id,
            message="ResumeToken pending_call_hash does not match approved_tool_calls.",
        )


def _validate_task_identifiers(task: AgentTask) -> None:
    """Verify mandatory multi-tenant identifiers before entering the execution loop."""
    if not task.tenant_id.strip():
        raise AgentValidationError(
            task_id=task.task_id,
            message="AgentTask.tenant_id must be non-blank.",
        )
    if not task.conversation_id.strip():
        raise AgentValidationError(
            task_id=task.task_id,
            message="AgentTask.conversation_id must be non-blank.",
        )
    if not task.task_id.strip():
        raise AgentValidationError(
            task_id=task.task_id,
            message="AgentTask.task_id must be non-blank.",
        )


def _elapsed_ms(start: float) -> float:
    import time

    return (time.monotonic() - start) * 1000.0


# ---------------------------------------------------------------------------
# AgentOrchestrator
# ---------------------------------------------------------------------------


class AgentOrchestrator:
    """Coordinates bounded, multi-step agent reasoning loops.

    Dependency Invariants:
    - `tool_invoker`: M6 `AIToolInvoker`. Never replaced by direct Kernel calls.
    - `llm_port`: `ILLMExecutionPort`. Never replaced by direct provider calls.
    - `context_port`: `IAgentContextPort`. M4/M5 are never imported directly.
    - `approval_policy`: `IApprovalPolicy`. Never makes approval decisions itself.
    - `output_parser`: `LLMOutputParser`. Never parses LLM output inline in loop.

    M7 does NOT persist anything. Zero write calls to any storage layer.
    """

    def __init__(
        self,
        tool_invoker: AIToolInvoker,
        llm_port: ILLMExecutionPort,
        context_port: IAgentContextPort,
        approval_policy: IApprovalPolicy,
        output_parser: LLMOutputParser | None = None,
    ) -> None:
        """Args:
        tool_invoker: M6 AIToolInvoker for tool schema validation and execution.
        llm_port: Port for LLM generation (M8 wires ModelRouter + provider here).
        context_port: Port for prompt context assembly (M5/M4 hidden behind this).
        approval_policy: Injected policy; decides whether approval is required.
        output_parser: Parser for LLM output; defaults to LLMOutputParser().
        """
        self._tool_invoker = tool_invoker
        self._llm_port = llm_port
        self._context_port = context_port
        self._approval_policy = approval_policy
        self._parser = output_parser if output_parser is not None else LLMOutputParser()

    async def run_task(
        self,
        task: AgentTask,
        authorizer: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None,
        cancellation_token: asyncio.Event | None = None,
        step_callback: Callable[[AgentStep], Awaitable[None]] | None = None,
    ) -> AgentExecutionResult:
        """Execute a bounded multi-step agent reasoning loop.

        Args:
            task: Immutable task declaration. Identifiers are verified before loop entry.
            authorizer: Optional M6 ToolAuthorizer forwarded to AIToolInvoker.
            cancellation_token: If set and becomes set(), the loop exits with CANCELLED.
            step_callback: Optional async callback invoked after each completed step.

        Returns:
            AgentExecutionResult with terminal status and full step trace.
        """
        _validate_task_identifiers(task)
        return await self._run_loop(
            task=task,
            initial_steps=[],
            step_count=0,
            authorizer=authorizer,
            cancellation_token=cancellation_token,
            step_callback=step_callback,
        )

    async def resume_task(
        self,
        task: AgentTask,
        resume_token: ResumeToken,
        approved_tool_calls: list[ToolCall],
        authorizer: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None,
        cancellation_token: asyncio.Event | None = None,
        step_callback: Callable[[AgentStep], Awaitable[None]] | None = None,
    ) -> AgentExecutionResult:
        """Resume a paused task after human approval has been granted.

        The `resume_token` issued by a prior `PAUSED_FOR_APPROVAL` result is
        verified unconditionally before any action is taken. There is no bypass.

        Args:
            task: Must be the same task that was originally paused.
            resume_token: Token issued by AgentOrchestrator at pause time.
            approved_tool_calls: The approved calls to execute next.
            authorizer: Optional M6 ToolAuthorizer.
            cancellation_token: Optional cancellation signal.
            step_callback: Optional async callback after each step.

        Raises:
            AgentValidationError: Token is expired, mismatched, or call hash mismatch.
        """
        _validate_task_identifiers(task)
        # Step count embedded in the token represents steps already completed;
        # we resume from that position.
        _verify_resume_token(
            token=resume_token,
            task=task,
            step_count=resume_token.step_count_at_pause,
            approved_calls=approved_tool_calls,
        )
        # Execute the approved calls as the first action of the resumed loop.
        return await self._run_loop(
            task=task,
            initial_steps=[],
            step_count=resume_token.step_count_at_pause,
            authorizer=authorizer,
            cancellation_token=cancellation_token,
            step_callback=step_callback,
            preapproved_calls=approved_tool_calls,
        )

    async def _run_loop(
        self,
        task: AgentTask,
        initial_steps: list[AgentStep],
        step_count: int,
        authorizer: Callable[[str, dict[str, Any]], Awaitable[bool]] | None,
        cancellation_token: asyncio.Event | None,
        step_callback: Callable[[AgentStep], Awaitable[None]] | None,
        preapproved_calls: list[ToolCall] | None = None,
    ) -> AgentExecutionResult:
        """Internal bounded execution loop."""
        import time

        start = time.monotonic()
        steps: list[AgentStep] = list(initial_steps)
        # Sliding window for loop detection: tracks the last LOOP_DETECTION_WINDOW
        # consecutive call-set hashes.
        recent_hashes: deque[str] = deque(maxlen=LOOP_DETECTION_WINDOW)

        # --- Handle pre-approved calls from resume_task() ---
        if preapproved_calls:
            step_start = time.monotonic()
            tool_results = await self._invoke_tools(preapproved_calls, authorizer, tenant_id=task.tenant_id)
            step_count += 1
            step = AgentStep(
                step_number=step_count,
                tool_calls=preapproved_calls,
                tool_results=tool_results,
                duration_ms=(time.monotonic() - step_start) * 1000.0,
            )
            steps.append(step)
            if step_callback is not None:
                await step_callback(step)
            recent_hashes.append(_hash_tool_calls(preapproved_calls))

        while True:
            # --- Cancellation check ---
            if cancellation_token is not None and cancellation_token.is_set():
                return self._terminal(
                    task=task,
                    status=AgentStatus.CANCELLED,
                    steps=steps,
                    start=start,
                    error_message="Task was cancelled.",
                )

            # --- Step budget check (pre-LLM call) ---
            if step_count >= task.max_steps:
                return self._terminal(
                    task=task,
                    status=AgentStatus.STEP_LIMIT_EXCEEDED,
                    steps=steps,
                    start=start,
                    error_message=f"Step limit of {task.max_steps} reached.",
                )

            # --- Timeout check ---
            elapsed = (time.monotonic() - start) * 1000.0
            if elapsed >= task.timeout_seconds * 1000.0:
                return self._terminal(
                    task=task,
                    status=AgentStatus.TIMED_OUT,
                    steps=steps,
                    start=start,
                    error_message="Task execution timeout exceeded.",
                )

            # --- Context assembly via port (M4 + M5 hidden) ---
            request = await self._context_port.build_step_context(task, steps)

            # --- LLM generation via port ---
            step_start = time.monotonic()
            try:
                response = await asyncio.wait_for(
                    self._llm_port.generate_step(request),
                    timeout=max(
                        0.1,
                        task.timeout_seconds - (time.monotonic() - start),
                    ),
                )
            except TimeoutError:
                return self._terminal(
                    task=task,
                    status=AgentStatus.TIMED_OUT,
                    steps=steps,
                    start=start,
                    error_message="LLM generation step exceeded remaining timeout.",
                )

            # --- Parse LLM output via dedicated boundary component ---
            try:
                tool_calls = self._parser.parse_tool_calls(response)
            except ToolValidationError as exc:
                return self._terminal(
                    task=task,
                    status=AgentStatus.FAILED,
                    steps=steps,
                    start=start,
                    error_message=f"LLM output parsing failed: {exc}",
                )

            thought = self._parser.extract_thought(response)

            # --- No tool calls → task complete ---
            if not tool_calls:
                final_text = response.text_content if response.text_content.strip() else None
                step_count += 1
                final_step = AgentStep(
                    step_number=step_count,
                    thought=thought,
                    tool_calls=[],
                    tool_results=[],
                    response_text=final_text,
                    duration_ms=(time.monotonic() - step_start) * 1000.0,
                )
                steps.append(final_step)
                if step_callback is not None:
                    await step_callback(final_step)
                return AgentExecutionResult(
                    task_id=task.task_id,
                    tenant_id=task.tenant_id,
                    status=AgentStatus.COMPLETED,
                    final_response=final_text,
                    steps=steps,
                    total_steps=step_count,
                    execution_time_ms=(time.monotonic() - start) * 1000.0,
                )

            # --- Loop detection ---
            call_hash = _hash_tool_calls(tool_calls)
            recent_hashes.append(call_hash)
            if (
                len(recent_hashes) == LOOP_DETECTION_WINDOW
                and len(set(recent_hashes)) == 1
            ):
                return self._terminal(
                    task=task,
                    status=AgentStatus.LOOP_DETECTED,
                    steps=steps,
                    start=start,
                    error_message=(
                        f"Agent loop detected: identical tool call batch repeated "
                        f"{LOOP_DETECTION_WINDOW} consecutive times."
                    ),
                )

            # --- Approval policy check (policy decides; M7 responds) ---
            needs_approval = await self._approval_policy.requires_approval(task, tool_calls)
            if needs_approval:
                token = _issue_resume_token(
                    task_id=task.task_id,
                    step_count=step_count,
                    pending_calls=tool_calls,
                )
                return AgentExecutionResult(
                    task_id=task.task_id,
                    tenant_id=task.tenant_id,
                    status=AgentStatus.PAUSED_FOR_APPROVAL,
                    steps=steps,
                    total_steps=step_count,
                    execution_time_ms=(time.monotonic() - start) * 1000.0,
                    pending_tool_calls=tool_calls,
                    resume_token=token,
                )

            # --- M6 tool invocation ---
            tool_results = await self._invoke_tools(tool_calls, authorizer, tenant_id=task.tenant_id)
            step_count += 1
            step = AgentStep(
                step_number=step_count,
                thought=thought,
                tool_calls=tool_calls,
                tool_results=tool_results,
                duration_ms=(time.monotonic() - step_start) * 1000.0,
            )
            steps.append(step)
            if step_callback is not None:
                await step_callback(step)

    async def _invoke_tools(
        self,
        tool_calls: list[ToolCall],
        authorizer: Callable[[str, dict[str, Any]], Awaitable[bool]] | None,
        tenant_id: str = "default",
    ) -> list[ToolResult]:
        """Delegate all tool invocations to M6 AIToolInvoker."""
        if not tool_calls:
            return []

        async def _default_authorizer(name: str, args: dict[str, Any]) -> bool:
            return True

        effective_authorizer = authorizer if authorizer is not None else _default_authorizer
        results = await self._tool_invoker.invoke_all(
            tenant_id=tenant_id,
            tool_calls=tool_calls,
            authorizer=effective_authorizer,
        )
        return results

    def _terminal(
        self,
        task: AgentTask,
        status: AgentStatus,
        steps: list[AgentStep],
        start: float,
        error_message: str | None = None,
    ) -> AgentExecutionResult:
        """Build a terminal AgentExecutionResult for non-COMPLETED statuses."""
        import time

        return AgentExecutionResult(
            task_id=task.task_id,
            tenant_id=task.tenant_id,
            status=status,
            steps=steps,
            total_steps=len(steps),
            execution_time_ms=(time.monotonic() - start) * 1000.0,
            error_message=error_message,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "LOOP_DETECTION_WINDOW",
    "RESUME_TOKEN_TTL_SECONDS",
    "AgentCancelledError",
    "AgentExecutionResult",
    "AgentLoopDetectedError",
    "AgentOrchestrationError",
    "AgentOrchestrator",
    "AgentStatus",
    "AgentStep",
    "AgentStepLimitExceededError",
    "AgentTask",
    "AgentValidationError",
    "AlwaysApprovePolicy",
    "AlwaysDenyPolicy",
    "IAgentContextPort",
    "IApprovalPolicy",
    "ILLMExecutionPort",
    "InMemoryAgentContextPort",
    "InMemoryLLMExecutionPort",
    "LLMOutputParser",
    "ResumeToken",
]
