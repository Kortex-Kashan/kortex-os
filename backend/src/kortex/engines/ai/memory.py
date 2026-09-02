"""Conversation memory for the KORTEX OS AI Orchestration Engine.

Implements `AIMemoryManager` (satisfying the frozen `IAIMemoryManager`
Protocol), the `IConversationStore` port it reads and writes through, and a
non-durable in-memory reference store. Governed by
`docs/architecture/ai_engine_m4_context_memory_spec.md`.

Conversation history is keyed by `(tenant_id, conversation_id)` and stores
no provider or model identifier anywhere — that is the mechanism by which
replacing one model with another leaves history intact and retrievable.

This module performs no I/O of its own, imports no infrastructure, and
never builds an `LLMRequest`. Durable storage lives behind the port in
`persistence.py`, the single module permitted to touch infrastructure;
prompt assembly and knowledge retrieval are a later milestone's concern.
"""

from __future__ import annotations

import asyncio
import datetime
import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from kortex.engines.ai.exceptions import MemoryValidationError
from kortex.engines.ai.models import LLMRequest, LLMResponse

_MAX_HISTORY_TURNS_CEILING = 200
"""Upper clamp on retained turns, mirroring the `audit.py` limit convention."""

USER_MARKER = "[[user]]"
ASSISTANT_MARKER = "[[assistant]]"

_MARKER_SENTINEL = "[["
_MARKER_SENTINEL_NEUTRALIZED = "[ ["

_DELIMITER_VARIANT_PATTERN: re.Pattern[str] = re.compile(
    r"(?:\[|\\\[|［)\s*(?:\[|\\\[|［)\s*(system|assistant|user|tool|knowledge|context_documents)\s*(?:\]|\\\]|］)\s*(?:\]|\\\]|］)",  # noqa: RUF001
    re.IGNORECASE,
)


def sanitize_context_content(content: str) -> str:
    """Neutralize role-marker sentinels so input cannot forge a context boundary.

    Applied at render time, never at write time.
    """
    if not content:
        return content

    def _neutralize_match(match: re.Match[str]) -> str:
        tag = match.group(1).lower()
        return f"[ [{tag}]]"

    sanitized = _DELIMITER_VARIANT_PATTERN.sub(_neutralize_match, content)
    sanitized = sanitized.replace(_MARKER_SENTINEL, _MARKER_SENTINEL_NEUTRALIZED)
    sanitized = sanitized.replace("［［", "［ ［")  # noqa: RUF001
    sanitized = sanitized.replace(r"\[\[", r"\[ \[")
    return sanitized


def require_identifier(value: str | None, field_name: str) -> str:
    """Reject a blank or whitespace-only identifier before any query runs.

    Public for the same reason `sanitize_context_content` is public: it is
    the single implementation of this guard for the whole engine.
    Milestone 5's `ContextComposer` reuses it to validate identifiers
    *before* reaching the knowledge port, not just before the memory
    store — an untrusted, unvalidated tenant_id must never cross any
    retrieval boundary, not only this module's own.
    """
    if not value or not value.strip():
        raise MemoryValidationError(f"{field_name} must not be empty or whitespace-only.")
    return value


class ConversationTurn(BaseModel):
    """One completed (user, assistant) exchange.

    Turn-grained rather than message-grained because the frozen
    `append_history(request, response)` signature can only ever deliver one
    request and one response — a dangling half-turn is not expressible,
    which makes the user/assistant alternation structural rather than
    merely enforced.
    """

    model_config = ConfigDict(frozen=True)

    sequence: int
    user_content: str
    assistant_content: str
    request_id: str
    user_id: str
    created_at: datetime.datetime


@runtime_checkable
class IConversationStore(Protocol):
    """Durable-storage port for conversation history.

    `AIMemoryManager` reads and writes exclusively through this port, so the
    engine's memory logic carries no infrastructure dependency. Implemented
    durably by `persistence.StorageConversationStore` and non-durably by
    `InMemoryConversationStore`.
    """

    async def append(
        self,
        tenant_id: str,
        conversation_id: str,
        user_content: str,
        assistant_content: str,
        request_id: str,
        user_id: str,
    ) -> ConversationTurn:
        """Persist one completed turn and return it with its assigned sequence."""
        ...

    async def recent_turns(
        self, tenant_id: str, conversation_id: str, limit: int, offset: int = 0
    ) -> list[ConversationTurn]:
        """Return at most `limit` most-recent turns, oldest-first, with optional `offset`."""
        ...


