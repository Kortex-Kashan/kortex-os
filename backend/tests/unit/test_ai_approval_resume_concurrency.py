"""M7.6-W1 adversarial coverage: tenant-concurrency enforcement on the
AI approval-resume path.

Root cause (M7.6 planning report §8, verified by direct source read before
this fix): `AIOrchestrationEngine._on_approval_decided` -- the *only* path
that resumes an approved AI-originated mutation in production (a human
decision always arrives asynchronously, via the durable
`workflow.approval.decided` event, never through the synchronous
`resume_agent` API) -- called `AgentOrchestrator.resume_task(...)` directly,
without ever acquiring a `TenantConcurrencyThrottler` agent slot, unlike
both synchronous entry points (`orchestrate_agent`, `resume_agent`), which
already wrap their own resume/run calls in `self._throttler.
acquire_agent_slot(...)`. Every mutating AI tool built so far (Connector's
`connector_send_action`, Document's `document_generate`) therefore had its
approval-resume traffic bypass the per-tenant concurrent-agent-workflow cap
the rest of the control plane enforces.

This file proves the fix's invariant directly, using a minimal, fully
controlled fake `AgentOrchestrator` (duck-typed: `get_task`/`cancel_task`/
`resume_task`) and a spy wrapping the real, unmodified
`TenantConcurrencyThrottler` -- not a second throttling mechanism, the same
one `AIOrchestrationEngine` already owns and the same one `orchestrate_agent`/
`resume_agent` already use. This keeps each test fast, deterministic, and
narrowly focused on the concurrency-control invariant itself, rather than
re-proving business logic (tool dispatch, template binding, etc.) the
existing M7.3-M7.5 vertical-slice tests already cover.

Invariant under test: one resumed execution -> exactly one throttle
acquisition -> exactly one release, regardless of success, failure, or
rejection; a saturated tenant defers (never crashes, never double-executes,
never blocks another tenant); duplicate approval-decided event delivery
remains idempotent (proven pre-existing, reconfirmed unbroken by this fix).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from kortex.engines.ai.agent import (
    AgentExecutionResult,
    AgentStatus,
    AgentTask,
    PersistedAgentTaskRecord,
    ResumeToken,
    ToolCall,
)
from kortex.engines.ai.engine import AIOrchestrationEngine
from kortex.engines.ai.exceptions import TenantQuotaExceededError
from kortex.engines.ai.throttling import TenantConcurrencyThrottler

_TENANT_A = "tenant-approval-resume-a"
_TENANT_B = "tenant-approval-resume-b"


class _SpyThrottler(TenantConcurrencyThrottler):
    """Wraps the real, unmodified `TenantConcurrencyThrottler` to record
    acquire/release call order and count -- the "controlled fake" the M7.6
    master prompt explicitly sanctions for proving no double acquisition,
    without inventing a second throttling mechanism."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.acquire_calls: list[str] = []
        self.release_calls: list[str] = []

    @asynccontextmanager
    async def acquire_agent_slot(self, tenant_id: str) -> AsyncIterator[None]:
        self.acquire_calls.append(tenant_id)
        # `async with super()...` raises directly out of this generator if
        # the underlying acquire fails (tenant at capacity) -- the `try`
        # below is nested *inside* it deliberately, so release_calls only
        # ever records an actual release of a slot that was really
        # acquired, never a failed-acquire exit.
        async with super().acquire_agent_slot(tenant_id):
            try:
                yield
            finally:
                self.release_calls.append(tenant_id)


