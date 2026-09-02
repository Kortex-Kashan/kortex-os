"""Milestone 11 unit and adversarial test suite — Durable Task Persistence and Concurrency.

Tests:
1. AIAgentTaskRow relational persistence and schema validation.
2. StorageAgentTaskStore CRUD operations and parity with InMemoryAgentTaskStore.
3. Concurrency safety: atomic CAS claim_task_for_resumption under racing workers.
4. Illegal state transition rejections (COMPLETED -> RESUMING, FAILED -> RESUMING).
5. Strict multi-tenant isolation.
6. Conversation history offset windowing / pagination parity.
"""

from __future__ import annotations

import asyncio
import datetime
import pathlib
import uuid
from collections.abc import AsyncIterator

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.engines.ai.agent import (
    AgentStatus,
    AgentStep,
    AgentTask,
    IAgentTaskStore,
    InMemoryAgentTaskStore,
    PersistedAgentTaskRecord,
    ResumeToken,
    ToolCall,
)
from kortex.engines.ai.exceptions import (
    AgentNotFoundError,
    AgentStateConflictError,
)
from kortex.engines.ai.memory import AIMemoryManager, InMemoryConversationStore
from kortex.engines.ai.models import LLMRequest, LLMResponse
from kortex.engines.ai.persistence import (
    StorageAgentTaskStore,
    StorageConversationStore,
)
from kortex.engines.storage.stores.data_store import RelationalDataStore

TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"
CONVERSATION_ID = "conv-100"


@pytest.fixture
async def data_store(tmp_path: pathlib.Path) -> AsyncIterator[RelationalDataStore]:
    """An isolated, real SQLite IDataStore scoped to this test only."""
    db_path = (tmp_path / "m11_persistence.db").as_posix()
    manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await manager.connect()
    await manager.create_all_tables()
    try:
        yield RelationalDataStore(manager)
    finally:
        await manager.disconnect()


@pytest.fixture
def storage_task_store(data_store: RelationalDataStore) -> StorageAgentTaskStore:
    return StorageAgentTaskStore(data_store)


@pytest.fixture
def in_memory_task_store() -> InMemoryAgentTaskStore:
    return InMemoryAgentTaskStore()


def _make_task(task_id: str | None = None, tenant_id: str = TENANT_A) -> AgentTask:
    return AgentTask(
        task_id=task_id or f"task-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        user_id="user-1",
        conversation_id=CONVERSATION_ID,
        goal="Process payroll report",
        max_steps=5,
        timeout_seconds=30.0,
    )


def _make_record(
    task: AgentTask,
    status: AgentStatus = AgentStatus.RUNNING,
    version: int = 1,
) -> PersistedAgentTaskRecord:
    return PersistedAgentTaskRecord(
        task=task,
        status=status,
        current_step=0,
        steps=[],
        pending_tool_calls=[],
        resume_token=None,
        version=version,
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )


async def _run_crud_lifecycle(store: IAgentTaskStore) -> None:
    task = _make_task()
    record = _make_record(task)

    # 1. Save
    await store.save_task(record)

    # 2. Get
    loaded = await store.get_task(task.task_id, task.tenant_id)
    assert loaded is not None
    assert loaded.task.task_id == task.task_id
    assert loaded.task.tenant_id == task.tenant_id
    assert loaded.status == AgentStatus.RUNNING
    assert loaded.version == 1

    # 3. Update to PAUSED_FOR_APPROVAL
    step = AgentStep(step_number=1, thought="Thinking", duration_ms=10.0)
    pending_call = ToolCall(call_id="call-1", tool_name="run_payroll", arguments={"month": "May"})
    token = ResumeToken(
        task_id=task.task_id,
        step_count_at_pause=1,
        pending_call_hash="fakehash",
        issued_at=datetime.datetime.now(datetime.UTC).isoformat(),
        expires_at=(datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)).isoformat(),
        signature="fakesig",
    )
    paused_record = record.model_copy(
        update={
            "status": AgentStatus.PAUSED_FOR_APPROVAL,
            "current_step": 1,
            "steps": [step],
            "pending_tool_calls": [pending_call],
            "resume_token": token,
            "version": 2,
        }
    )
    await store.update_task(paused_record)

    loaded_paused = await store.get_task(task.task_id, task.tenant_id)
    assert loaded_paused is not None
    assert loaded_paused.status == AgentStatus.PAUSED_FOR_APPROVAL
    assert loaded_paused.version == 2
    assert len(loaded_paused.steps) == 1
    assert loaded_paused.steps[0].thought == "Thinking"
    assert len(loaded_paused.pending_tool_calls) == 1
    assert loaded_paused.pending_tool_calls[0].tool_name == "run_payroll"
    assert loaded_paused.resume_token is not None
    assert loaded_paused.resume_token.signature == "fakesig"


