"""Unit tests for AI Orchestration Engine knowledge retrieval contracts (Milestone 5).

Covers the port DTO, the classification normalization rules, and the
in-memory reference port. Composition behaviour lives in
`test_ai_pipeline.py`.

Local fakes only — no Knowledge Engine, no Kernel, no network.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kortex.engines.ai.retrieval import (
    CONFIDENTIAL,
    DEFAULT_ALLOWED_CLASSIFICATIONS,
    INTERNAL,
    KNOWN_CLASSIFICATIONS,
    PUBLIC,
    RESTRICTED,
    IKnowledgeQueryPort,
    InMemoryKnowledgeQueryPort,
    RetrievedDocument,
    normalize_allowed_classifications,
    normalize_classification,
)

# U+0131 LATIN SMALL LETTER DOTLESS I — uppercases to "I", so this string
# folds onto exactly "PUBLIC". A real bypass, not a hypothetical one.
UNICODE_LOOKALIKE = "publ" + chr(0x131) + "c"


# --------------------------------------------------------------------------
# Classification constants
# --------------------------------------------------------------------------


def test_known_classifications_are_exactly_the_four_levels() -> None:
    assert set(KNOWN_CLASSIFICATIONS) == {PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED}


def test_default_allowlist_is_fail_closed() -> None:
    """Confidential and restricted must require an explicit opt-in."""
    assert set(DEFAULT_ALLOWED_CLASSIFICATIONS) == {PUBLIC, INTERNAL}
    assert CONFIDENTIAL not in DEFAULT_ALLOWED_CLASSIFICATIONS
    assert RESTRICTED not in DEFAULT_ALLOWED_CLASSIFICATIONS


# --------------------------------------------------------------------------
# normalize_classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PUBLIC", PUBLIC),
        ("public", PUBLIC),
        ("  Internal  ", INTERNAL),
        ("\tCONFIDENTIAL\n", CONFIDENTIAL),
        ("restricted", RESTRICTED),
    ],
)
def test_normalization_strips_and_uppercases(raw: str, expected: str) -> None:
    assert normalize_classification(raw) == expected


def test_none_is_rejected() -> None:
    """Absent classification is unknown, never public."""
    assert normalize_classification(None) is None


@pytest.mark.parametrize("raw", ["", "   ", "SECRET", "TOP_SECRET", "publicish", "PUBLIC "[:-1] + "X"])
def test_unknown_values_are_rejected(raw: str) -> None:
    assert normalize_classification(raw) is None


def test_non_ascii_lookalike_is_rejected_before_case_folding() -> None:
    """The ASCII guard closes a demonstrated Unicode bypass.

    Without it, this value would uppercase to exactly "PUBLIC" and be
    admitted by a fail-closed filter.
    """
    assert UNICODE_LOOKALIKE.upper() == PUBLIC  # the bypass is real
    assert normalize_classification(UNICODE_LOOKALIKE) is None  # and is closed


def test_non_ascii_is_rejected_generally() -> None:
    """Ambiguous/full-width look-alikes are the point of this test, so the
    confusable-character lint is suppressed deliberately here."""
    zero_width_space = "PUBLIC" + chr(0x200B)
    fullwidth_public = "".join(chr(ord(c) - ord("A") + 0xFF21) for c in "PUBLIC")
    assert normalize_classification(zero_width_space) is None
    assert normalize_classification(fullwidth_public) is None


# --------------------------------------------------------------------------
# normalize_allowed_classifications
# --------------------------------------------------------------------------


def test_lowercase_allowlist_behaves_identically_to_uppercase() -> None:
    assert normalize_allowed_classifications(frozenset({"public", "internal"})) == {
        PUBLIC,
        INTERNAL,
    }


def test_allowlist_drops_unrecognized_entries() -> None:
    """A typo must not widen the allowlist."""
    assert normalize_allowed_classifications(frozenset({"PUBLIC", "SECRET"})) == {PUBLIC}


def test_allowlist_drops_non_ascii_entries() -> None:
    assert normalize_allowed_classifications(frozenset({UNICODE_LOOKALIKE})) == frozenset()


def test_empty_allowlist_stays_empty() -> None:
    assert normalize_allowed_classifications(frozenset()) == frozenset()


# --------------------------------------------------------------------------
# RetrievedDocument
# --------------------------------------------------------------------------


def test_document_defaults_and_frozen() -> None:
    document = RetrievedDocument(content="body")
    assert document.classification is None
    assert document.source_id is None
    with pytest.raises(ValidationError):
        document.content = "changed"  # type: ignore[misc]


def test_document_carries_classification_and_provenance_verbatim() -> None:
    """Neither field is interpreted or rewritten at construction."""
    document = RetrievedDocument(content="body", classification="  confidential ", source_id="rec-1")
    assert document.classification == "  confidential "
    assert document.source_id == "rec-1"


# --------------------------------------------------------------------------
# InMemoryKnowledgeQueryPort
# --------------------------------------------------------------------------


def test_in_memory_port_satisfies_protocol() -> None:
    assert isinstance(InMemoryKnowledgeQueryPort(), IKnowledgeQueryPort)


async def test_in_memory_port_matches_case_insensitive_substring() -> None:
    port = InMemoryKnowledgeQueryPort(
        [RetrievedDocument(content="Quarterly Revenue Report"), RetrievedDocument(content="Other")]
    )
    results = await port.search(tenant_id="t1", query_text="revenue", max_results=10)
    assert [d.content for d in results] == ["Quarterly Revenue Report"]


async def test_in_memory_port_respects_max_results() -> None:
    port = InMemoryKnowledgeQueryPort([RetrievedDocument(content=f"doc {i}") for i in range(10)])
    assert len(await port.search(tenant_id="t1", query_text="doc", max_results=3)) == 3


async def test_in_memory_port_returns_empty_on_no_match() -> None:
    port = InMemoryKnowledgeQueryPort([RetrievedDocument(content="alpha")])
    assert await port.search(tenant_id="t1", query_text="omega", max_results=5) == []


async def test_in_memory_port_add_extends_corpus() -> None:
    port = InMemoryKnowledgeQueryPort()
    port.add(RetrievedDocument(content="added later"))
    assert len(await port.search(tenant_id="t1", query_text="added", max_results=5)) == 1
