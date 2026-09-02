"""Knowledge retrieval port for the KORTEX OS AI Orchestration Engine.

Defines the boundary through which context documents are obtained, without
the AI package ever importing Knowledge Engine. Governed by the approved
Milestone 5 architecture.

**Why a port rather than a direct call.** Reaching
`kortex.knowledge.query.search` through the Kernel requires constructing a
real `KnowledgeQuery`, which requires importing `kortex.engines.knowledge`
— forbidden in this package. The capability is also registered
`requires_authentication=True`, so the call needs a session token that
`LLMRequest` does not carry. Both concerns therefore live in the adapter,
outside this package; Milestone 8 supplies it along with the session token.

**What the real adapter must honour**, recorded here because this module is
the contract's home:

- Return **records only**. Knowledge Engine's `search_graph` never reads
  `trust_states`, so `matching_nodes` bypass the trust filter that protects
  `matching_records`, and nodes carry no classification at all.
- Cap the **union**. `max_results` is applied per sub-search inside
  Knowledge Engine, so asking for N can return up to 2N items; the port
  contract is a bound on what the caller receives.
- Deliver `content` as an already-rendered `str`. `KnowledgeRecord.content`
  is a `Dict[str, Any]` with no title/text convention, so rendering is the
  adapter's decision, not this engine's.
- Bind to the **requesting principal's** authority, never a long-lived
  service principal — otherwise retrieval becomes a privilege-escalation
  path within a tenant.

Retrieval here is substring-matched and lexicographically truncated by
Knowledge Engine, with no relevance ranking. It must not be described as
semantic retrieval.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

PUBLIC = "PUBLIC"
INTERNAL = "INTERNAL"
CONFIDENTIAL = "CONFIDENTIAL"
RESTRICTED = "RESTRICTED"

KNOWN_CLASSIFICATIONS = frozenset({PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED})
"""The only classification values this engine will recognize.

Deliberately plain strings mirroring Knowledge Engine's own vocabulary.
Security Engine's `ClassificationLevel` is **not** imported: that would
cross an authority boundary this package does not cross, and this filter is
defense-in-depth rather than the authoritative classification model.
"""

DEFAULT_ALLOWED_CLASSIFICATIONS = frozenset({PUBLIC, INTERNAL})
"""Fail-closed default: confidential and restricted content requires an
explicit opt-in from the caller.

Knowledge Engine performs no classification filtering of its own, so
without this a forgetful caller could pull `RESTRICTED` tenant data into a
prompt. Mirrors the fail-closed posture already established for cloud
egress in the model router.
"""


def normalize_classification(value: str | None) -> str | None:
    """Normalize a classification label, or return `None` if unusable.

    Rejects (returns `None` for) anything that cannot be trusted to mean
    what it appears to mean:

    - `None` — absent classification is *unknown*, never *public*.
    - Non-ASCII input, **rejected before case-folding**. This is not
      hypothetical: `("publ" + chr(0x131) + "c").upper()` is exactly
      `"PUBLIC"`, so a Unicode look-alike would otherwise fold onto an
      allowed value and turn a fail-closed filter into a bypass.
    - Any value that is not one of `KNOWN_CLASSIFICATIONS` after
      normalization — unknown is never assumed safe.
    """
    if value is None:
        return None
    if not value.isascii():
        return None
    normalized = value.strip().upper()
    if normalized not in KNOWN_CLASSIFICATIONS:
        return None
    return normalized


def normalize_allowed_classifications(values: frozenset[str]) -> frozenset[str]:
    """Normalize a caller-supplied allowlist once, at construction.

    Without this, an allowlist of `{"public"}` would silently match nothing
    — fail-closed, but confusingly so. Unrecognized entries are dropped
    rather than honoured, so a typo cannot widen the allowlist.
    """
    normalized = {normalize_classification(value) for value in values}
    return frozenset(value for value in normalized if value is not None)


class RetrievedDocument(BaseModel):
    """One document returned by the knowledge port.

    `classification` is carried verbatim and never interpreted by this
    engine beyond allowlist matching — interpreting it would require
    Security Engine's model and a policy that does not exist yet.

    `source_id` exists for future observability and audit only. It is
    **never inserted into the assembled request**: this engine exposes no
    citation feature, and rendering it would create an additional
    attacker-influenced text path. A later milestone that renders it must
    sanitize it.
    """

    model_config = ConfigDict(frozen=True)

    content: str
    classification: str | None = None
    source_id: str | None = None


@runtime_checkable
class IKnowledgeQueryPort(Protocol):
    """Port for retrieving context documents.

    Implemented for real outside this package (Milestone 8), because the
    adapter must import Knowledge Engine and hold a session token — see the
    module docstring.
    """

    async def search(self, tenant_id: str, query_text: str, max_results: int) -> list[RetrievedDocument]:
        """Return at most `max_results` documents for `tenant_id`.

        `max_results` is required and has no default: Knowledge Engine
        treats an absent cap as *unlimited*, which on a common term would
        pull an entire tenant corpus into a prompt. Returning more than
        `max_results` is a contract violation and is rejected by the
        caller.
        """
        ...


class InMemoryKnowledgeQueryPort(IKnowledgeQueryPort):
    """Non-durable reference port serving canned documents.

    For development and tests. Performs a case-insensitive substring match,
    deliberately mirroring the real engine's behaviour so callers do not
    develop against semantics the real adapter cannot deliver.
    """

    def __init__(self, documents: list[RetrievedDocument] | None = None) -> None:
        self._documents: list[RetrievedDocument] = list(documents or [])

    def add(self, document: RetrievedDocument) -> None:
        """Add one document to the canned corpus."""
        self._documents.append(document)

    async def search(self, tenant_id: str, query_text: str, max_results: int) -> list[RetrievedDocument]:
        needle = query_text.lower()
        matches = [doc for doc in self._documents if needle in doc.content.lower()]
        return matches[:max_results]


__all__ = [
    "CONFIDENTIAL",
    "DEFAULT_ALLOWED_CLASSIFICATIONS",
    "INTERNAL",
    "KNOWN_CLASSIFICATIONS",
    "PUBLIC",
    "RESTRICTED",
    "IKnowledgeQueryPort",
    "InMemoryKnowledgeQueryPort",
    "RetrievedDocument",
    "normalize_allowed_classifications",
    "normalize_classification",
]
