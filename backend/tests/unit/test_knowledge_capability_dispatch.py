"""Slice 4.7 regression coverage: `kortex.knowledge.graph.traverse` and
`kortex.knowledge.graph.list` through the real Kernel Capability
Enforcement Boundary (`kortex.core.dispatch`).

Mirrors the established M5-4.6 bootstrap/seeding pattern (real, unmodified
Storage + Security Engines; no mocks on the security decision path) but
drives it against the real, production `KnowledgeEngine` — proving the
three states Slice 4.7 requires end to end:

    no token                          -> AuthenticationError  (401)
    valid token, missing permission   -> AuthorizationDeniedError (403)
    valid token, "knowledge:read"     -> real results (200)

Unlike Document's pre-seeded registries, the Knowledge Graph genuinely
starts empty (no reference nodes are auto-loaded) — the empty-registry
success case here is an honest empty result, not a limitation of the test.

`kortex.knowledge.query.search` was, until M7.2, NOT exercised as a working
capability here: its handler (`KnowledgeEngine.search`) expects a live
`KnowledgeQuery` object, but `CapabilityDispatcher._invoke_handler` used to
deliver plain, JSON-deserialized dicts as `**parameters` unmodified —
confirmed to raise `AttributeError` for every real (non-Python-object)
caller, including the desktop's own `invokeCapability()` path. Reported as
an out-of-scope finding at the time (see the M7 preflight AskUserQuestion
decision) rather than silently patched. M7.2 fixed the actual root cause at
the dispatch boundary (`core.dispatch._coerce_model_parameters` now coerces
a caller-supplied dict into the handler's own declared Pydantic type, for
every capability, not just this one) while implementing the AI Studio
conversational capabilities, which had the identical defect —
`test_search_capability_now_works_over_the_real_dict_based_ipc_path` below
is this file's own regression coverage for that fix. `kortex.knowledge.
graph.list` (Slice 4.7) remains a working, primitive-parameter
entity-discovery path regardless."""

from __future__ import annotations

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
from kortex.engines.knowledge.models import KnowledgeNode
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_MASTER_KEY = b"\xdd" * 32
_TEST_SIGNING_KEY = b"\xee" * 32
_TEST_ROLE = "knowledge-dispatch-test-role"


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-knowledge-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, KnowledgeEngine]:
    kernel = Kernel()
    # M5-A8: explicit isolated in-memory DB — this test's exact-result graph
    # assertions must never observe a node another test (or a real local
    # run sharing the machine-wide default) left behind.
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "knowledge_dispatch_storage"))
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
    credential_hash = PasswordHasher().hash("knowledge-dispatch-test-credential")

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
            "password": "knowledge-dispatch-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.asyncio
async def test_no_token_is_denied_authentication_for_search(tmp_path: Path) -> None:
    """The security boundary runs before the (broken, see below) handler is
    ever invoked, so this remains valid regardless of the search defect."""
    kernel, _storage, _security, _knowledge = await _boot_kernel(tmp_path)

    request = CapabilityRequest(capability_name="kortex.knowledge.query.search", session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_without_knowledge_read_permission_is_denied_authorization_for_search(
    tmp_path: Path,
) -> None:
    """Same rationale as the 401 test above — authorization is checked
    before the handler runs."""
    kernel, storage_engine, security_engine, _knowledge = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.knowledge.query.search",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_search_capability_now_works_over_the_real_dict_based_ipc_path(tmp_path: Path) -> None:
    """Was `test_search_capability_is_broken_over_the_real_dict_based_ipc_path`
    (Slice 4.7 preflight finding): `kortex.knowledge.query.search`'s handler
    (`KnowledgeEngine.search`) expects a live `KnowledgeQuery` object and
    immediately does attribute access on it (`query.tenant_id`), but every
    real caller (Tauri/Rust IPC, the FastAPI HTTP boundary, or this test's
    own dict below) delivers plain, JSON-deserialized data — never a live
    Python object — so this capability was provably unusable outside a
    same-process test that hand-constructs the object itself.

    M7.2 fixed the actual root cause (`core.dispatch._coerce_model_parameters`,
    not this engine): the dispatcher now coerces a caller-supplied dict into
    the real Pydantic model type a handler's own signature declares, for
    every capability, not just this one. This test's own prior docstring
    explicitly anticipated exactly this: "If a future change fixes this
    (e.g. a coercion wrapper), this test should start failing here as a
    signal to replace it with a real success-path test" — this is that
    replacement. The empty-registry result here is an honest empty result
    (no node was seeded for this fresh tenant), not a limitation of the fix."""
    kernel, storage_engine, security_engine, _knowledge = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "knowledge:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    # Exactly what arrives over real JSON/HTTP: a plain dict, not a
    # KnowledgeQuery instance.
    raw_query = {"query_id": str(uuid.uuid4()), "tenant_id": tenant_id, "query_text": "anything"}
    request = CapabilityRequest(
        capability_name="kortex.knowledge.query.search",
        session_token=token,
        parameters={"query": raw_query},
        context={"resource_tenant_id": tenant_id},
    )

    result = await kernel.invoke_capability(request)

    assert result.query_id == raw_query["query_id"]
    assert result.matching_nodes == []


@pytest.mark.asyncio
async def test_no_token_is_denied_authentication_for_traverse(tmp_path: Path) -> None:
    kernel, _storage, _security, _knowledge = await _boot_kernel(tmp_path)

    request = CapabilityRequest(capability_name="kortex.knowledge.graph.traverse", session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_without_knowledge_read_permission_is_denied_authorization_for_traverse(
    tmp_path: Path,
) -> None:
    kernel, storage_engine, security_engine, _knowledge = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.knowledge.graph.traverse",
        session_token=token,
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_with_knowledge_read_permission_traverses_a_seeded_node(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, knowledge_engine = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    knowledge_engine.graph.add_node(
        KnowledgeNode(
            node_id="node-1",
            tenant_id=tenant_id,
            entity_type="Concept",
            label="Distributed Systems",
        )
    )
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "knowledge:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.knowledge.graph.traverse",
        session_token=token,
        parameters={"node_id": "node-1", "tenant_id": tenant_id, "max_hops": 1},
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert result == []


@pytest.mark.asyncio
async def test_no_token_is_denied_authentication_for_graph_list(tmp_path: Path) -> None:
    kernel, _storage, _security, _knowledge = await _boot_kernel(tmp_path)

    request = CapabilityRequest(capability_name="kortex.knowledge.graph.list", session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_without_knowledge_read_permission_is_denied_authorization_for_graph_list(
    tmp_path: Path,
) -> None:
    kernel, storage_engine, security_engine, _knowledge = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.knowledge.graph.list",
        session_token=token,
        parameters={"tenant_id": tenant_id},
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_with_knowledge_read_permission_graph_list_returns_honest_empty_result(
    tmp_path: Path,
) -> None:
    kernel, storage_engine, security_engine, _knowledge = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "knowledge:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.knowledge.graph.list",
        session_token=token,
        parameters={"tenant_id": tenant_id},
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert result == []


@pytest.mark.asyncio
async def test_authenticated_with_knowledge_read_permission_graph_list_returns_seeded_nodes(
    tmp_path: Path,
) -> None:
    kernel, storage_engine, security_engine, knowledge_engine = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    knowledge_engine.graph.add_node(
        KnowledgeNode(node_id="node-1", tenant_id=tenant_id, entity_type="Concept", label="Distributed Systems")
    )
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "knowledge:read")
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.knowledge.graph.list",
        session_token=token,
        parameters={"tenant_id": tenant_id},
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert len(result) == 1
    assert result[0].node_id == "node-1"
