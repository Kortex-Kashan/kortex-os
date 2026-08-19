"""
KORTEX Knowledge Engine — Knowledge Pack Loader (`KnowledgePackManager`).

Implements the load-bearing operation behind `IKnowledgeEngine.load_pack()`
(`interfaces.py:177-179`, frozen since M1: `async def load_pack(self, pack:
KnowledgePack) -> KnowledgePack`) — the last remaining piece of the
Knowledge Engine's ratified three-pillar scope (directed graph, search
coordinator, knowledge pack loader —
`docs/architecture/ARCHITECTURE_VERSION_1.0.md` §17) that had not yet been
built as of the post-M8 reconciliation audit.

Scope, reasoned from the frozen `load_pack(pack: KnowledgePack) ->
KnowledgePack` signature itself and from `docs/architecture/
knowledge_engine_implementation_spec.md` §7/§12 (this repository's only
authoritative source for pack semantics — no other document defines them):
the signature takes an already-identified `KnowledgePack` (whose
`storage_key`/`bucket_name`/`checksum_sha256`/`manifest` are already
populated) and returns the *same kind of object*, not a list of ingested
graph/lineage content. This is strong signature-level evidence that
"loading" here means verify-and-durably-register the pack itself, not
"parse `manifest` into `KnowledgeNode`/`KnowledgeRelationship` instances" —
no document anywhere defines an ontology-to-graph-node mapping format for a
pack manifest, and inventing one would be exactly the unsupported package
semantics the closure-work authorization explicitly forbids inventing.
Actual ontology ingestion from a loaded pack's manifest is therefore a
disclosed, deliberate scope boundary for this component, not a silent
omission — parallel to `sources.py`'s own disclosed M5 boundary (ingestion
produces evidence; wiring it into lineage is a separate, later
responsibility) and to `search.py`'s disclosed `graph_relationships`
boundary.

Integrity verification (spec §12: "Zero direct filesystem or database
calls" — exclusive `StorageEngine` use) is the real gate this component
provides:
1. `pack.manifest` must be non-empty. `KnowledgePack.manifest` is an
   untyped `Dict[str, Any]` (Milestone M1 design — no `KortexAssetManifest`
   schema implementation exists anywhere in this codebase to validate
   against, per `models.py`'s own documented rationale for leaving it
   untyped), so this is a structural check only, never a fabricated
   key-schema requirement.
2. The object referenced by `pack.bucket_name`/`pack.storage_key` must
   exist in the configured `IObjectStore` (`KnowledgePackNotFoundError` if
   not — wraps `ResourceNotFoundError` from `kortex.core.exceptions`).
3. Its retrieved byte length must equal `pack.size_bytes`, and its SHA-256
   digest must equal `pack.checksum_sha256` (`KnowledgePackIntegrityError`
   on either mismatch) — a corrupted or tampered-with pack is never
   registered as loaded.

Disclosed, deliberate scope boundary: `pack.digital_signature` (`Optional[str]`,
Milestone M1) is stored as-is but is **not** cryptographically verified by
this component. No specification anywhere in this repository names a
signing algorithm, a key-distribution/trust-root model, or a capability for
verifying a Knowledge Pack's signature specifically (Security Engine's own
`kortex.security.signature.verify` capability exists for a different,
unrelated purpose, and invoking it here would require caller-identity/
session-token context that `load_pack(pack)`'s own frozen signature does not
carry). Fabricating a verification scheme with no evidentiary basis would
violate the explicit "do not invent unsupported package semantics"
constraint this work was authorized under. If cryptographic pack-signature
verification is required, it needs its own authoritative specification
first.

Tenant isolation and duplicate handling: identity is `(tenant_id, asset_id)`
— matching every other Knowledge Engine manager's own composite-key
convention exactly (`graph.py`, `lineage.py`, `annotations.py`). Loading the
same `asset_id` for two different tenants is two independent, unrelated
operations; loading the same `(tenant_id, asset_id)` twice raises
`KnowledgeDuplicatePackError` rather than silently overwriting or
re-verifying — a pack, once loaded, is immutable for that tenant.

Concurrency: this milestone's own audit re-confirmed the Milestone M7
lesson (introducing a genuine `await` suspension point where none existed
before can turn a check-then-act sequence into a real TOCTOU race). This
manager therefore uses the identical `asyncio.Lock`-per-instance pattern
`KnowledgeLineageManager`/`KnowledgeAnnotationManager` already established
from the start, serializing the full duplicate-check-through-persist-
through-memory-write sequence, rather than discovering the same race
adversarially a second time.

Persistence follows the exact `KnowledgeRecordRow`/`KnowledgeAnnotationRow`
pattern (`persistence.py`, Milestone M7): optional `data_store` constructor
argument, `load()` to hydrate from a prior process's durable state
(explicitly called by `KnowledgeEngine.initialize()`), persist-before-
memory-write ordering, and any non-domain storage failure normalized to
`KnowledgePersistenceError` via the identical `_execute_in_transaction`
helper pattern used by `lineage.py`/`annotations.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.exceptions import ResourceNotFoundError
from kortex.engines.knowledge.exceptions import (
    KnowledgeDuplicatePackError,
    KnowledgeEngineError,
    KnowledgeInvalidManifestError,
    KnowledgePackIntegrityError,
    KnowledgePackNotFoundError,
    KnowledgePersistenceError,
)
from kortex.engines.knowledge.models import KnowledgePack
from kortex.engines.knowledge.persistence import KnowledgePackRow
from kortex.engines.storage.interfaces import IDataStore, IObjectStore


class KnowledgePackManager:
    """Tenant-scoped Knowledge Pack loader/verifier. Optionally durable via
    `IDataStore`, matching `KnowledgeLineageManager`/`KnowledgeAnnotationManager`."""

    def __init__(self, object_store: IObjectStore, data_store: Optional[IDataStore] = None) -> None:
        self._object_store = object_store
        self._data_store = data_store
        self._packs: Dict[Tuple[str, str], KnowledgePack] = {}
        # See module docstring's concurrency note.
        self._lock = asyncio.Lock()

    async def _execute_in_transaction(self, action: Any) -> Any:
        """Run `action` via the configured `IDataStore`, normalizing any
        non-`KnowledgeEngineError` failure into `KnowledgePersistenceError`
        (original exception preserved as `__cause__`)."""
        assert self._data_store is not None
        try:
            return await self._data_store.execute_in_transaction(action)
        except KnowledgeEngineError:
            raise
        except Exception as exc:
            raise KnowledgePersistenceError(f"Knowledge pack persistence operation failed: {exc}") from exc

    async def load(self) -> None:
        """Hydrate in-memory state from the configured `IDataStore`. No-op
        if no `data_store` was provided at construction. Must be called
        explicitly after construction (`__init__` cannot itself `await`)."""
        if self._data_store is None:
            return

        async def _action(session: AsyncSession) -> List[KnowledgePackRow]:
            result = await session.execute(select(KnowledgePackRow))
            return list(result.scalars().all())

        async with self._lock:
            rows = await self._execute_in_transaction(_action)
            for row in rows:
                pack = KnowledgePack(
                    asset_id=row.asset_id,
                    tenant_id=row.tenant_id,
                    manifest=dict(row.manifest),
                    checksum_sha256=row.checksum_sha256,
                    digital_signature=row.digital_signature,
                    size_bytes=row.size_bytes,
                    mime_type=row.mime_type,
                    storage_key=row.storage_key,
                    bucket_name=row.bucket_name,
                )
                self._packs[(row.tenant_id, row.asset_id)] = pack

    async def _persist_pack(self, pack: KnowledgePack, loaded_at: datetime) -> None:
        """Durably insert one new row. No-op if no `data_store` was
        configured. Raises on failure — the caller relies on it to decide
        whether the in-memory mutation may proceed."""
        if self._data_store is None:
            return

        async def _action(session: AsyncSession) -> None:
            session.add(
                KnowledgePackRow(
                    id=str(uuid.uuid4()),
                    tenant_id=pack.tenant_id,
                    asset_id=pack.asset_id,
                    manifest=dict(pack.manifest),
                    checksum_sha256=pack.checksum_sha256,
                    digital_signature=pack.digital_signature,
                    size_bytes=pack.size_bytes,
                    mime_type=pack.mime_type,
                    storage_key=pack.storage_key,
                    bucket_name=pack.bucket_name,
                    loaded_at=loaded_at,
                )
            )

        await self._execute_in_transaction(_action)

    async def load_pack(self, pack: KnowledgePack) -> KnowledgePack:
        """Verify and durably register `pack`. See module docstring for the
        exact verification sequence and the disclosed digital-signature
        scope boundary.

        Raises:
            KnowledgeDuplicatePackError: `(tenant_id, asset_id)` was already loaded.
            KnowledgeInvalidManifestError: `pack.manifest` is empty.
            KnowledgePackNotFoundError: no object exists at
                `pack.bucket_name`/`pack.storage_key`.
            KnowledgePackIntegrityError: the retrieved object's byte length
                or SHA-256 digest does not match `pack.size_bytes`/
                `pack.checksum_sha256`.
        """
        async with self._lock:
            key = (pack.tenant_id, pack.asset_id)
            if key in self._packs:
                raise KnowledgeDuplicatePackError(
                    f"Knowledge pack '{pack.asset_id}' has already been loaded for tenant "
                    f"'{pack.tenant_id}'."
                )

            if not pack.manifest:
                raise KnowledgeInvalidManifestError(
                    f"Knowledge pack '{pack.asset_id}' (tenant '{pack.tenant_id}') has an empty manifest."
                )

            try:
                data = await self._object_store.get_object(pack.bucket_name, pack.storage_key)
            except ResourceNotFoundError as exc:
                raise KnowledgePackNotFoundError(
                    f"Knowledge pack '{pack.asset_id}' object not found at "
                    f"bucket='{pack.bucket_name}' storage_key='{pack.storage_key}'."
                ) from exc

            if len(data) != pack.size_bytes:
                raise KnowledgePackIntegrityError(
                    f"Knowledge pack '{pack.asset_id}' size mismatch: expected {pack.size_bytes} bytes, "
                    f"retrieved {len(data)} bytes."
                )
            computed_checksum = hashlib.sha256(data).hexdigest()
            if computed_checksum != pack.checksum_sha256:
                raise KnowledgePackIntegrityError(
                    f"Knowledge pack '{pack.asset_id}' checksum mismatch: expected "
                    f"'{pack.checksum_sha256}', computed '{computed_checksum}'."
                )

            loaded_at = datetime.now(timezone.utc)
            await self._persist_pack(pack, loaded_at)

            self._packs[key] = pack
            return pack

    async def get_loaded_pack(self, asset_id: str, tenant_id: str) -> Optional[KnowledgePack]:
        """Return the loaded pack for `(tenant_id, asset_id)`, or `None` if
        it was never loaded (a normal, non-exceptional outcome, matching
        `KnowledgeLineageManager.get_current`'s own `Optional`-return
        convention)."""
        return self._packs.get((tenant_id, asset_id))

    def list_loaded_packs(self, tenant_id: str) -> List[KnowledgePack]:
        """Return every pack loaded for `tenant_id`. Additive helper (not on
        any frozen Protocol), matching `graph.py::list_nodes`/
        `lineage.py::list_current_records`'s own precedent for a
        search/diagnostics-facing enumeration method. Synchronous — reads
        only already-hydrated in-memory state, no I/O."""
        return [pack for (t, _asset_id), pack in self._packs.items() if t == tenant_id]