class _FakeAgentOrchestrator:
    """A minimal, fully controlled stand-in for `AgentOrchestrator`, giving
    the test complete control over `resume_task`'s outcome and letting the
    concurrency-throttle wrapping in `_on_approval_decided` be proven in
    isolation from the (already separately tested, M7.3-M7.5) real tool
    dispatch/adapter machinery.

    Maintains its own tiny in-memory task-status map so the idempotency
    check `_on_approval_decided` performs (`record.status !=
    PAUSED_FOR_APPROVAL`) behaves exactly as it would against a real store
    across repeated event deliveries."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], PersistedAgentTaskRecord] = {}
        self.resume_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.resume_result: AgentExecutionResult | None = None
        self.resume_exception: Exception | None = None

    def seed_paused(self, task: AgentTask, pending_tool_calls: list[ToolCall] | None = None) -> None:
        self._records[(task.tenant_id, task.task_id)] = PersistedAgentTaskRecord(
            task=task,
            status=AgentStatus.PAUSED_FOR_APPROVAL,
            current_step=1,
            steps=[],
            pending_tool_calls=pending_tool_calls or [],
            resume_token=ResumeToken(
                task_id=task.task_id,
                step_count_at_pause=1,
                pending_call_hash="dummy",
                issued_at="2026-01-01T00:00:00Z",
                expires_at="2026-01-01T01:00:00Z",
            ),
            version=1,
        )

    async def get_task(self, task_id: str, tenant_id: str) -> PersistedAgentTaskRecord | None:
        return self._records.get((tenant_id, task_id))

    async def cancel_task(self, task_id: str, tenant_id: str) -> bool:
        self.cancel_calls.append(task_id)
        record = self._records.get((tenant_id, task_id))
        if record is None:
            return False
        self._records[(tenant_id, task_id)] = record.model_copy(update={"status": AgentStatus.CANCELLED})
        return True

    async def resume_task(
        self,
        task: AgentTask,
        resume_token: ResumeToken,
        approved_tool_calls: list[ToolCall],
        authorizer: object | None = None,
    ) -> AgentExecutionResult:
        self.resume_calls.append(task.task_id)
        if self.resume_exception is not None:
            raise self.resume_exception
        record = self._records.get((task.tenant_id, task.task_id))
        if record is not None:
            self._records[(task.tenant_id, task.task_id)] = record.model_copy(update={"status": AgentStatus.COMPLETED})
        assert self.resume_result is not None
        return self.resume_result


def _task(tenant_id: str, task_id: str) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        tenant_id=tenant_id,
        user_id="user-1",
        conversation_id=f"conv-{task_id}",
        goal="Do the approved mutation",
    )


def _completed_result(task: AgentTask) -> AgentExecutionResult:
    return AgentExecutionResult(
        task_id=task.task_id,
        tenant_id=task.tenant_id,
        status=AgentStatus.COMPLETED,
        final_response="done",
        total_steps=1,
    )


def _approved_event(task_id: str, tenant_id: str) -> SimpleNamespace:
    """A crafted `workflow.approval.decided` event, matching exactly the
    shape `_on_approval_decided` reads -- `action_fingerprint` deliberately
    omitted (the handler's own check is a no-op when falsy, per
    `_action_fingerprint`'s call site)."""
    return SimpleNamespace(
        payload={
            "context_snapshot": {"action": "ai_tool_invocation", "task_id": task_id},
            "tenant_id": tenant_id,
            "decision": "APPROVED",
        }
    )


def _rejected_event(task_id: str, tenant_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        payload={
            "context_snapshot": {"action": "ai_tool_invocation", "task_id": task_id},
            "tenant_id": tenant_id,
            "decision": "REJECTED",
        }
    )


# -- Test A / C: approval resume acquires and releases the tenant slot ------


@pytest.mark.asyncio
async def test_approval_resume_acquires_and_releases_tenant_slot() -> None:
    throttler = _SpyThrottler()
    fake_orchestrator = _FakeAgentOrchestrator()
    engine = AIOrchestrationEngine(throttler=throttler, agent_orchestrator=fake_orchestrator)

    task = _task(_TENANT_A, "task-a-1")
    fake_orchestrator.seed_paused(task)
    fake_orchestrator.resume_result = _completed_result(task)

    await engine._on_approval_decided(_approved_event(task.task_id, _TENANT_A))

    assert fake_orchestrator.resume_calls == [task.task_id]
    assert throttler.acquire_calls == [_TENANT_A]
    assert throttler.release_calls == [_TENANT_A]
    assert throttler.get_active_agents(_TENANT_A) == 0


# -- Test D: release on failure ----------------------------------------------


@pytest.mark.asyncio
async def test_slot_is_released_even_when_resume_fails() -> None:
    throttler = _SpyThrottler()
    fake_orchestrator = _FakeAgentOrchestrator()
    fake_orchestrator.resume_exception = RuntimeError("simulated resume failure")
    engine = AIOrchestrationEngine(throttler=throttler, agent_orchestrator=fake_orchestrator)

    task = _task(_TENANT_A, "task-a-2")
    fake_orchestrator.seed_paused(task)

    # _on_approval_decided fails closed generically -- it must not raise.
    await engine._on_approval_decided(_approved_event(task.task_id, _TENANT_A))

    assert fake_orchestrator.resume_calls == [task.task_id]
    assert throttler.acquire_calls == [_TENANT_A]
    assert throttler.release_calls == [_TENANT_A]
    assert throttler.get_active_agents(_TENANT_A) == 0


# -- Test E: no double acquisition -------------------------------------------


@pytest.mark.asyncio
async def test_one_resume_acquires_exactly_once() -> None:
    throttler = _SpyThrottler()
    fake_orchestrator = _FakeAgentOrchestrator()
    engine = AIOrchestrationEngine(throttler=throttler, agent_orchestrator=fake_orchestrator)

    task = _task(_TENANT_A, "task-a-3")
    fake_orchestrator.seed_paused(task)
    fake_orchestrator.resume_result = _completed_result(task)

    await engine._on_approval_decided(_approved_event(task.task_id, _TENANT_A))

    assert len(throttler.acquire_calls) == 1
    assert len(throttler.release_calls) == 1


# -- Test B: tenant limit is enforced ----------------------------------------


@pytest.mark.asyncio
async def test_saturated_tenant_defers_resume_without_executing_or_double_acquiring() -> None:
    throttler = _SpyThrottler(max_concurrent_agents=1)
    fake_orchestrator = _FakeAgentOrchestrator()
    engine = AIOrchestrationEngine(throttler=throttler, agent_orchestrator=fake_orchestrator)

    task = _task(_TENANT_A, "task-a-4")
    fake_orchestrator.seed_paused(task)
    fake_orchestrator.resume_result = _completed_result(task)

    # Saturate tenant A's one available agent slot before the approval
    # decision arrives (simulating another in-flight agent execution).
    async with throttler.acquire_agent_slot(_TENANT_A):
        await engine._on_approval_decided(_approved_event(task.task_id, _TENANT_A))

        # The deferral must be closed -- never a crash, never a fabricated
        # success, never a double execution.
        assert fake_orchestrator.resume_calls == []
        assert fake_orchestrator.cancel_calls == []
        record = await fake_orchestrator.get_task(task.task_id, _TENANT_A)
        assert record is not None
        assert record.status == AgentStatus.PAUSED_FOR_APPROVAL

        # One rejected acquisition attempt, no matching release (the
        # attempt never entered the protected body).
        assert throttler.acquire_calls.count(_TENANT_A) == 2  # the held slot + the rejected attempt
        assert throttler.release_calls.count(_TENANT_A) == 0

    # Once the pre-existing slot is released, a later redelivery of the
    # exact same event can still resume the task -- proving the deferral
    # didn't strand it.
    await engine._on_approval_decided(_approved_event(task.task_id, _TENANT_A))
    assert fake_orchestrator.resume_calls == [task.task_id]


@pytest.mark.asyncio
async def test_acquire_agent_slot_raises_tenant_quota_exceeded_when_saturated() -> None:
    """Direct proof of the exact exception `_on_approval_decided` catches --
    guards against the fix silently catching the wrong exception type."""
    throttler = TenantConcurrencyThrottler(max_concurrent_agents=1)
    async with throttler.acquire_agent_slot(_TENANT_A):
        with pytest.raises(TenantQuotaExceededError):
            async with throttler.acquire_agent_slot(_TENANT_A):
                pass


# -- Test F: cross-tenant independence ---------------------------------------


@pytest.mark.asyncio
async def test_saturated_tenant_does_not_block_another_tenant() -> None:
    throttler = _SpyThrottler(max_concurrent_agents=1)
    fake_orchestrator = _FakeAgentOrchestrator()
    engine = AIOrchestrationEngine(throttler=throttler, agent_orchestrator=fake_orchestrator)

    task_b = _task(_TENANT_B, "task-b-1")
    fake_orchestrator.seed_paused(task_b)
    fake_orchestrator.resume_result = _completed_result(task_b)

    async with throttler.acquire_agent_slot(_TENANT_A):
        await engine._on_approval_decided(_approved_event(task_b.task_id, _TENANT_B))

    assert fake_orchestrator.resume_calls == [task_b.task_id]
    assert throttler.get_active_agents(_TENANT_B) == 0


# -- Test G: approval rejection performs zero execution and zero acquisition -


@pytest.mark.asyncio
async def test_rejected_approval_never_acquires_a_slot_or_resumes() -> None:
    throttler = _SpyThrottler()
    fake_orchestrator = _FakeAgentOrchestrator()
    engine = AIOrchestrationEngine(throttler=throttler, agent_orchestrator=fake_orchestrator)

    task = _task(_TENANT_A, "task-a-5")
    fake_orchestrator.seed_paused(task)

    await engine._on_approval_decided(_rejected_event(task.task_id, _TENANT_A))

    assert fake_orchestrator.resume_calls == []
    assert fake_orchestrator.cancel_calls == [task.task_id]
    assert throttler.acquire_calls == []
    assert throttler.release_calls == []


# -- §6: idempotency must remain intact --------------------------------------


@pytest.mark.asyncio
async def test_duplicate_approval_decided_event_does_not_double_execute_or_double_acquire() -> None:
    """Reconfirms the pre-existing, engine-agnostic idempotency guarantee
    (M7.3/M7.4/M7.5's own proofs: a no-op once the task is no longer
    PAUSED_FOR_APPROVAL) still holds with the M7.6-W1 throttle wrapping in
    place -- the second delivery must not resume, cancel, or acquire again."""
    throttler = _SpyThrottler()
    fake_orchestrator = _FakeAgentOrchestrator()
    engine = AIOrchestrationEngine(throttler=throttler, agent_orchestrator=fake_orchestrator)

    task = _task(_TENANT_A, "task-a-6")
    fake_orchestrator.seed_paused(task)
    fake_orchestrator.resume_result = _completed_result(task)

    event = _approved_event(task.task_id, _TENANT_A)
    await engine._on_approval_decided(event)
    await engine._on_approval_decided(event)  # duplicate delivery

    assert fake_orchestrator.resume_calls == [task.task_id]  # exactly once
    assert throttler.acquire_calls == [_TENANT_A]  # exactly once
    assert throttler.release_calls == [_TENANT_A]  # exactly once
