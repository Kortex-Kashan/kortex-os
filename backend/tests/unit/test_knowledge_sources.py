"""Unit tests for the Knowledge Engine Reference Source Provider
(Milestone M5).

Verifies `ReferenceSourceProvider` satisfies `IKnowledgeSourceProvider`,
produces well-formed `SOURCE_EVIDENCE` `KnowledgeRecord`s scoped correctly
to the requested tenant, fails closed on malformed `tenant_id`, is
concurrency-safe across tenants, never leaks `USER`-typed or non-evidence
trust into its output, and carries zero production coupling to
`KnowledgeLineageManager`/`KnowledgeGraph` (the M5/M11 scope boundary).
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from kortex.engines.knowledge.exceptions import KnowledgeSourceIngestionError
from kortex.engines.knowledge.interfaces import IKnowledgeSourceProvider
from kortex.engines.knowledge.lineage import KnowledgeLineageManager
from kortex.engines.knowledge.models import (
    KnowledgeActorType,
    KnowledgeRecordStatus,
    KnowledgeTrustState,
)
from kortex.engines.knowledge.sources import ReferenceSourceProvider


# -- Contract conformance -------------------------------------------------------


def test_reference_source_provider_satisfies_iknowledgesourceprovider() -> None:
    assert isinstance(ReferenceSourceProvider(), IKnowledgeSourceProvider)


def test_source_id_is_synchronous_not_a_coroutine_function() -> None:
    """The committed Protocol declares `source_id()` synchronous, unlike
    every other Knowledge Engine manager method added in M3/M4 (`async
    def`). This must not be "corrected" to async during implementation."""
    provider = ReferenceSourceProvider()
    assert not inspect.iscoroutinefunction(provider.source_id)
    assert not asyncio.iscoroutine(provider.source_id())


def test_ingest_is_a_coroutine_function() -> None:
    provider = ReferenceSourceProvider()
    assert inspect.iscoroutinefunction(provider.ingest)


# -- source_id() -------------------------------------------------------------------


def test_source_id_is_non_empty_and_stable_across_calls() -> None:
    provider = ReferenceSourceProvider()
    first = provider.source_id()
    second = provider.source_id()
    assert first != ""
    assert first == second


# -- ingest() — valid ingestion ----------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_returns_well_formed_source_evidence_records() -> None:
    provider = ReferenceSourceProvider()
    records = await provider.ingest("tenant-a")

    assert len(records) >= 1
    for record in records:
        assert record.tenant_id == "tenant-a"
        assert record.trust_state == KnowledgeTrustState.SOURCE_EVIDENCE
        assert record.created_by_type == KnowledgeActorType.SERVICE_PRINCIPAL
        assert record.created_by == provider.source_id()
        assert record.parent_version_id is None
        assert record.status == KnowledgeRecordStatus.CURRENT
        assert record.content.get("source_id") == provider.source_id()


@pytest.mark.asyncio
async def test_ingest_never_produces_user_actor_type_or_non_evidence_trust() -> None:
    """Adversarial: prove no returned record can carry `USER` authorship or
    a trust state other than `SOURCE_EVIDENCE` — both would be integrity
    violations (a `USER`-typed record would misrepresent unreviewed,
    machine-ingested content as human-authored; any non-`SOURCE_EVIDENCE`
    trust state would bypass the M3/M6 promotion-gate invariant)."""
    provider = ReferenceSourceProvider()
    records = await provider.ingest("tenant-a")
    assert len(records) >= 1
    for record in records:
        assert record.created_by_type != KnowledgeActorType.USER
        assert record.trust_state == KnowledgeTrustState.SOURCE_EVIDENCE


@pytest.mark.asyncio
async def test_ingest_exposes_no_parameter_to_override_trust_or_actor_type() -> None:
    """Structural proof, not just behavioral: `ingest()`'s own signature
    accepts only `tenant_id` — there is no keyword argument through which a
    caller could ever attempt to inject a different `trust_state` or
    `created_by_type`."""
    signature = inspect.signature(ReferenceSourceProvider.ingest)
    assert list(signature.parameters) == ["self", "tenant_id"]


# -- ingest() — tenant isolation ----------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_scopes_every_record_to_the_requested_tenant() -> None:
    provider = ReferenceSourceProvider()
    records_a = await provider.ingest("tenant-a")
    records_b = await provider.ingest("tenant-b")

    assert all(r.tenant_id == "tenant-a" for r in records_a)
    assert all(r.tenant_id == "tenant-b" for r in records_b)
    # No identity overlap between the two tenants' record sets.
    ids_a = {r.record_id for r in records_a}
    ids_b = {r.record_id for r in records_b}
    assert ids_a.isdisjoint(ids_b)


@pytest.mark.asyncio
async def test_concurrent_ingest_calls_for_different_tenants_do_not_cross_contaminate() -> None:
    """The provider holds no per-call instance state, so concurrent
    `ingest()` calls for different tenants must never interleave or leak
    into each other's results."""
    provider = ReferenceSourceProvider()
    tenants = [f"tenant-{i}" for i in range(8)]

    results = await asyncio.gather(*(provider.ingest(t) for t in tenants))

    for tenant, records in zip(tenants, results):
        assert records, f"no records returned for {tenant}"
        assert all(r.tenant_id == tenant for r in records)


