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
"""The single registry of context markers and their owning milestone.

Imports M4's markers rather than restating them, so the two can never
drift. Two properties make this load-bearing rather than documentation:

1. **Every marker begins with `MARKER_SENTINEL`.** Sanitization neutralizes
   exactly that prefix, so a future marker using a different delimiter
   (say ``<<tool>>``) would silently fall outside the anti-forgery
   guarantee. A test asserts this for every entry, including the reserved
   one.
2. **There is no `[[system]]` marker, and there must never be one.** The
   trusted layer is carried solely by `LLMRequest.system_instruction`, a
   separate field, so no in-band token exists for untrusted content to
   forge. Adding a system marker would manufacture the very forgery target
   the design avoids.
"""


class PromptPipeline:
    """Assembles context into a new `LLMRequest`. Pure and synchronous.

    Trust model:

    - `system_instruction` is the trusted layer and is passed through
      untouched. This class contains no code path that writes to it.
    - `prompt` is passed through untouched — the user's words are never
      rewritten.
    - Everything placed into `context_documents` is untrusted and is
      sanitized, **with no exemptions** — including the caller's own
      entries. A guarantee with an exemption is not a guarantee: a caller
      entry containing ``[[assistant]]`` would otherwise forge a role
      boundary.
    - History entries are the one thing *not* sanitized here, because they
      arrive already sanitized and already marked from
      `AIMemoryManager.get_context()`. Re-running sanitization over
      ``[[user]]\\ntext`` would rewrite the marker itself into
      ``[ [user]]`` and destroy it.
    """

    def assemble(
        self,
        request: LLMRequest,
        history_entries: list[str],
        documents: list[RetrievedDocument],
    ) -> LLMRequest:
        """Return a new request with context assembled into `context_documents`.

        Order is caller entries, then knowledge, then history. History sits
        adjacent to the current prompt for conversational recency;
        knowledge is background framing; the caller's own entries lead
        because they are the application's explicit framing and should not
        be displaced. This ordering is tunable policy, not a correctness
        invariant.

        The input request is never mutated — `LLMRequest` is frozen and a
        copy is returned.
        """
        caller_entries = [sanitize_context_content(entry) for entry in request.context_documents]
        knowledge_entries = [
            f"{KNOWLEDGE_MARKER}\n{sanitize_context_content(document.content)}"
            for document in documents
        ]
        # History is inserted verbatim: already sanitized and marked by M4.
        assembled = [*caller_entries, *knowledge_entries, *history_entries]
        return request.model_copy(update={"context_documents": assembled})


class ContextComposer:
    """Fetches history and knowledge, then assembles them into a request.

    Stateless apart from its injected collaborators and configuration, so
    it is safe to construct per request — which Milestone 8 must do, since
    the knowledge adapter has to be bound to the requesting principal's
    authority rather than a long-lived service principal.
    """

    def __init__(
        self,
        memory: AIMemoryManager,
        pipeline: PromptPipeline,
        knowledge: IKnowledgeQueryPort | None = None,
        max_documents: int = 5,
        allowed_classifications: frozenset[str] = DEFAULT_ALLOWED_CLASSIFICATIONS,
    ) -> None:
        """Args:
        memory: Supplies conversation history via its public rendered form.
        pipeline: The pure assembler.
        knowledge: Optional. When absent, requesting retrieval is an error
            rather than a silent no-op — see `compose`.
        max_documents: Bound on retrieved documents, clamped to [1, 50].
        allowed_classifications: Fail-closed allowlist, normalized once here
            so a lowercase allowlist behaves the same as an uppercase one.
        """
        self._memory = memory
        self._pipeline = pipeline
        self._knowledge = knowledge
        self._max_documents = max(1, min(max_documents, 50))
        self._allowed_classifications = normalize_allowed_classifications(allowed_classifications)

    @property
    def max_documents(self) -> int:
        """Effective (clamped) retrieved-document bound."""
        return self._max_documents

    @property
    def allowed_classifications(self) -> frozenset[str]:
        """Effective (normalized) classification allowlist."""
        return self._allowed_classifications

    async def compose(
        self, request: LLMRequest, *, knowledge_query: str | None = None
    ) -> LLMRequest:
        """Compose a context-enriched request.

        Retrieval is opt-in: without `knowledge_query` the port is never
        called. This is deliberate — Knowledge Engine's search is a
        substring match over a full tenant-corpus scan with no caching, so
        retrieving on every request would be expensive and would usually
        match nothing.

        Raises:
            MemoryValidationError: `request.tenant_id`/`conversation_id` is
                blank or whitespace-only. Checked before anything else —
                an unvalidated identifier must never cross the knowledge
                port, not only the memory store's own boundary.
            ContextCompositionError: Retrieval was requested but no port is
                configured. Failing here prevents the failure mode where an
                unwired port silently returns nothing and the capability
                merely appears to work.
            KnowledgeRetrievalError: Retrieval was requested and failed, or
                the adapter returned more documents than it was asked for.
        """
        require_identifier(request.tenant_id, "tenant_id")
        require_identifier(request.conversation_id, "conversation_id")

        documents: list[RetrievedDocument] = []
        if knowledge_query is not None and knowledge_query.strip():
            documents = await self._retrieve(request.tenant_id, knowledge_query)

        history_entries = await self._memory.get_context(
            request.tenant_id, request.conversation_id
        )
        return self._pipeline.assemble(request, history_entries, documents)

    async def _retrieve(self, tenant_id: str, query_text: str) -> list[RetrievedDocument]:
        """Retrieve, verify the port honoured its bound, then filter."""
        if self._knowledge is None:
            raise ContextCompositionError(
                "Knowledge retrieval was requested but no IKnowledgeQueryPort is configured."
            )

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
            raise KnowledgeRetrievalError(
                f"Knowledge retrieval failed: {type(exc).__name__}"
            ) from exc

        if len(documents) > self._max_documents:
            # Truncating here would be exactly the silent truncation this
            # design forbids; an over-returning adapter is a bug worth surfacing.
            raise KnowledgeRetrievalError(
                f"Knowledge port returned {len(documents)} documents when at most "
                f"{self._max_documents} were requested."
            )

        return self._deduplicate(self._filter_by_classification(documents))

    def _filter_by_classification(
        self, documents: list[RetrievedDocument]
    ) -> list[RetrievedDocument]:
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
]
