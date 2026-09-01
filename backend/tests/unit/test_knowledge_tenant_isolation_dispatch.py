"""M7.5-W1 adversarial tenant-isolation coverage for the Knowledge Engine.

Mirrors `test_document_tenant_isolation_dispatch.py`'s (M7.4) harness and
intent exactly, and reuses `test_knowledge_capability_dispatch.py`'s
(Slice 4.7) `_tenant`/`_build_kernel`/`_boot_kernel`/`_seed_principal`/
`_grant_role_permission`/`_issue_token` helper pattern verbatim, driven
against the real, production `KnowledgeEngine` through real Kernel
dispatch (`kernel.invoke_capability`), not direct Python method calls --
proving the fix at the actual Capability Enforcement Boundary, per the
M7.5 master implementation prompt's explicit instruction.

Every test below exercises a capability whose handler gained a `principal`
parameter in M7.5-W1 (`kortex.knowledge.query.search`,
`kortex.knowledge.graph.traverse`, `kortex.knowledge.graph.list`,
`kortex.knowledge.source.index`, `kortex.knowledge.pack.load`). Before that
fix, none of the five handlers accepted `principal` at all, so a caller's
own `tenant_id` was never verified against anything -- see the M7.5
planning report §10 for the full, independently-reproduced finding.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.knowledge.exceptions import KnowledgeNodeNotFoundError
from kortex.engines.knowledge.models import KnowledgeNode, KnowledgePack
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_MASTER_KEY = b"\x77" * 32
_TEST_SIGNING_KEY = b"\x88" * 32
_TEST_ROLE = "knowledge-tenant-isolation-test-role"


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-knowledge-iso-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, KnowledgeEngine]:
    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "knowledge_iso_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    knowledge_engine = KnowledgeEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(knowledge_engine)
    return kernel, storage_engine, security_engine, knowledge_engine


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, KnowledgeEngine]:
    kernel, storage_engine, security_engine, knowledge_engine = _build_kernel(tmp_path)
    await kernel.boot()
    return kernel, storage_engine, security_engine, knowledge_engine


async def _seed_principal(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("knowledge-iso-test-credential")

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=True,
                credential_hash=credential_hash,
                roles=roles or [],
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await data_store.execute_in_transaction(_action)


async def _grant_role_permission(data_store: IDataStore, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        existing = await session.scalar(
            select(RolePermissionRecord).where(
                RolePermissionRecord.role == role,
                RolePermissionRecord.permission == permission,
            )
        )
        if existing is None:
            session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))

    await data_store.execute_in_transaction(_action)


async def _issue_token(security_engine: SecurityEngine, tenant_id: str, principal_id: str) -> TokenPayload:
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "knowledge-iso-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _authorized_token(
    storage_engine: StorageEngine, security_engine: SecurityEngine, tenant_id: str, permission: str
) -> TokenPayload:
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, permission)
    return await _issue_token(security_engine, tenant_id, "principal-1")


def _pack(data: bytes, asset_id: str, tenant_id: str) -> KnowledgePack:
    return KnowledgePack(
        asset_id=asset_id,
        tenant_id=tenant_id,
        manifest={"name": "iso-test-ontology"},
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        mime_type="application/x-kortex-knowledge",
        storage_key=f"packs/{asset_id}.kortex-knowledge",
    )


# -- kortex.knowledge.query.search -------------------------------------------


@pytest.mark.asyncio
async def test_search_succeeds_for_the_owning_tenant(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, knowledge_engine = await _boot_kernel(tmp_path)

    tenant_a = _tenant(tmp_path, "-a")
    knowledge_engine.graph.add_node(
        KnowledgeNode(node_id="node-a", tenant_id=tenant_a, entity_type="Concept", label="Widget Assembly")
    )
    token = await _authorized_token(storage_engine, security_engine, tenant_a, "knowledge:read")

    raw_query = {"query_id": str(uuid.uuid4()), "query_text": "Widget"}
    request = CapabilityRequest(
        capability_name="kortex.knowledge.query.search",
        session_token=token,
        parameters={"query": raw_query},
        context={"resource_tenant_id": tenant_a},
    )
    result = await kernel.invoke_capability(request)

    assert [n.node_id for n in result.matching_nodes] == ["node-a"]


@pytest.mark.asyncio
async def test_search_cross_tenant_attempt_fails_closed_even_with_spoofed_tenant_id(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, knowledge_engine = await _boot_kernel(tmp_path)

    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")
    knowledge_engine.graph.add_node(
        KnowledgeNode(node_id="node-a", tenant_id=tenant_a, entity_type="Concept", label="Widget Assembly")
    )
    token_b = await _authorized_token(storage_engine, security_engine, tenant_b, "knowledge:read")

    # Tenant B's own token, but the query payload spoofs tenant A's id --
    # the pre-fix behavior would have trusted this and returned tenant A's
    # real node.
    raw_query = {"query_id": str(uuid.uuid4()), "query_text": "Widget", "tenant_id": tenant_a}
    request = CapabilityRequest(
        capability_name="kortex.knowledge.query.search",
        session_token=token_b,
        parameters={"query": raw_query},
        context={"resource_tenant_id": tenant_b},
    )
    result = await kernel.invoke_capability(request)

    assert result.matching_nodes == []
    assert result.matching_records == []


# -- kortex.knowledge.graph.traverse -----------------------------------------


@pytest.mark.asyncio
async def test_traverse_graph_succeeds_for_the_owning_tenant(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, knowledge_engine = await _boot_kernel(tmp_path)

    tenant_a = _tenant(tmp_path, "-a")
    knowledge_engine.graph.add_node(
        KnowledgeNode(node_id="node-a", tenant_id=tenant_a, entity_type="Concept", label="Distributed Systems")
    )
    token = await _authorized_token(storage_engine, security_engine, tenant_a, "knowledge:read")

    request = CapabilityRequest(
        capability_name="kortex.knowledge.graph.traverse",
        session_token=token,
        parameters={"node_id": "node-a", "tenant_id": tenant_a, "max_hops": 1},
        context={"resource_tenant_id": tenant_a},
    )
    result = await kernel.invoke_capability(request)

    assert result == []


@pytest.mark.asyncio
async def test_traverse_graph_cross_tenant_attempt_fails_closed(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, knowledge_engine = await _boot_kernel(tmp_path)

    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")
    knowledge_engine.graph.add_node(
        KnowledgeNode(node_id="node-a", tenant_id=tenant_a, entity_type="Concept", label="Distributed Systems")
    )
    token_b = await _authorized_token(storage_engine, security_engine, tenant_b, "knowledge:read")

    # Tenant B's own token, spoofing tenant A's id AND tenant A's real
    # node_id in the request payload.
    request = CapabilityRequest(
        capability_name="kortex.knowledge.graph.traverse",
        session_token=token_b,
        parameters={"node_id": "node-a", "tenant_id": tenant_a, "max_hops": 1},
        context={"resource_tenant_id": tenant_b},
    )
    with pytest.raises(KnowledgeNodeNotFoundError):
        await kernel.invoke_capability(request)


# -- kortex.knowledge.graph.list ----------------------------------------------


@pytest.mark.asyncio
async def test_list_nodes_returns_only_the_callers_tenant(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, knowledge_engine = await _boot_kernel(tmp_path)

    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")
    knowledge_engine.graph.add_node(
        KnowledgeNode(node_id="node-a", tenant_id=tenant_a, entity_type="Concept", label="Tenant A Node")
    )
    knowledge_engine.graph.add_node(
        KnowledgeNode(node_id="node-b", tenant_id=tenant_b, entity_type="Concept", label="Tenant B Node")
    )
    token_a = await _authorized_token(storage_engine, security_engine, tenant_a, "knowledge:read")

    # Tenant A's own token, but the request spoofs tenant B's id.
    request = CapabilityRequest(
        capability_name="kortex.knowledge.graph.list",
        session_token=token_a,
        parameters={"tenant_id": tenant_b},
        context={"resource_tenant_id": tenant_a},
    )
    result = await kernel.invoke_capability(request)

    assert [n.node_id for n in result] == ["node-a"]


# -- kortex.knowledge.source.index -------------------------------------------


@pytest.mark.asyncio
async def test_index_source_lands_records_in_the_callers_real_tenant(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, knowledge_engine = await _boot_kernel(tmp_path)

    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")
    default_source_id = next(iter(knowledge_engine._source_providers))
    token_b = await _authorized_token(storage_engine, security_engine, tenant_b, "knowledge:write")

    # Tenant B's own token, spoofing tenant A's id -- the pre-fix behavior
    # would have ingested records into tenant A's partition.
    request = CapabilityRequest(
        capability_name="kortex.knowledge.source.index",
        session_token=token_b,
        parameters={"source_id": default_source_id, "tenant_id": tenant_a},
        context={"resource_tenant_id": tenant_b},
    )
    result = await kernel.invoke_capability(request)

    assert result, "expected the reference source provider to ingest at least one record"
    assert all(record.tenant_id == tenant_b for record in result)
    assert all(record.tenant_id != tenant_a for record in result)


# -- kortex.knowledge.pack.load -----------------------------------------------


@pytest.mark.asyncio
async def test_load_pack_registers_under_the_callers_real_tenant(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, knowledge_engine = await _boot_kernel(tmp_path)

    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")
    token_b = await _authorized_token(storage_engine, security_engine, tenant_b, "knowledge:write")

    data = b"iso-test-pack-payload"
    # The pack object itself claims tenant A -- the pre-fix behavior would
    # have registered it under tenant A regardless of who the real caller is.
    pack = _pack(data, asset_id="iso-pack-1", tenant_id=tenant_a)
    await storage_engine.object.put_object(pack.bucket_name, pack.storage_key, data)

    request = CapabilityRequest(
        capability_name="kortex.knowledge.pack.load",
        session_token=token_b,
        parameters={"pack": pack.model_dump(mode="json")},
        context={"resource_tenant_id": tenant_b},
    )
    result = await kernel.invoke_capability(request)

    assert result.tenant_id == tenant_b
    assert result.tenant_id != tenant_a