# ---------------------------------------------------------------------------
# Storage & InMemory Task Store CRUD and Parity Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_store_crud_lifecycle_storage(storage_task_store: StorageAgentTaskStore) -> None:
    await _run_crud_lifecycle(storage_task_store)


@pytest.mark.asyncio
async def test_task_store_crud_lifecycle_in_memory(in_memory_task_store: InMemoryAgentTaskStore) -> None:
    await _run_crud_lifecycle(in_memory_task_store)


async def _run_tenant_isolation(store: IAgentTaskStore) -> None:
    task_a = _make_task(tenant_id=TENANT_A)
    record_a = _make_record(task_a)
    await store.save_task(record_a)

    # Attempt to read Tenant A's task using Tenant B's ID
    loaded = await store.get_task(task_a.task_id, TENANT_B)
    assert loaded is None

    # List tasks for Tenant B should return empty
    list_b = await store.list_tasks(TENANT_B)
    assert len(list_b) == 0

    list_a = await store.list_tasks(TENANT_A)
    assert len(list_a) == 1
    assert list_a[0].task.task_id == task_a.task_id


@pytest.mark.asyncio
async def test_task_store_strict_tenant_isolation_storage(storage_task_store: StorageAgentTaskStore) -> None:
    await _run_tenant_isolation(storage_task_store)


@pytest.mark.asyncio
async def test_task_store_strict_tenant_isolation_in_memory(in_memory_task_store: InMemoryAgentTaskStore) -> None:
    await _run_tenant_isolation(in_memory_task_store)


# ---------------------------------------------------------------------------
# Concurrency & Atomic CAS Claim Tests
# ---------------------------------------------------------------------------


async def _run_atomic_cas_claim(store: IAgentTaskStore) -> None:
    task = _make_task()
    record = _make_record(task, status=AgentStatus.PAUSED_FOR_APPROVAL, version=3)
    await store.save_task(record)

    # Successful atomic claim
    claimed = await store.claim_task_for_resumption(task.task_id, task.tenant_id, expected_version=3)
    assert claimed.status == AgentStatus.RESUMING
    assert claimed.version == 4

    # Second claim on the same task fails with state conflict error because it is now RESUMING
    with pytest.raises(AgentStateConflictError):
        await store.claim_task_for_resumption(task.task_id, task.tenant_id, expected_version=3)


@pytest.mark.asyncio
async def test_atomic_cas_claim_for_resumption_storage(storage_task_store: StorageAgentTaskStore) -> None:
    await _run_atomic_cas_claim(storage_task_store)


@pytest.mark.asyncio
async def test_atomic_cas_claim_for_resumption_in_memory(in_memory_task_store: InMemoryAgentTaskStore) -> None:
    await _run_atomic_cas_claim(in_memory_task_store)


