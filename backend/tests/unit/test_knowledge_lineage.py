"""Unit tests for the Knowledge Engine Record Lineage, Supersession & Trust
Promotion manager (Milestone M3 lineage/supersession; Milestone M6 trust
promotion).

Verifies `KnowledgeLineageManager` satisfies `IKnowledgeRecordManager`,
enforces tenant isolation, keeps the "exactly one CURRENT version"
invariant atomic, reconstructs full lineage chains correctly, never
mutates historical versions, and that `promote()` enforces USER-only
trust-state promotion (denying AGENT/SERVICE_PRINCIPAL, checked before
any record lookup) with zero partial-mutation on denial and zero
disturbance to lineage/version bookkeeping on success.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kortex.engines.knowledge.exceptions import (
    KnowledgeInvalidTrustTransitionError,
    KnowledgeLineageConsistencyError,
    KnowledgePromotionNotAuthorizedError,
    KnowledgeRecordNotFoundError,
)
from kortex.engines.knowledge.interfaces import IKnowledgeRecordManager
from kortex.engines.knowledge.lineage import KnowledgeLineageManager
from kortex.engines.knowledge.models import (
    KnowledgeActorType,
    KnowledgeRecord,
    KnowledgeRecordStatus,
    KnowledgeRecordType,
    KnowledgeTrustState,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _record(
    record_id: str,
    version_id: str,
    tenant_id: str = "tenant-a",
    parent_version_id: str | None = None,
    status: KnowledgeRecordStatus = KnowledgeRecordStatus.CURRENT,
    trust_state: KnowledgeTrustState = KnowledgeTrustState.HUMAN_CONFIRMED,
    successor_version_id: str | None = None,
) -> KnowledgeRecord:
    return KnowledgeRecord(
        record_id=record_id,
        tenant_id=tenant_id,
        version_id=version_id,
        parent_version_id=parent_version_id,
        record_type=KnowledgeRecordType.DECISION,
        trust_state=trust_state,
        created_by="user-1",
        created_by_type=KnowledgeActorType.USER,
        created_at=_NOW,
        status=status,
        successor_version_id=successor_version_id,
    )


# -- Contract conformance -------------------------------------------------------


def test_knowledge_lineage_manager_satisfies_iknowledgerecordmanager() -> None:
    assert isinstance(KnowledgeLineageManager(), IKnowledgeRecordManager)


# -- create_record ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_record_valid() -> None:
    manager = KnowledgeLineageManager()
    record = _record("rec-1", "v1")
    assert await manager.create_record(record) == record


@pytest.mark.asyncio
async def test_create_record_rejects_duplicate_record_chain() -> None:
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1"))
    with pytest.raises(KnowledgeLineageConsistencyError):
        await manager.create_record(_record("rec-1", "v2"))


@pytest.mark.asyncio
async def test_create_record_rejects_non_root_parent_version_id() -> None:
    manager = KnowledgeLineageManager()
    with pytest.raises(KnowledgeLineageConsistencyError):
        await manager.create_record(_record("rec-1", "v1", parent_version_id="v0"))


@pytest.mark.asyncio
async def test_create_record_normalizes_status_to_current() -> None:
    """Consistency finding: `create_record` must normalize the stored
    version's `status` to `CURRENT` just as `supersede` normalizes
    `new_version`'s, so the internal current-pointer index is never out of
    sync with the object's own `status` field."""
    manager = KnowledgeLineageManager()
    record = _record("rec-1", "v1", status=KnowledgeRecordStatus.ARCHIVED)
    result = await manager.create_record(record)
    assert result.status == KnowledgeRecordStatus.CURRENT

    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.status == KnowledgeRecordStatus.CURRENT


@pytest.mark.asyncio
async def test_create_record_same_id_allowed_across_different_tenants() -> None:
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1", tenant_id="tenant-a"))
    await manager.create_record(_record("rec-1", "v1", tenant_id="tenant-b"))  # must not raise


# -- get_current -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_valid() -> None:
    manager = KnowledgeLineageManager()
    record = _record("rec-1", "v1")
    await manager.create_record(record)
    assert await manager.get_current("rec-1", "tenant-a") == record