# -- ingest() — determinism of shape -----------------------------------------------


@pytest.mark.asyncio
async def test_repeated_ingest_is_shape_deterministic_but_identity_fresh() -> None:
    """Same tenant, two separate calls: count and field *shape* must match;
    `record_id`/`version_id` must differ (a fresh ingestion event each
    call) — this is a stated design decision, not an accident."""
    provider = ReferenceSourceProvider()
    first = await provider.ingest("tenant-a")
    second = await provider.ingest("tenant-a")

    assert len(first) == len(second)
    for r1, r2 in zip(first, second):
        assert r1.record_id != r2.record_id
        assert r1.version_id != r2.version_id
        assert r1.tenant_id == r2.tenant_id
        assert r1.trust_state == r2.trust_state
        assert r1.record_type == r2.record_type
        assert r1.created_by_type == r2.created_by_type


# -- ingest() — fail-closed on malformed tenant_id ---------------------------------


@pytest.mark.asyncio
async def test_ingest_rejects_empty_tenant_id() -> None:
    provider = ReferenceSourceProvider()
    with pytest.raises(KnowledgeSourceIngestionError):
        await provider.ingest("")


@pytest.mark.asyncio
async def test_ingest_rejects_none_tenant_id_despite_declared_type() -> None:
    """`tenant_id: str` is the declared type, but a caller that violates it
    at runtime (e.g. via `**kwargs` or a dynamically-constructed call) must
    still fail closed, never silently proceed or return `[]`."""
    provider = ReferenceSourceProvider()
    with pytest.raises(KnowledgeSourceIngestionError):
        await provider.ingest(None)  # type: ignore[arg-type]


# -- Cross-milestone scope boundary (M5 must not couple to M3/M2 production code) --


def test_sources_module_has_zero_production_references_to_lineage_or_graph() -> None:
    """Scope-boundary proof, mirroring M4's own
    `test_correction_annotation_never_mutates_or_references_any_record_manager`:
    `sources.py` must never *import* `kortex.engines.knowledge.lineage` or
    `kortex.engines.knowledge.graph` — persisting ingested records into
    lineage is `IKnowledgeEngine.index_source()`'s Milestone M11 job, not
    M5's. Checked against the module's actual import graph and top-level
    names, not its docstring prose (which legitimately discusses this very
    boundary in words)."""
    import kortex.engines.knowledge.sources as sources_module

    imported_module_names = {
        getattr(value, "__module__", None) for value in vars(sources_module).values()
    }
    assert "kortex.engines.knowledge.lineage" not in imported_module_names
    assert "kortex.engines.knowledge.graph" not in imported_module_names
    module_globals = vars(sources_module)
    assert "KnowledgeLineageManager" not in module_globals
    assert "KnowledgeGraph" not in module_globals
    assert not hasattr(ReferenceSourceProvider, "create_record")


@pytest.mark.asyncio
async def test_ingested_records_are_valid_create_record_input_test_only() -> None:
    """Recommended, non-blocking interop sanity check (test-only, no
    production coupling): every record `ingest()` produces must be a valid
    input to a *fresh* `KnowledgeLineageManager.create_record()` — proving
    M5's output actually composes with M3's contract, without `sources.py`
    itself importing or calling into `lineage.py`."""
    provider = ReferenceSourceProvider()
    records = await provider.ingest("tenant-a")

    manager = KnowledgeLineageManager()
    for record in records:
        stored = await manager.create_record(record)
        assert stored.trust_state == KnowledgeTrustState.SOURCE_EVIDENCE
        current = await manager.get_current(record.record_id, "tenant-a")
        assert current is not None
        assert current.record_id == record.record_id