@pytest.mark.asyncio
async def test_concurrent_racing_resumption_claims(
    storage_task_store: StorageAgentTaskStore,
) -> None:
    """Simulate two workers simultaneously attempting to resume the same paused task."""
    task = _make_task()
    record = _make_record(task, status=AgentStatus.PAUSED_FOR_APPROVAL, version=1)
    await storage_task_store.save_task(record)

    async def _worker_claim() -> tuple[str, PersistedAgentTaskRecord | None, Exception | None]:
        try:
            res = await storage_task_store.claim_task_for_resumption(task.task_id, task.tenant_id, expected_version=1)
            return ("WON", res, None)
        except Exception as exc:
            return ("LOST", None, exc)

    results = await asyncio.gather(_worker_claim(), _worker_claim())
    winners = [r for r in results if r[0] == "WON"]
    losers = [r for r in results if r[0] == "LOST"]

    assert len(winners) == 1, "Exactly one worker must succeed in claiming the task"
    assert len(losers) == 1, "Exactly one worker must fail with a conflict exception"
    assert isinstance(losers[0][2], AgentStateConflictError)
    assert winners[0][1] is not None
    assert winners[0][1].status == AgentStatus.RESUMING
    assert winners[0][1].version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_status",
    [
        AgentStatus.RUNNING,
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
        AgentStatus.TIMED_OUT,
    ],
)
async def test_claim_rejects_non_paused_states(
    storage_task_store: StorageAgentTaskStore,
    invalid_status: AgentStatus,
) -> None:
    task = _make_task()
    record = _make_record(task, status=invalid_status, version=1)
    await storage_task_store.save_task(record)

    with pytest.raises(AgentStateConflictError, match="cannot be resumed"):
        await storage_task_store.claim_task_for_resumption(task.task_id, task.tenant_id, expected_version=1)


@pytest.mark.asyncio
async def test_claim_nonexistent_task_raises_not_found(
    storage_task_store: StorageAgentTaskStore,
) -> None:
    with pytest.raises(AgentNotFoundError):
        await storage_task_store.claim_task_for_resumption("non-existent-task", TENANT_A, expected_version=1)


# ---------------------------------------------------------------------------
# Conversation History Windowing / Pagination Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_history_offset_pagination_storage(data_store: RelationalDataStore) -> None:
    store = StorageConversationStore(data_store)
    manager = AIMemoryManager(store, max_history_turns=5)

    for i in range(1, 11):
        req = LLMRequest(
            request_id=f"req-{i}",
            tenant_id=TENANT_A,
            user_id="user-1",
            conversation_id=CONVERSATION_ID,
            prompt=f"user turn {i}",
        )
        resp = LLMResponse(request_id=f"req-{i}", text_content=f"assistant turn {i}")
        await manager.append_history(TENANT_A, CONVERSATION_ID, req, resp)

    # Page 1
    page1 = await manager.get_turns(TENANT_A, CONVERSATION_ID, offset=0)
    assert len(page1) == 5
    assert [t.sequence for t in page1] == [6, 7, 8, 9, 10]

    # Page 2
    page2 = await manager.get_turns(TENANT_A, CONVERSATION_ID, offset=5)
    assert len(page2) == 5
    assert [t.sequence for t in page2] == [1, 2, 3, 4, 5]

    # Page 3
    page3 = await manager.get_turns(TENANT_A, CONVERSATION_ID, offset=10)
    assert len(page3) == 0


@pytest.mark.asyncio
async def test_conversation_history_offset_pagination_in_memory() -> None:
    store = InMemoryConversationStore()
    manager = AIMemoryManager(store, max_history_turns=5)

    for i in range(1, 11):
        req = LLMRequest(
            request_id=f"req-{i}",
            tenant_id=TENANT_A,
            user_id="user-1",
            conversation_id=CONVERSATION_ID,
            prompt=f"user turn {i}",
        )
        resp = LLMResponse(request_id=f"req-{i}", text_content=f"assistant turn {i}")
        await manager.append_history(TENANT_A, CONVERSATION_ID, req, resp)

    # Page 1
    page1 = await manager.get_turns(TENANT_A, CONVERSATION_ID, offset=0)
    assert len(page1) == 5
    assert [t.sequence for t in page1] == [6, 7, 8, 9, 10]

    # Page 2
    page2 = await manager.get_turns(TENANT_A, CONVERSATION_ID, offset=5)
    assert len(page2) == 5
    assert [t.sequence for t in page2] == [1, 2, 3, 4, 5]

    # Page 3
    page3 = await manager.get_turns(TENANT_A, CONVERSATION_ID, offset=10)
    assert len(page3) == 0