@pytest.mark.asyncio
async def test_get_current_returns_none_for_missing_record() -> None:
    """Confirmed contract per the interface's own `Optional` return type:
    a missing record is a normal outcome, not an exception."""
    manager = KnowledgeLineageManager()
    assert await manager.get_current("does-not-exist", "tenant-a") is None


@pytest.mark.asyncio
async def test_get_current_enforces_tenant_isolation() -> None:
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1", tenant_id="tenant-a"))
    assert await manager.get_current("rec-1", "tenant-b") is None


# -- get_lineage -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_lineage_raises_for_missing_record() -> None:
    manager = KnowledgeLineageManager()
    with pytest.raises(KnowledgeRecordNotFoundError):
        await manager.get_lineage("does-not-exist", "tenant-a")


@pytest.mark.asyncio
async def test_get_lineage_single_version() -> None:
    manager = KnowledgeLineageManager()
    record = _record("rec-1", "v1")
    await manager.create_record(record)
    assert await manager.get_lineage("rec-1", "tenant-a") == [record]


@pytest.mark.asyncio
async def test_get_lineage_enforces_tenant_isolation() -> None:
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1", tenant_id="tenant-a"))
    with pytest.raises(KnowledgeRecordNotFoundError):
        await manager.get_lineage("rec-1", "tenant-b")


@pytest.mark.asyncio
async def test_get_lineage_reconstructs_multi_version_chain_in_order() -> None:
    manager = KnowledgeLineageManager()
    v1 = _record("rec-1", "v1")
    await manager.create_record(v1)
    v2 = _record("rec-1", "v2", parent_version_id="v1")
    await manager.supersede("rec-1", "tenant-a", v2)
    v3 = _record("rec-1", "v3", parent_version_id="v2")
    await manager.supersede("rec-1", "tenant-a", v3)

    lineage = await manager.get_lineage("rec-1", "tenant-a")
    assert [v.version_id for v in lineage] == ["v1", "v2", "v3"]
    assert lineage[0].status == KnowledgeRecordStatus.SUPERSEDED
    assert lineage[0].successor_version_id == "v2"
    assert lineage[1].status == KnowledgeRecordStatus.SUPERSEDED
    assert lineage[1].successor_version_id == "v3"
    assert lineage[2].status == KnowledgeRecordStatus.CURRENT


# -- supersede ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supersede_valid_updates_current_and_preserves_history() -> None:
    manager = KnowledgeLineageManager()
    v1 = _record("rec-1", "v1")
    await manager.create_record(v1)
    v2 = _record("rec-1", "v2", parent_version_id="v1")

    result = await manager.supersede("rec-1", "tenant-a", v2)
    assert result.version_id == "v2"
    assert result.status == KnowledgeRecordStatus.CURRENT

    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.version_id == "v2"


@pytest.mark.asyncio
async def test_supersede_never_mutates_the_original_caller_object() -> None:
    """`KnowledgeRecord` is frozen — this test proves the manager never
    attempts in-place mutation (which would raise `ValidationError` on a
    frozen model) and that the caller's own `v1` reference is untouched;
    only the manager's internal copy reflects the SUPERSEDED transition."""
    manager = KnowledgeLineageManager()
    v1 = _record("rec-1", "v1")
    await manager.create_record(v1)
    v2 = _record("rec-1", "v2", parent_version_id="v1")
    await manager.supersede("rec-1", "tenant-a", v2)

    assert v1.status == KnowledgeRecordStatus.CURRENT  # caller's own object, unchanged
    lineage = await manager.get_lineage("rec-1", "tenant-a")
    assert lineage[0].status == KnowledgeRecordStatus.SUPERSEDED  # manager's internal copy


@pytest.mark.asyncio
async def test_supersede_normalizes_new_version_status_to_current() -> None:
    """Even if the caller forgot to set `status=CURRENT` on `new_version`,
    the manager's own invariant (exactly one CURRENT per record) must hold."""
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1"))
    v2 = _record("rec-1", "v2", parent_version_id="v1", status=KnowledgeRecordStatus.ARCHIVED)

    result = await manager.supersede("rec-1", "tenant-a", v2)
    assert result.status == KnowledgeRecordStatus.CURRENT


