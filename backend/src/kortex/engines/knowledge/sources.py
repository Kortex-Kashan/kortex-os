"""
KORTEX Knowledge Engine — Reference Source Provider (Milestone M5).

Implements `IKnowledgeSourceProvider` (`interfaces.py`, declared since M1,
unimplemented until now): `source_id(self) -> str` (sync, matching the
Protocol's own sync declaration — not "corrected" to async) and
`async def ingest(self, tenant_id: str) -> List[KnowledgeRecord]`.

Scope (per the audited M5 roadmap): this milestone delivers exactly one
concrete, fully synthetic/deterministic-in-shape provider — no real
Document/Connector/Recipe Engine I/O, no provider registry or
multi-provider dispatch of its own, and no production coupling to
`KnowledgeLineageManager` or `KnowledgeGraph` (`ingest()` only *produces*
`KnowledgeRecord`s; persisting them via `create_record()` is
`KnowledgeEngine.index_source()`'s job — implemented in `engine.py`, which
resolves a `source_id` string to a provider instance via its own small
internal registry and calls `create_record()` for each returned record;
`sources.py` itself never gains that responsibility).

`KnowledgeRecord` (frozen since M1) has no dedicated field recording which
source produced a record. Rather than modify that frozen model, provenance
is expressed entirely through existing fields: `created_by` is set to this
provider's own `source_id()`, `created_by_type` is `SERVICE_PRINCIPAL` (the
better-supported choice over `AGENT` — `SOURCE_EVIDENCE`'s own docstring
frames this trust state as pre-AI, purely mechanical ingestion, which maps
to a non-AI automated-service identity, not an AI agent), and the source is
additionally echoed inside `content["source_id"]`.

Every record this provider returns is deliberately: `trust_state
=SOURCE_EVIDENCE` (never any other value — an ingestion path that could
emit `HUMAN_CONFIRMED`/`HUMAN_CORRECTED`/`AI_CANDIDATE` directly would
bypass the M3/M6 promotion-gate invariant entirely), `created_by_type
=SERVICE_PRINCIPAL` (never `USER` — ingested, unreviewed content must never
masquerade as human-authored), and `parent_version_id=None` (a root
version only; this provider never produces a supersession — that is M3's
`supersede()` concern, invoked, if at all, by a later caller, not here).

Tenant isolation: `ingest(tenant_id)` holds no instance state and performs
no lookup keyed by anything other than the supplied `tenant_id` — every
returned record's own `tenant_id` field is always exactly the argument
`ingest()` was invoked with, never a cached or reused value from a prior
call. A malformed/empty `tenant_id` fails closed
(`KnowledgeSourceIngestionError`), never silently returning `[]`.

Determinism: repeated calls for the same `tenant_id` return the same
*shape* — the same count and field values, other than fresh
`record_id`/`version_id` per call (each `ingest()` call represents a new
ingestion event, so identity fields are new each time by design; this is a
deliberate choice, not an accident).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List

from kortex.engines.knowledge.exceptions import KnowledgeSourceIngestionError
from kortex.engines.knowledge.models import (
    KnowledgeActorType,
    KnowledgeRecord,
    KnowledgeRecordStatus,
    KnowledgeRecordType,
    KnowledgeTrustState,
)

_SOURCE_ID = "kortex.knowledge.source.reference"
_RECORD_COUNT = 2


class ReferenceSourceProvider:
    """Synthetic, deterministic-in-shape reference implementation of
    `IKnowledgeSourceProvider` (Milestone M5). No real external I/O."""

    def source_id(self) -> str:
        """Stable, non-empty identifier for this provider. Pure — no side
        effects, same value on every call."""
        return _SOURCE_ID

    async def ingest(self, tenant_id: str) -> List[KnowledgeRecord]:
        """Return `_RECORD_COUNT` fresh `SOURCE_EVIDENCE` `KnowledgeRecord`s
        scoped to `tenant_id`.

        Raises `KnowledgeSourceIngestionError` if `tenant_id` is falsy
        (empty string, or any other falsy value a caller might pass despite
        the declared `str` type) — ingestion fails closed on malformed
        identity input rather than silently returning an empty list.
        """
        if not tenant_id:
            raise KnowledgeSourceIngestionError(
                "ingest() requires a non-empty tenant_id; received "
                f"{tenant_id!r}."
            )

        source_id = self.source_id()
        now = datetime.now(timezone.utc)
        return [
            KnowledgeRecord(
                record_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                version_id=str(uuid.uuid4()),
                parent_version_id=None,
                record_type=KnowledgeRecordType.FACT,
                content={"source_id": source_id, "index": index},
                trust_state=KnowledgeTrustState.SOURCE_EVIDENCE,
                created_by=source_id,
                created_by_type=KnowledgeActorType.SERVICE_PRINCIPAL,
                created_at=now,
                status=KnowledgeRecordStatus.CURRENT,
            )
            for index in range(_RECORD_COUNT)
        ]
