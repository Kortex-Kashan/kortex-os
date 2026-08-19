"""Unit tests for the Knowledge Pack Loader (`KnowledgePackManager`).

Mirrors `test_knowledge_persistence.py`'s established fixture style (real
SQLite-backed `RelationalDataStore` via `DatabaseEngineManager`, a real
`BlobObjectStore`/`LocalFileStore` pair rather than a fake object store,
and the same `_FailingDataStore` simulated-failure pattern) rather than
mocking storage — consistent with every other Knowledge Engine test file.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import select

from kortex.core.db import DatabaseEngineManager
from kortex.engines.knowledge.exceptions import (
    KnowledgeDuplicatePackError,
    KnowledgeInvalidManifestError,
    KnowledgePackIntegrityError,
    KnowledgePackNotFoundError,
    KnowledgePersistenceError,
)
from kortex.engines.knowledge.models import KnowledgePack
from kortex.engines.knowledge.packs import KnowledgePackManager
from kortex.engines.knowledge.persistence import KnowledgePackRow
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.storage.stores.file_store import LocalFileStore
from kortex.engines.storage.stores.object_store import BlobObjectStore


class _FailingDataStore:
    """Simulates an `IDataStore` operational failure — mirrors the
    established `_FailingDataStore` pattern already used in
    `test_knowledge_persistence.py`/`test_capability_dispatch.py`."""

    async def get_session(self) -> Any:  # pragma: no cover - not exercised by these tests
        raise AssertionError("get_session should not be called directly")

    async def execute_in_transaction(self, action: Any) -> Any:
        raise RuntimeError("simulated storage failure")


async def _build_data_store(tmp_path: Path, name: str = "packs") -> RelationalDataStore:
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{tmp_path}/{name}.db")
    await db_manager.create_all_tables()
    return RelationalDataStore(db_manager)


def _build_object_store(tmp_path: Path, name: str = "objects") -> BlobObjectStore:
    file_store = LocalFileStore(str(tmp_path / name))
    return BlobObjectStore(file_store)


def _pack(
    asset_id: str,
    data: bytes,
    tenant_id: str = "tenant-a",
    manifest: Optional[Dict[str, Any]] = None,
    bucket_name: str = "knowledge",
    storage_key: Optional[str] = None,
    digital_signature: Optional[str] = None,
    size_bytes: Optional[int] = None,
    checksum_sha256: Optional[str] = None,
) -> KnowledgePack:
    return KnowledgePack(
        asset_id=asset_id,
        tenant_id=tenant_id,
        manifest=manifest if manifest is not None else {"name": "hr-ontology"},
        checksum_sha256=checksum_sha256 if checksum_sha256 is not None else hashlib.sha256(data).hexdigest(),
        digital_signature=digital_signature,
        size_bytes=size_bytes if size_bytes is not None else len(data),
        mime_type="application/x-kortex-knowledge",
        storage_key=storage_key or f"packs/{asset_id}.kortex-knowledge",
        bucket_name=bucket_name,
    )


# -- Successful load ----------------------------------------------------------


@pytest.mark.asyncio
async def test_load_pack_succeeds_and_registers_pack(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    data = b"ontology-payload-bytes"
    pack = _pack("pack-1", data)
    await object_store.put_object(pack.bucket_name, pack.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store)
    loaded = await manager.load_pack(pack)

    assert loaded.asset_id == "pack-1"
    fetched = await manager.get_loaded_pack("pack-1", "tenant-a")
    assert fetched is not None
    assert fetched.checksum_sha256 == pack.checksum_sha256


@pytest.mark.asyncio
async def test_load_pack_with_no_data_store_is_in_memory_only(tmp_path: Path) -> None:
    """Matches M3/M4/M7's own "no data_store" backward-compatible contract."""
    object_store = _build_object_store(tmp_path)
    data = b"in-memory-only"
    pack = _pack("pack-mem", data)
    await object_store.put_object(pack.bucket_name, pack.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store)
    await manager.load()  # must be a safe no-op with no data_store
    await manager.load_pack(pack)
    assert await manager.get_loaded_pack("pack-mem", "tenant-a") is not None


# -- Duplicate / tenant isolation ----------------------------------------------