@pytest.mark.asyncio
async def test_supersede_raises_for_missing_record() -> None:
    manager = KnowledgeLineageManager()
    with pytest.raises(KnowledgeRecordNotFoundError):
        await manager.supersede("does-not-exist", "tenant-a", _record("does-not-exist", "v2", parent_version_id="v1"))


@pytest.mark.asyncio
async def test_supersede_rejects_stale_parent_version_id() -> None:
    """A supersession attempt whose `parent_version_id` does not match the
    actual current version is a stale/concurrent-modification attempt and
    must be rejected, not silently accepted."""
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1"))
    await manager.supersede("rec-1", "tenant-a", _record("rec-1", "v2", parent_version_id="v1"))

    stale_attempt = _record("rec-1", "v3", parent_version_id="v1")  # stale — current is now v2
    with pytest.raises(KnowledgeLineageConsistencyError):
        await manager.supersede("rec-1", "tenant-a", stale_attempt)

    # The stale attempt must not have changed anything.
    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.version_id == "v2"


@pytest.mark.asyncio
async def test_supersede_rejects_version_id_reused_from_an_older_ancestor() -> None:
    """Adversarial finding: reusing a `version_id` that already exists
    anywhere in the chain — not just the immediate parent being
    superseded — must be rejected. Otherwise the dict write in
    `supersede()` would silently overwrite that older stored version,
    corrupting history without any error."""
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1"))
    await manager.supersede("rec-1", "tenant-a", _record("rec-1", "v2", parent_version_id="v1"))

    reused_id_attempt = _record("rec-1", "v1", parent_version_id="v2")  # "v1" already exists
    with pytest.raises(KnowledgeLineageConsistencyError):
        await manager.supersede("rec-1", "tenant-a", reused_id_attempt)

    # The original v1 must remain intact, unclobbered.
    lineage = await manager.get_lineage("rec-1", "tenant-a")
    assert [v.version_id for v in lineage] == ["v1", "v2"]
    assert lineage[0].status == KnowledgeRecordStatus.SUPERSEDED
    assert lineage[0].successor_version_id == "v2"


@pytest.mark.asyncio
async def test_supersede_rejects_mismatched_new_version_identity() -> None:
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1"))
    wrong_record_id = _record("rec-2", "v2", parent_version_id="v1")
    with pytest.raises(KnowledgeLineageConsistencyError):
        await manager.supersede("rec-1", "tenant-a", wrong_record_id)


@pytest.mark.asyncio
async def test_supersede_enforces_tenant_isolation() -> None:
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1", tenant_id="tenant-a"))
    with pytest.raises(KnowledgeRecordNotFoundError):
        await manager.supersede("rec-1", "tenant-b", _record("rec-1", "v2", tenant_id="tenant-b", parent_version_id="v1"))


# -- promote (Milestone M6: USER-only trust-state promotion) ----------------------


@pytest.mark.asyncio
async def test_promote_succeeds_for_user_actor_and_changes_trust_state() -> None:
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1", trust_state=KnowledgeTrustState.AI_CANDIDATE))

    promoted = await manager.promote(
        "rec-1", "tenant-a", "actor-1", KnowledgeActorType.USER, KnowledgeTrustState.HUMAN_CONFIRMED
    )
    assert promoted.trust_state == KnowledgeTrustState.HUMAN_CONFIRMED

    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.trust_state == KnowledgeTrustState.HUMAN_CONFIRMED


@pytest.mark.asyncio
async def test_promote_denied_for_agent_actor() -> None:
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1", trust_state=KnowledgeTrustState.AI_CANDIDATE))

    with pytest.raises(KnowledgePromotionNotAuthorizedError):
        await manager.promote(
            "rec-1", "tenant-a", "actor-1", KnowledgeActorType.AGENT, KnowledgeTrustState.HUMAN_CONFIRMED
        )

    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.trust_state == KnowledgeTrustState.AI_CANDIDATE  # unchanged


