"""
KORTEX Knowledge Engine — Record Lineage, Supersession & Trust Promotion
(Milestone M3 lineage/supersession; Milestone M6 trust promotion).

Implements `IKnowledgeRecordManager` (`interfaces.py`) for `KnowledgeRecord`
version management: creation, current-version lookup, full lineage
reconstruction, atomic supersession, and (Milestone M6) trust-state
promotion.

In-memory only, tenant-scoped, mirroring the storage-agnostic conventions
already established by Milestone M2's `KnowledgeGraph` — persistence is
explicitly Milestone M7, not here. `IKnowledgeRecordManager`'s methods are
declared `async` in the committed Protocol (unlike `IKnowledgeGraph`'s
synchronous ones), so this implementation is `async` to match that
contract exactly, even though its internals do no actual I/O yet.

`promote()` (Milestone M6) enforces that only a `USER`-type actor may
promote a record's trust state, and only from an unconfirmed state
(`SOURCE_EVIDENCE`/`AI_CANDIDATE`) to a confirmed one
(`HUMAN_CONFIRMED`/`HUMAN_CORRECTED`) — `AGENT`/`SERVICE_PRINCIPAL` are
always denied, and the actor-type check runs *before* any record lookup,
so a non-`USER` caller cannot use this method as an existence oracle
(an unknown `record_id` and a real one are rejected identically for a
denied actor). A successful promotion replaces the current version's
stored `KnowledgeRecord` with a `model_copy(update={"trust_state": ...})`
copy at the *same* `version_id` — it never creates a new lineage version
(that is `supersede()`'s concern; `KnowledgeAnnotationType.CORRECTION`'s
own docstring assigns version-creating corrections to `supersede()`, not
to `promote()`), so `get_lineage()`'s chain is unaffected by a promotion.
Promoting an already-confirmed record, or targeting anything other than a
confirmed state, is rejected as an invalid transition — not silently
accepted as a no-op.

Documented residual, found during Milestone M6's adversarial audit and
deliberately NOT changed here: `create_record()` and `supersede()` accept
a caller-supplied `trust_state` — including `HUMAN_CONFIRMED`/
`HUMAN_CORRECTED` — with no actor-type check at all, unlike `promote()`.
This is not an oversight this milestone silently left in place; it follows
directly from the committed `IKnowledgeRecordManager` Protocol itself,
whose `create_record`/`supersede` signatures carry no `actor_id`/
`actor_type` parameters at all (`promote()`'s signature uniquely does) —
a deliberate Milestone M1 API asymmetry that places responsibility for
*who* may call `create_record`/`supersede` with a confirmed trust state
outside `KnowledgeLineageManager` entirely (a capability-dispatch/Security
Engine boundary, mirroring the pattern already established for the
unrelated Kernel capability-enforcement boundary elsewhere in this
codebase), not inside this milestone's scope. Restricting
`create_record`/`supersede` to unconfirmed-only trust states here would be
an unevidenced redesign of already-committed M3 behavior, not a proven M6
requirement — see `test_create_record_and_supersede_accept_confirmed_trust_state_directly_by_design`
in `test_knowledge_lineage.py` for the explicit, intentional proof of this
characteristic.

`KnowledgeRecord` is frozen (Milestone M1 design, unchanged here).
`supersede` therefore never mutates a stored record in place — it replaces
this manager's own internal reference to the old current version with a
newly-constructed `KnowledgeRecord` (via `model_copy`) carrying
`status=SUPERSEDED` and `successor_version_id` set, then installs
`new_version` (normalized to `status=CURRENT`, regardless of what the
caller set) as the new current. The caller's own object references are
never mutated.

Tenant isolation is a structural invariant: internal storage is keyed by
`(tenant_id, record_id)` / `(tenant_id, record_id, version_id)`, matching
Milestone M2's established pattern — identical `record_id` values across
tenants never collide.

`get_current` returns `Optional[KnowledgeRecord]` — per the interface's own
declared return type, a missing record is a normal, non-exceptional
outcome for this one method. `get_lineage` and `supersede` instead raise
`KnowledgeRecordNotFoundError` for a wholly unknown `record_id`, since
asking for the lineage of, or attempting to supersede, something that was
never created is a genuine caller error, not a normal "not found yet"
case.

`create_record` only ever creates the *initial* version of a record chain
(`parent_version_id` must be `None`) — subsequent versions arrive
exclusively through `supersede`, which requires `new_version.parent_version_id`
to exactly match the actual current version's `version_id`, rejecting
stale or concurrent-modification attempts. Both invariants are enforced at
write time specifically so `get_lineage`'s chain walk (following
`parent_version_id` links) can never encounter a dangling reference.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from kortex.engines.knowledge.exceptions import (
    KnowledgeInvalidTrustTransitionError,
    KnowledgeLineageConsistencyError,
    KnowledgePromotionNotAuthorizedError,
    KnowledgeRecordNotFoundError,
)
from kortex.engines.knowledge.models import (
    KnowledgeActorType,
    KnowledgeRecord,
    KnowledgeRecordStatus,
    KnowledgeTrustState,
)

_CONFIRMED_TRUST_STATES = frozenset(
    {KnowledgeTrustState.HUMAN_CONFIRMED, KnowledgeTrustState.HUMAN_CORRECTED}
)


class KnowledgeLineageManager:
    """In-memory, tenant-scoped record lineage and supersession manager (Milestone M3)."""

    def __init__(self) -> None:
        # (tenant_id, record_id, version_id) -> KnowledgeRecord — every version ever created.
        self._versions: Dict[Tuple[str, str, str], KnowledgeRecord] = {}
        # (tenant_id, record_id) -> version_id of the current version.
        self._current_version_id: Dict[Tuple[str, str], str] = {}

    async def create_record(self, record: KnowledgeRecord) -> KnowledgeRecord:
        """Create the initial version of a new record identity.

        Raises:
            KnowledgeLineageConsistencyError: `(tenant_id, record_id)`
                already has a version chain (use `supersede` instead), or
                `record.parent_version_id` is not `None` (only `supersede`
                may introduce a non-root version).
        """
        record_key = (record.tenant_id, record.record_id)
        if record_key in self._current_version_id:
            raise KnowledgeLineageConsistencyError(
                f"Record '{record.record_id}' already has a version chain for tenant "
                f"'{record.tenant_id}' — use supersede() to add a new version, not create_record()."
            )
        if record.parent_version_id is not None:
            raise KnowledgeLineageConsistencyError(
                f"create_record() creates only the initial version of a record chain; "
                f"'{record.record_id}' was supplied with parent_version_id="
                f"{record.parent_version_id!r} — use supersede() for subsequent versions."
            )

        current_record = (
            record
            if record.status == KnowledgeRecordStatus.CURRENT
            else record.model_copy(update={"status": KnowledgeRecordStatus.CURRENT})
        )
        version_key = (current_record.tenant_id, current_record.record_id, current_record.version_id)
        self._versions[version_key] = current_record
        self._current_version_id[record_key] = current_record.version_id
        return current_record

    async def get_current(self, record_id: str, tenant_id: str) -> Optional[KnowledgeRecord]:
        """Return the current version of `record_id`, scoped to `tenant_id`,
        or `None` if the record does not exist — a normal outcome per this
        method's own `Optional` contract, not an error."""
        current_version_id = self._current_version_id.get((tenant_id, record_id))
        if current_version_id is None:
            return None
        return self._versions[(tenant_id, record_id, current_version_id)]

    async def get_lineage(self, record_id: str, tenant_id: str) -> List[KnowledgeRecord]:
        """Return the full ordered version history of `record_id`, from the
        earliest ancestor to the current version, scoped to `tenant_id`.

        Raises `KnowledgeRecordNotFoundError` if `record_id` was never
        created for `tenant_id` at all.
        """
        record_key = (tenant_id, record_id)
        current_version_id = self._current_version_id.get(record_key)
        if current_version_id is None:
            raise KnowledgeRecordNotFoundError(f"Record '{record_id}' not found for tenant '{tenant_id}'.")

        chain: List[KnowledgeRecord] = []
        version_id: Optional[str] = current_version_id
        while version_id is not None:
            version = self._versions[(tenant_id, record_id, version_id)]
            chain.append(version)
            version_id = version.parent_version_id
        chain.reverse()
        return chain

    async def supersede(
        self, record_id: str, tenant_id: str, new_version: KnowledgeRecord
    ) -> KnowledgeRecord:
        """Atomically supersede the current version of `record_id` with
        `new_version`.

        Raises:
            KnowledgeRecordNotFoundError: `record_id` was never created for `tenant_id`.
            KnowledgeLineageConsistencyError: `new_version`'s `record_id`/
                `tenant_id` do not match the call's own `record_id`/
                `tenant_id` arguments, or `new_version.parent_version_id`
                does not match the actual current version's `version_id`
                (a stale or concurrent-modification supersession attempt).
        """
        record_key = (tenant_id, record_id)
        current_version_id = self._current_version_id.get(record_key)
        if current_version_id is None:
            raise KnowledgeRecordNotFoundError(f"Record '{record_id}' not found for tenant '{tenant_id}'.")

        if new_version.record_id != record_id or new_version.tenant_id != tenant_id:
            raise KnowledgeLineageConsistencyError(
                f"new_version identity (record_id={new_version.record_id!r}, "
                f"tenant_id={new_version.tenant_id!r}) does not match the call's own "
                f"record_id={record_id!r}/tenant_id={tenant_id!r}."
            )

        if new_version.parent_version_id != current_version_id:
            raise KnowledgeLineageConsistencyError(
                f"Cannot supersede record '{record_id}' for tenant '{tenant_id}': "
                f"new_version.parent_version_id={new_version.parent_version_id!r} does not match "
                f"the actual current version_id={current_version_id!r}."
            )

        if (tenant_id, record_id, new_version.version_id) in self._versions:
            raise KnowledgeLineageConsistencyError(
                f"version_id={new_version.version_id!r} already exists in the lineage of record "
                f"'{record_id}' for tenant '{tenant_id}' — version identifiers must be unique "
                f"within a chain (reusing one would silently overwrite that stored version)."
            )

        old_current = self._versions[(tenant_id, record_id, current_version_id)]
        superseded_old_current = old_current.model_copy(
            update={
                "status": KnowledgeRecordStatus.SUPERSEDED,
                "successor_version_id": new_version.version_id,
            }
        )
        self._versions[(tenant_id, record_id, current_version_id)] = superseded_old_current

        current_new_version = (
            new_version
            if new_version.status == KnowledgeRecordStatus.CURRENT
            else new_version.model_copy(update={"status": KnowledgeRecordStatus.CURRENT})
        )
        self._versions[(tenant_id, record_id, current_new_version.version_id)] = current_new_version
        self._current_version_id[record_key] = current_new_version.version_id
        return current_new_version

    async def promote(
        self,
        record_id: str,
        tenant_id: str,
        actor_id: str,
        actor_type: KnowledgeActorType,
        new_trust_state: KnowledgeTrustState,
    ) -> KnowledgeRecord:
        """Promote the current version of `record_id` from an unconfirmed
        trust state (`SOURCE_EVIDENCE`/`AI_CANDIDATE`) to a confirmed one
        (`HUMAN_CONFIRMED`/`HUMAN_CORRECTED`).

        Raises:
            KnowledgePromotionNotAuthorizedError: `actor_type` is not
                `USER`. Checked before any record lookup — a non-`USER`
                caller cannot distinguish an unknown `record_id` from a
                real one by the error raised.
            KnowledgeRecordNotFoundError: `record_id` was never created for
                `tenant_id` (only reached for a `USER` actor).
            KnowledgeInvalidTrustTransitionError: `new_trust_state` is not
                a confirmed state, or the record's current trust state is
                already confirmed (promotion is not idempotent and does
                not apply to an already-confirmed record).

        On success, replaces the stored current version with a
        `model_copy(update={"trust_state": new_trust_state})` copy at the
        same `version_id` — `actor_id` is used only for this authorization
        decision; `KnowledgeRecord` (frozen, Milestone M1) has no field
        recording who promoted it.
        """
        if actor_type != KnowledgeActorType.USER:
            raise KnowledgePromotionNotAuthorizedError(
                f"Trust-state promotion of record '{record_id}' requires a USER actor; "
                f"actor_type={actor_type.value!r} is not permitted."
            )

        record_key = (tenant_id, record_id)
        current_version_id = self._current_version_id.get(record_key)
        if current_version_id is None:
            raise KnowledgeRecordNotFoundError(f"Record '{record_id}' not found for tenant '{tenant_id}'.")

        current_record = self._versions[(tenant_id, record_id, current_version_id)]

        if new_trust_state not in _CONFIRMED_TRUST_STATES:
            raise KnowledgeInvalidTrustTransitionError(
                f"Cannot promote record '{record_id}' to trust_state={new_trust_state.value!r}: "
                f"promote() may only target a confirmed trust state "
                f"(HUMAN_CONFIRMED/HUMAN_CORRECTED)."
            )
        if current_record.trust_state in _CONFIRMED_TRUST_STATES:
            raise KnowledgeInvalidTrustTransitionError(
                f"Record '{record_id}' is already at trust_state={current_record.trust_state.value!r}; "
                f"promote() only applies to an unconfirmed record (SOURCE_EVIDENCE/AI_CANDIDATE)."
            )

        promoted_record = current_record.model_copy(update={"trust_state": new_trust_state})
        self._versions[(tenant_id, record_id, current_version_id)] = promoted_record
        return promoted_record
