"""Context composition for the KORTEX OS AI Orchestration Engine.

Turns conversation history and retrieved knowledge into a safely assembled
`LLMRequest`. Governed by the approved Milestone 5 architecture.

Two components with deliberately different shapes: `PromptPipeline` is a
pure, synchronous assembler that touches nothing, and `ContextComposer`
performs the async fetching around it. Keeping assembly pure means the
security-critical part — what ends up in the prompt — is testable with no
I/O and no fakes.

This module never contacts a provider, never routes, never persists, and
never makes an authority decision.
"""

from __future__ import annotations

from collections.abc import Callable

from kortex.engines.ai.exceptions import ContextCompositionError, KnowledgeRetrievalError
from kortex.engines.ai.memory import (
    ASSISTANT_MARKER,
    USER_MARKER,
    AIMemoryManager,
    require_identifier,
    sanitize_context_content,
)
from kortex.engines.ai.models import LLMRequest
from kortex.engines.ai.retrieval import (
    DEFAULT_ALLOWED_CLASSIFICATIONS,
    IKnowledgeQueryPort,
    RetrievedDocument,
    normalize_allowed_classifications,
    normalize_classification,
)

KNOWLEDGE_MARKER = "[[knowledge]]"
"""Marks a retrieved knowledge document. Owned by Milestone 5."""

TOOL_MARKER = "[[tool]]"
"""**Reserved for Milestone 6. Not implemented here.**

Declared only so the marker registry can prevent a future milestone from
duplicating an existing marker or introducing one that falls outside the
sanitization guarantee. Nothing in this module emits it.
"""

MARKER_SENTINEL = "[["
"""The prefix every marker shares, and the exact string sanitization neutralizes."""

RESERVED_CONTEXT_MARKERS: dict[str, str] = {
    USER_MARKER: "M4",
    ASSISTANT_MARKER: "M4",
    KNOWLEDGE_MARKER: "M5",
    TOOL_MARKER: "M6 (reserved)",
}