@pytest.mark.asyncio
async def test_promote_denied_for_service_principal_actor() -> None:
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1", trust_state=KnowledgeTrustState.AI_CANDIDATE))

    with pytest.raises(KnowledgePromotionNotAuthorizedError):
        await manager.promote(
            "rec-1", "tenant-a", "actor-1", KnowledgeActorType.SERVICE_PRINCIPAL, KnowledgeTrustState.HUMAN_CONFIRMED
        )

    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.trust_state == KnowledgeTrustState.AI_CANDIDATE  # unchanged


@pytest.mark.asyncio
async def test_promote_raises_for_missing_record() -> None:
    manager = KnowledgeLineageManager()
    with pytest.raises(KnowledgeRecordNotFoundError):
        await manager.promote(
            "does-not-exist", "tenant-a", "actor-1", KnowledgeActorType.USER, KnowledgeTrustState.HUMAN_CONFIRMED
        )


@pytest.mark.asyncio
async def test_promote_enforces_tenant_isolation() -> None:
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1", tenant_id="tenant-a", trust_state=KnowledgeTrustState.AI_CANDIDATE))

    with pytest.raises(KnowledgeRecordNotFoundError):
        await manager.promote(
            "rec-1", "tenant-b", "actor-1", KnowledgeActorType.USER, KnowledgeTrustState.HUMAN_CONFIRMED
        )

    # tenant-a's own record must remain completely untouched by the cross-tenant attempt.
    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.trust_state == KnowledgeTrustState.AI_CANDIDATE


@pytest.mark.asyncio
async def test_promote_checks_authorization_before_record_existence() -> None:
    """A denied actor must get `KnowledgePromotionNotAuthorizedError` even
    for a wholly nonexistent `record_id` — never `KnowledgeRecordNotFoundError`
    — so a non-USER caller cannot use the distinct error type as an oracle
    to probe which record_ids exist."""
    manager = KnowledgeLineageManager()
    with pytest.raises(KnowledgePromotionNotAuthorizedError):
        await manager.promote(
            "does-not-exist", "tenant-a", "actor-1", KnowledgeActorType.AGENT, KnowledgeTrustState.HUMAN_CONFIRMED
        )


@pytest.mark.asyncio
async def test_promote_rejects_invalid_target_trust_state() -> None:
    """`promote()` moves a record *to* a confirmed trust state only —
    targeting `SOURCE_EVIDENCE`/`AI_CANDIDATE` is not a valid promotion."""
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1", trust_state=KnowledgeTrustState.SOURCE_EVIDENCE))

    for invalid_target in (KnowledgeTrustState.SOURCE_EVIDENCE, KnowledgeTrustState.AI_CANDIDATE):
        with pytest.raises(KnowledgeInvalidTrustTransitionError):
            await manager.promote("rec-1", "tenant-a", "actor-1", KnowledgeActorType.USER, invalid_target)

    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.trust_state == KnowledgeTrustState.SOURCE_EVIDENCE  # unchanged


@pytest.mark.asyncio
async def test_promote_rejects_already_confirmed_record() -> None:
    """Repeated/redundant promotion attempts on an already-confirmed
    record must be rejected, not silently accepted as a no-op."""
    manager = KnowledgeLineageManager()
    await manager.create_record(_record("rec-1", "v1", trust_state=KnowledgeTrustState.AI_CANDIDATE))
    await manager.promote(
        "rec-1", "tenant-a", "actor-1", KnowledgeActorType.USER, KnowledgeTrustState.HUMAN_CONFIRMED
    )

    with pytest.raises(KnowledgeInvalidTrustTransitionError):
        await manager.promote(
            "rec-1", "tenant-a", "actor-1", KnowledgeActorType.USER, KnowledgeTrustState.HUMAN_CORRECTED
        )

    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.trust_state == KnowledgeTrustState.HUMAN_CONFIRMED  # unchanged by the rejected re-promotion