class InMemoryConversationStore(IConversationStore):
    """Non-durable reference store. **History is lost when the process exits.**

    Intended for development and tests. It is never a default:
    `AIMemoryManager` requires an explicit store, so this can only be
    selected deliberately — a deployment cannot silently end up with
    non-durable memory by omission.
    """

    def __init__(self) -> None:
        self._turns: dict[tuple[str, str], list[ConversationTurn]] = {}
        self._lock = asyncio.Lock()

    async def append(
        self,
        tenant_id: str,
        conversation_id: str,
        user_content: str,
        assistant_content: str,
        request_id: str,
        user_id: str,
    ) -> ConversationTurn:
        key = (tenant_id, conversation_id)
        async with self._lock:
            existing = self._turns.setdefault(key, [])
            turn = ConversationTurn(
                sequence=len(existing) + 1,
                user_content=user_content,
                assistant_content=assistant_content,
                request_id=request_id,
                user_id=user_id,
                created_at=datetime.datetime.now(datetime.UTC),
            )
            existing.append(turn)
            return turn

    async def recent_turns(
        self, tenant_id: str, conversation_id: str, limit: int, offset: int = 0
    ) -> list[ConversationTurn]:
        async with self._lock:
            turns = self._turns.get((tenant_id, conversation_id), [])
            if not turns or offset >= len(turns):
                return []
            end_idx = len(turns) - offset
            start_idx = max(0, end_idx - limit)
            return list(turns[start_idx:end_idx])


class AIMemoryManager:
    """Retrieves and records conversation history.

    Satisfies the frozen `IAIMemoryManager` Protocol. Stateless apart from
    its injected store and configuration; performs no I/O directly and never
    contacts a provider, the Kernel, or Security Engine.
    """

    def __init__(self, store: IConversationStore, max_history_turns: int = 20) -> None:
        """Args:
        store: Required. There is deliberately no default, so no deployment
            can obtain non-durable memory by omitting it.
        max_history_turns: Retained turn count, clamped to [1, 200].
        """
        self._store = store
        self._max_history_turns = max(1, min(max_history_turns, _MAX_HISTORY_TURNS_CEILING))

    @property
    def max_history_turns(self) -> int:
        """Effective (clamped) retained-turn limit."""
        return self._max_history_turns

    async def get_turns(self, tenant_id: str, conversation_id: str, offset: int = 0) -> list[ConversationTurn]:
        """Return the most recent turns, oldest-first, with optional offset pagination."""
        require_identifier(tenant_id, "tenant_id")
        require_identifier(conversation_id, "conversation_id")
        if offset > 0:
            return await self._store.recent_turns(tenant_id, conversation_id, self._max_history_turns, offset=offset)
        return await self._store.recent_turns(tenant_id, conversation_id, self._max_history_turns)

    async def get_context(self, tenant_id: str, conversation_id: str, offset: int = 0) -> list[str]:
        """Return the rendered form of exactly what `get_turns` returns."""
        turns = await self.get_turns(tenant_id, conversation_id, offset=offset)
        rendered: list[str] = []
        for turn in turns:
            rendered.append(f"{USER_MARKER}\n{sanitize_context_content(turn.user_content)}")
            rendered.append(f"{ASSISTANT_MARKER}\n{sanitize_context_content(turn.assistant_content)}")
        return rendered

    async def append_history(
        self,
        tenant_id: str,
        conversation_id: str,
        request: LLMRequest,
        response: LLMResponse,
    ) -> None:
        """Record one completed turn.

        The request's own `tenant_id`/`conversation_id` must agree with the
        explicit arguments. A mismatch is rejected rather than reconciled:
        silently trusting either side would store one tenant's content under
        another tenant's key, which is a cross-tenant write.
        """
        require_identifier(tenant_id, "tenant_id")
        require_identifier(conversation_id, "conversation_id")

        if request.tenant_id != tenant_id:
            raise MemoryValidationError(
                "Request tenant_id does not match the tenant_id supplied to append_history; "
                "refusing to record a turn under a different tenant."
            )
        if request.conversation_id != conversation_id:
            raise MemoryValidationError(
                "Request conversation_id does not match the conversation_id supplied to "
                "append_history; refusing to record a turn under a different conversation."
            )

        await self._store.append(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_content=request.prompt,
            assistant_content=response.text_content,
            request_id=request.request_id,
            user_id=request.user_id,
        )


__all__ = [
    "ASSISTANT_MARKER",
    "USER_MARKER",
    "AIMemoryManager",
    "ConversationTurn",
    "IConversationStore",
    "InMemoryConversationStore",
    "require_identifier",
    "sanitize_context_content",
]