def estimate_tokens(text: str) -> int:
    """Deterministic token estimation heuristic (~4 characters per token, minimum 1 for non-empty strings)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


class PromptPipeline:
    """Assembles context into a new `LLMRequest` with optional context token budgeting. Pure and synchronous.

    Trust model:

    - `system_instruction` is the trusted layer and is passed through
      untouched. This class contains no code path that writes to it.
    - `prompt` is passed through untouched — the user's words are never
      rewritten.
    - Everything placed into `context_documents` is untrusted and is
      sanitized, **with no exemptions** — including the caller's own
      entries.
    - History entries arrive already sanitized and marked from
      `AIMemoryManager.get_context()`.
    - When `max_context_tokens` is configured, a deterministic sliding-window
      policy prioritizes recent conversation history and bounded knowledge docs
      while ensuring the combined prompt never exceeds budget.
    """

    def __init__(
        self,
        max_context_tokens: int | None = None,
        token_estimator: Callable[[str], int] | None = None,
    ) -> None:
        self._max_context_tokens = max_context_tokens
        self._token_estimator = token_estimator or estimate_tokens

    @property
    def max_context_tokens(self) -> int | None:
        """Configured upper bound on total context tokens."""
        return self._max_context_tokens

    def assemble(
        self,
        request: LLMRequest,
        history_entries: list[str],
        documents: list[RetrievedDocument],
        max_context_tokens: int | None = None,
    ) -> LLMRequest:
        """Return a new request with context assembled into `context_documents`.

        Order is caller entries, then knowledge, then history.

        When a token budget is active (via parameter or constructor default):
        1. System instruction + current prompt tokens are reserved as highest priority.
        2. Caller context documents are included up to available capacity.
        3. Knowledge documents are included up to available capacity.
        4. History entries use a sliding window: most recent turns are prioritized first.
        """
        caller_entries = [sanitize_context_content(entry) for entry in request.context_documents]
        knowledge_entries = [
            f"{KNOWLEDGE_MARKER}\n{sanitize_context_content(document.content)}" for document in documents
        ]
        history = list(history_entries)

        budget = max_context_tokens if max_context_tokens is not None else self._max_context_tokens
        if budget is None:
            assembled = [*caller_entries, *knowledge_entries, *history]
            return request.model_copy(update={"context_documents": assembled})

        base_tokens = self._token_estimator(request.prompt) + self._token_estimator(request.system_instruction or "")
        remaining_budget = max(0, budget - base_tokens)

        # 1. Caller entries (application explicit framing)
        selected_caller: list[str] = []
        for entry in caller_entries:
            cost = self._token_estimator(entry)
            if cost <= remaining_budget:
                selected_caller.append(entry)
                remaining_budget -= cost
            else:
                break

        # 2. Knowledge entries (background framing)
        selected_knowledge: list[str] = []
        for entry in knowledge_entries:
            cost = self._token_estimator(entry)
            if cost <= remaining_budget:
                selected_knowledge.append(entry)
                remaining_budget -= cost
            else:
                break

        # 3. History entries (sliding window: newest turns preserved first)
        selected_history_reversed: list[str] = []
        for entry in reversed(history):
            cost = self._token_estimator(entry)
            if cost <= remaining_budget:
                selected_history_reversed.append(entry)
                remaining_budget -= cost
            else:
                break
        selected_history = list(reversed(selected_history_reversed))

        assembled = [*selected_caller, *selected_knowledge, *selected_history]
        return request.model_copy(update={"context_documents": assembled})


class ContextComposer:
    """Fetches history and knowledge, then assembles them into a request with token budget enforcement.

    Stateless apart from its injected collaborators and configuration, so
    it is safe to construct per request.
    """

    def __init__(
        self,
        memory: AIMemoryManager,
        pipeline: PromptPipeline,
        knowledge: IKnowledgeQueryPort | None = None,
        max_documents: int = 5,
        allowed_classifications: frozenset[str] = DEFAULT_ALLOWED_CLASSIFICATIONS,
        max_context_tokens: int | None = None,
    ) -> None:
        """Args:
        memory: Supplies conversation history via its public rendered form.
        pipeline: The pure assembler.
        knowledge: Optional. When absent, requesting retrieval is an error.
        max_documents: Bound on retrieved documents, clamped to [1, 50].
        allowed_classifications: Fail-closed allowlist, normalized once here.
        max_context_tokens: Optional token budget bound enforced during prompt assembly.
        """
        self._memory = memory
        self._pipeline = pipeline
        self._knowledge = knowledge
        self._max_documents = max(1, min(max_documents, 50))
        self._allowed_classifications = normalize_allowed_classifications(allowed_classifications)
        self._max_context_tokens = max_context_tokens

    @property
    def max_documents(self) -> int:
        """Effective (clamped) retrieved-document bound."""
        return self._max_documents

    @property
    def allowed_classifications(self) -> frozenset[str]:
        """Effective (normalized) classification allowlist."""
        return self._allowed_classifications

    @property
    def max_context_tokens(self) -> int | None:
        """Configured maximum context token limit."""
        return self._max_context_tokens

    async def compose(self, request: LLMRequest, *, knowledge_query: str | None = None) -> LLMRequest:
        """Compose a context-enriched request with token budget enforcement."""
        require_identifier(request.tenant_id, "tenant_id")
        require_identifier(request.conversation_id, "conversation_id")

        documents: list[RetrievedDocument] = []
        if knowledge_query is not None and knowledge_query.strip():
            documents = await self._retrieve(request.tenant_id, knowledge_query)

        history_entries = await self._memory.get_context(request.tenant_id, request.conversation_id)
        return self._pipeline.assemble(
            request=request,
            history_entries=history_entries,
            documents=documents,
            max_context_tokens=self._max_context_tokens,
        )

    async def _retrieve(self, tenant_id: str, query_text: str) -> list[RetrievedDocument]:
        """Retrieve, verify the port honoured its bound, then filter."""
        if self._knowledge is None:
            raise ContextCompositionError("Knowledge retrieval was requested but no IKnowledgeQueryPort is configured.")

        try:
            documents = await self._knowledge.search(
                tenant_id=tenant_id,
                query_text=query_text,
                max_results=self._max_documents,
            )
        except KnowledgeRetrievalError:
            raise
        except Exception as exc:
            # Message names the failure type only — retrieved content and
            # query text are tenant-sensitive.
            raise KnowledgeRetrievalError(f"Knowledge retrieval failed: {type(exc).__name__}") from exc

        if len(documents) > self._max_documents:
            # Truncating here would be exactly the silent truncation this
            # design forbids; an over-returning adapter is a bug worth surfacing.
            raise KnowledgeRetrievalError(
                f"Knowledge port returned {len(documents)} documents when at most {self._max_documents} were requested."
            )

        return self._deduplicate(self._filter_by_classification(documents))

    def _filter_by_classification(self, documents: list[RetrievedDocument]) -> list[RetrievedDocument]:
        """Drop documents whose classification is not explicitly allowed.

        Knowledge Engine applies no classification filtering of its own, so
        this is the only gate. Unknown, absent, and non-ASCII values are all
        rejected — unknown is never assumed safe.
        """
        return [
            document
            for document in documents
            if (normalized := normalize_classification(document.classification)) is not None
            and normalized in self._allowed_classifications
        ]

    def _deduplicate(self, documents: list[RetrievedDocument]) -> list[RetrievedDocument]:
        """Remove exact-content duplicates, keeping the first occurrence.

        Normalization rather than truncation: it removes redundancy only
        and cannot lose information.
        """
        seen: set[str] = set()
        unique: list[RetrievedDocument] = []
        for document in documents:
            if document.content in seen:
                continue
            seen.add(document.content)
            unique.append(document)
        return unique


__all__ = [
    "KNOWLEDGE_MARKER",
    "MARKER_SENTINEL",
    "RESERVED_CONTEXT_MARKERS",
    "TOOL_MARKER",
    "ContextComposer",
    "PromptPipeline",
    "estimate_tokens",
]