@pytest.mark.asyncio
async def test_promote_does_not_create_a_new_lineage_version() -> None:
    """A promotion must never introduce a new `version_id` or disturb
    `parent_version_id` — that is `supersede()`'s concern, not
    `promote()`'s. `get_lineage()`'s chain shape is unaffected."""
    manager = KnowledgeLineageManager()
    v1 = _record("rec-1", "v1", trust_state=KnowledgeTrustState.SOURCE_EVIDENCE)
    await manager.create_record(v1)
    v2 = _record("rec-1", "v2", parent_version_id="v1", trust_state=KnowledgeTrustState.AI_CANDIDATE)
    await manager.supersede("rec-1", "tenant-a", v2)

    promoted = await manager.promote(
        "rec-1", "tenant-a", "actor-1", KnowledgeActorType.USER, KnowledgeTrustState.HUMAN_CONFIRMED
    )
    assert promoted.version_id == "v2"
    assert promoted.parent_version_id == "v1"

    lineage = await manager.get_lineage("rec-1", "tenant-a")
    assert [v.version_id for v in lineage] == ["v1", "v2"]
    assert lineage[0].status == KnowledgeRecordStatus.SUPERSEDED
    assert lineage[0].successor_version_id == "v2"
    assert lineage[1].status == KnowledgeRecordStatus.CURRENT
    assert lineage[1].trust_state == KnowledgeTrustState.HUMAN_CONFIRMED


@pytest.mark.asyncio
async def test_promote_never_mutates_the_original_caller_object() -> None:
    """`KnowledgeRecord` is frozen — proves `promote()` never attempts
    in-place mutation (which would raise on a frozen model): the caller's
    own object reference, created before `promote()` was ever called,
    remains at its original trust state."""
    manager = KnowledgeLineageManager()
    original = _record("rec-1", "v1", trust_state=KnowledgeTrustState.AI_CANDIDATE)
    await manager.create_record(original)

    await manager.promote(
        "rec-1", "tenant-a", "actor-1", KnowledgeActorType.USER, KnowledgeTrustState.HUMAN_CONFIRMED
    )

    assert original.trust_state == KnowledgeTrustState.AI_CANDIDATE  # caller's own object, unchanged
    current = await manager.get_current("rec-1", "tenant-a")
    assert current is not None
    assert current.trust_state == KnowledgeTrustState.HUMAN_CONFIRMED  # manager's internal copy


# -- Documented residual (found during M6 adversarial audit, not fixed here) ------


@pytest.mark.asyncio
async def test_create_record_and_supersede_accept_confirmed_trust_state_directly_by_design() -> None:
    """Adversarial-audit finding, deliberately NOT closed in Milestone M6:
    `create_record()`/`supersede()` accept a caller-supplied
    `HUMAN_CONFIRMED`/`HUMAN_CORRECTED` `trust_state` with no actor-type
    check at all -- unlike `promote()`, whose signature uniquely carries
    `actor_id`/`actor_type`. This is a Milestone M1 API design choice (the
    committed `IKnowledgeRecordManager` Protocol gives `create_record`/
    `supersede` no actor parameter to check), not an M6 oversight:
    enforcing *who* may call these two methods with a confirmed trust
    state is a responsibility of whatever capability-dispatch/authorization
    boundary sits in front of this manager, not of
    `KnowledgeLineageManager` itself. Restricting these two methods here
    would be an unevidenced redesign of already-committed M3 behavior. This
    test exists so the characteristic is proven intentional and understood,
    not a silent, undiscovered bypass."""
    manager = KnowledgeLineageManager()

    created = await manager.create_record(
        _record("rec-1", "v1", trust_state=KnowledgeTrustState.HUMAN_CONFIRMED)
    )
    assert created.trust_state == KnowledgeTrustState.HUMAN_CONFIRMED

    superseded = await manager.supersede(
        "rec-1",
        "tenant-a",
        _record("rec-1", "v2", parent_version_id="v1", trust_state=KnowledgeTrustState.HUMAN_CORRECTED),
    )
    assert superseded.trust_state == KnowledgeTrustState.HUMAN_CORRECTED