@pytest.mark.asyncio
async def test_load_pack_rejects_duplicate_for_same_tenant(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    data = b"dup-bytes"
    pack = _pack("pack-dup", data)
    await object_store.put_object(pack.bucket_name, pack.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store)
    await manager.load_pack(pack)
    with pytest.raises(KnowledgeDuplicatePackError):
        await manager.load_pack(pack)


@pytest.mark.asyncio
async def test_load_pack_same_asset_id_different_tenants_both_succeed(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    data = b"shared-asset-id-bytes"
    pack_a = _pack("pack-shared", data, tenant_id="tenant-a", storage_key="packs/shared-a.kortex-knowledge")
    pack_b = _pack("pack-shared", data, tenant_id="tenant-b", storage_key="packs/shared-b.kortex-knowledge")
    await object_store.put_object(pack_a.bucket_name, pack_a.storage_key, data)
    await object_store.put_object(pack_b.bucket_name, pack_b.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store)
    await manager.load_pack(pack_a)
    await manager.load_pack(pack_b)  # must not collide with tenant-a's identical asset_id

    assert await manager.get_loaded_pack("pack-shared", "tenant-a") is not None
    assert await manager.get_loaded_pack("pack-shared", "tenant-b") is not None


# -- Manifest / integrity verification -----------------------------------------


@pytest.mark.asyncio
async def test_load_pack_rejects_empty_manifest(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    data = b"empty-manifest-bytes"
    pack = _pack("pack-empty-manifest", data, manifest={})
    await object_store.put_object(pack.bucket_name, pack.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store)
    with pytest.raises(KnowledgeInvalidManifestError):
        await manager.load_pack(pack)


@pytest.mark.asyncio
async def test_load_pack_rejects_missing_object(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    pack = _pack("pack-missing", b"never-actually-stored")

    manager = KnowledgePackManager(object_store=object_store)
    with pytest.raises(KnowledgePackNotFoundError):
        await manager.load_pack(pack)


@pytest.mark.asyncio
async def test_load_pack_rejects_checksum_mismatch(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    real_data = b"real-payload"
    tampered_data = b"tampered-payload-different-length!!"
    pack = _pack("pack-tampered", real_data)  # checksum computed from real_data
    # Storage actually holds different bytes than the pack's own checksum describes.
    await object_store.put_object(pack.bucket_name, pack.storage_key, tampered_data)

    manager = KnowledgePackManager(object_store=object_store)
    with pytest.raises(KnowledgePackIntegrityError):
        await manager.load_pack(pack)
    assert await manager.get_loaded_pack("pack-tampered", "tenant-a") is None


@pytest.mark.asyncio
async def test_load_pack_rejects_size_mismatch(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    data = b"correct-size-data"
    pack = _pack("pack-wrong-size", data, size_bytes=len(data) + 1)  # fabricated wrong size
    await object_store.put_object(pack.bucket_name, pack.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store)
    with pytest.raises(KnowledgePackIntegrityError):
        await manager.load_pack(pack)


@pytest.mark.asyncio
async def test_load_pack_corrupted_or_duplicate_never_registered(tmp_path: Path) -> None:
    """A failed load (corrupt/duplicate/missing) must never leave a partial
    registration behind — the next load attempt for the same identity must
    see a clean slate to retry against (once the underlying problem, e.g. a
    corrupted object, is fixed)."""
    object_store = _build_object_store(tmp_path)
    real_data = b"clean-payload"
    pack = _pack("pack-retry", real_data)
    await object_store.put_object(pack.bucket_name, pack.storage_key, b"wrong-bytes-entirely")

    manager = KnowledgePackManager(object_store=object_store)
    with pytest.raises(KnowledgePackIntegrityError):
        await manager.load_pack(pack)

    # Fix the underlying object and retry -- must succeed, not raise a false duplicate error.
    await object_store.delete_object(pack.bucket_name, pack.storage_key)
    await object_store.put_object(pack.bucket_name, pack.storage_key, real_data)
    loaded = await manager.load_pack(pack)
    assert loaded.asset_id == "pack-retry"


# -- Disclosed scope boundary: digital_signature is stored but not verified ---


@pytest.mark.asyncio
async def test_load_pack_does_not_cryptographically_verify_digital_signature(tmp_path: Path) -> None:
    """Proves the documented disclosed boundary (see `packs.py` module
    docstring): a pack with an arbitrary, non-cryptographic
    `digital_signature` string still loads successfully as long as its
    checksum/size/manifest are valid -- no signature verification is
    performed by this milestone."""
    object_store = _build_object_store(tmp_path)
    data = b"signed-looking-payload"
    pack_no_sig = _pack("pack-no-sig", data, digital_signature=None, storage_key="packs/no-sig.kortex-knowledge")
    pack_garbage_sig = _pack(
        "pack-garbage-sig", data, digital_signature="not-a-real-signature", storage_key="packs/garbage-sig.kortex-knowledge"
    )
    await object_store.put_object(pack_no_sig.bucket_name, pack_no_sig.storage_key, data)
    await object_store.put_object(pack_garbage_sig.bucket_name, pack_garbage_sig.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store)
    await manager.load_pack(pack_no_sig)
    await manager.load_pack(pack_garbage_sig)  # must not raise despite a nonsensical signature

    assert await manager.get_loaded_pack("pack-garbage-sig", "tenant-a") is not None


# -- Persistence: durability + reload ------------------------------------------


@pytest.mark.asyncio
async def test_load_pack_persists_and_survives_reload(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    data_store = await _build_data_store(tmp_path)
    data = b"durable-payload"
    pack = _pack("pack-durable", data)
    await object_store.put_object(pack.bucket_name, pack.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store, data_store=data_store)
    await manager.load_pack(pack)

    reloaded_manager = KnowledgePackManager(object_store=object_store, data_store=data_store)
    await reloaded_manager.load()

    fetched = await reloaded_manager.get_loaded_pack("pack-durable", "tenant-a")
    assert fetched is not None
    assert fetched.checksum_sha256 == pack.checksum_sha256
    assert fetched.manifest == pack.manifest


@pytest.mark.asyncio
async def test_pack_tenant_isolation_preserved_across_reload(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    data_store = await _build_data_store(tmp_path, "tenant_iso")
    data = b"tenant-scoped-payload"
    pack_a = _pack("pack-x", data, tenant_id="tenant-a", storage_key="packs/x-a.kortex-knowledge")
    pack_b = _pack("pack-x", data, tenant_id="tenant-b", storage_key="packs/x-b.kortex-knowledge")
    await object_store.put_object(pack_a.bucket_name, pack_a.storage_key, data)
    await object_store.put_object(pack_b.bucket_name, pack_b.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store, data_store=data_store)
    await manager.load_pack(pack_a)
    await manager.load_pack(pack_b)

    reloaded_manager = KnowledgePackManager(object_store=object_store, data_store=data_store)
    await reloaded_manager.load()

    assert (await reloaded_manager.get_loaded_pack("pack-x", "tenant-a")).tenant_id == "tenant-a"
    assert (await reloaded_manager.get_loaded_pack("pack-x", "tenant-b")).tenant_id == "tenant-b"


@pytest.mark.asyncio
async def test_load_pack_persistence_failure_leaves_no_partial_state(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    data = b"failure-payload"
    pack = _pack("pack-fail", data)
    await object_store.put_object(pack.bucket_name, pack.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store, data_store=_FailingDataStore())
    with pytest.raises(KnowledgePersistenceError) as exc_info:
        await manager.load_pack(pack)
    assert isinstance(exc_info.value.__cause__, RuntimeError)

    assert await manager.get_loaded_pack("pack-fail", "tenant-a") is None
    assert manager._packs == {}


# -- Concurrency: same class of TOCTOU race M7 already taught us to guard against --


@pytest.mark.asyncio
async def test_concurrent_load_pack_for_same_identity_does_not_duplicate(tmp_path: Path) -> None:
    """Same class of finding as M7's `create_record`/`add_annotation` race
    regression tests: two concurrent `load_pack()` calls for the same
    `(tenant_id, asset_id)` must not both succeed, and the durable store
    must end with exactly one row."""
    object_store = _build_object_store(tmp_path)
    data_store = await _build_data_store(tmp_path, "race_pack")
    data = b"race-payload"
    pack = _pack("pack-race", data)
    await object_store.put_object(pack.bucket_name, pack.storage_key, data)

    manager = KnowledgePackManager(object_store=object_store, data_store=data_store)

    results = await asyncio.gather(
        manager.load_pack(pack),
        manager.load_pack(pack),
        return_exceptions=True,
    )
    successes = [r for r in results if isinstance(r, KnowledgePack)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], KnowledgeDuplicatePackError)

    async def _action(session: Any) -> List[Any]:
        result = await session.execute(select(KnowledgePackRow))
        return list(result.scalars().all())

    rows = await data_store.execute_in_transaction(_action)
    matching = [r for r in rows if r.asset_id == "pack-race" and r.tenant_id == "tenant-a"]
    assert len(matching) == 1  # never two rows for the same identity


# -- Enumeration helper ---------------------------------------------------------


@pytest.mark.asyncio
async def test_list_loaded_packs_scoped_to_tenant(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    manager = KnowledgePackManager(object_store=object_store)

    for i, tenant in enumerate(["tenant-a", "tenant-a", "tenant-b"]):
        data = f"payload-{i}".encode()
        pack = _pack(f"pack-{i}", data, tenant_id=tenant, storage_key=f"packs/list-{i}.kortex-knowledge")
        await object_store.put_object(pack.bucket_name, pack.storage_key, data)
        await manager.load_pack(pack)

    assert len(manager.list_loaded_packs("tenant-a")) == 2
    assert len(manager.list_loaded_packs("tenant-b")) == 1
    assert manager.list_loaded_packs("tenant-nonexistent") == []


@pytest.mark.asyncio
async def test_get_loaded_pack_returns_none_when_not_loaded(tmp_path: Path) -> None:
    object_store = _build_object_store(tmp_path)
    manager = KnowledgePackManager(object_store=object_store)
    assert await manager.get_loaded_pack("nonexistent", "tenant-a") is None
