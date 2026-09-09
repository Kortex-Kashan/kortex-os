"""
KORTEX OS — Milestone F4 Test Suite
Workflow Definition Lifecycle: Draft/Publish/Archive/Clone, Persistence, Tenant Isolation,
Optimistic Concurrency, Publish-Time Authorization, and Backward Compatibility.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
from argon2 import PasswordHasher

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import ResourceNotFoundError
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import (
    WorkflowDefinitionAuthorizationError,
    WorkflowDefinitionConflictError,
    WorkflowDefinitionNotFoundError,
    WorkflowDefinitionStateError,
)
from kortex.engines.workflow.models import (
    WorkflowDefinition,
    WorkflowDefinitionStatus,
    WorkflowStep,
    WorkflowTrigger,
)
from kortex.engines.workflow.persistence import WorkflowStore

_TEST_MASTER_KEY = b"\xee" * 32
_TEST_SIGNING_KEY = b"\xff" * 32

# ============================================================================
# 1. Persistence-layer tests (WorkflowStore direct — no Kernel/dispatch)
# ============================================================================


@pytest.fixture
async def test_store() -> AsyncGenerator[WorkflowStore, None]:
    db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    await db_manager.connect()
    await db_manager.create_all_tables()
    data_store = RelationalDataStore(db_manager)
    store = WorkflowStore(data_store)
    yield store
    if db_manager._engine:
        await db_manager._engine.dispose()


@pytest.mark.asyncio
async def test_create_draft_and_get_control_row(test_store: WorkflowStore) -> None:
    dv = await test_store.create_draft("def_a", "tenant1", created_by="alice", name="A")
    assert dv.status == WorkflowDefinitionStatus.DRAFT
    assert dv.version == "0.0.0-draft"
    assert dv.lock_version == 1

    fetched = await test_store.get_control_row("def_a", "tenant1")
    assert fetched is not None
    assert fetched.name == "A"
    assert fetched.created_by == "alice"


@pytest.mark.asyncio
async def test_create_draft_duplicate_raises_conflict(test_store: WorkflowStore) -> None:
    await test_store.create_draft("def_b", "tenant1", created_by="alice", name="B")
    with pytest.raises(WorkflowDefinitionConflictError):
        await test_store.create_draft("def_b", "tenant1", created_by="alice", name="B again")


@pytest.mark.asyncio
async def test_update_draft_optimistic_lock_success_and_conflict(test_store: WorkflowStore) -> None:
    dv = await test_store.create_draft("def_c", "tenant1", created_by="alice", name="C")
    updated = await test_store.update_draft("def_c", "tenant1", dv.lock_version, name="C renamed")
    assert updated.name == "C renamed"
    assert updated.lock_version == dv.lock_version + 1

    # Stale lock_version (the original, already superseded) must be rejected.
    with pytest.raises(WorkflowDefinitionConflictError):
        await test_store.update_draft("def_c", "tenant1", dv.lock_version, name="stale write")


@pytest.mark.asyncio
async def test_update_draft_missing_definition_raises_not_found(test_store: WorkflowStore) -> None:
    with pytest.raises(WorkflowDefinitionNotFoundError):
        await test_store.update_draft("does_not_exist", "tenant1", 1, name="x")


@pytest.mark.asyncio
async def test_archive_then_update_rejected(test_store: WorkflowStore) -> None:
    dv = await test_store.create_draft("def_d", "tenant1", created_by="alice", name="D")
    archived = await test_store.archive_control_row("def_d", "tenant1", dv.lock_version)
    assert archived.status == WorkflowDefinitionStatus.ARCHIVED

    with pytest.raises(WorkflowDefinitionStateError):
        await test_store.update_draft("def_d", "tenant1", archived.lock_version, name="cannot")


@pytest.mark.asyncio
async def test_publish_draft_creates_immutable_version_and_projection_row(test_store: WorkflowStore) -> None:
    step = WorkflowStep(id="s1", name="Step 1")
    dv = await test_store.create_draft(
        "def_e", "tenant1", created_by="alice", name="E", steps=[step], trigger=WorkflowTrigger.MANUAL
    )
    published = await test_store.publish_draft("def_e", "tenant1", dv.lock_version, "1.0.0")
    assert published.status == WorkflowDefinitionStatus.PUBLISHED
    assert published.version == "1.0.0"
    assert published.published_at is not None

    projection = await test_store.get_definition("def_e", tenant_id="tenant1")
    assert projection is not None
    assert projection.version == "1.0.0"
    assert projection.steps[0].id == "s1"

    # Git-like model: the draft control row survives publish, unchanged in content.
    control = await test_store.get_control_row("def_e", "tenant1")
    assert control is not None
    assert control.status == WorkflowDefinitionStatus.DRAFT
    assert control.name == "E"


@pytest.mark.asyncio
async def test_publish_draft_lock_conflict(test_store: WorkflowStore) -> None:
    dv = await test_store.create_draft(
        "def_f", "tenant1", created_by="alice", name="F", steps=[WorkflowStep(id="s1", name="s1")]
    )
    with pytest.raises(WorkflowDefinitionConflictError):
        await test_store.publish_draft("def_f", "tenant1", dv.lock_version + 99, "1.0.0")


@pytest.mark.asyncio
async def test_get_definition_version_aware_exact_resolution(test_store: WorkflowStore) -> None:
    """Milestone F4 (D7/D8) core persistence contract: an exact version request resolves that
    exact immutable content, never 'latest' — the mechanism every re-entry path depends on."""
    dv = await test_store.create_draft(
        "def_g", "tenant1", created_by="alice", name="v1 name", steps=[WorkflowStep(id="s1", name="s1")]
    )
    await test_store.publish_draft("def_g", "tenant1", dv.lock_version, "1.0.0")

    updated = await test_store.update_draft(
        "def_g",
        "tenant1",
        dv.lock_version + 1,
        name="v2 name",
        steps=[WorkflowStep(id="s1", name="s1"), WorkflowStep(id="s2", name="s2")],
    )
    await test_store.publish_draft("def_g", "tenant1", updated.lock_version, "1.1.0")

    v1 = await test_store.get_definition("def_g", tenant_id="tenant1", version="1.0.0")
    v2 = await test_store.get_definition("def_g", tenant_id="tenant1", version="1.1.0")
    latest = await test_store.get_definition("def_g", tenant_id="tenant1")

    assert v1 is not None and v1.name == "v1 name" and len(v1.steps) == 1
    assert v2 is not None and v2.name == "v2 name" and len(v2.steps) == 2
    assert latest is not None and latest.version == "1.1.0"

    missing = await test_store.get_definition("def_g", tenant_id="tenant1", version="9.9.9")
    assert missing is None


@pytest.mark.asyncio
async def test_get_definition_legacy_fallback_for_untouched_definition(test_store: WorkflowStore) -> None:
    """A definition never touched by the F4 lifecycle (no `workflow_definition_versions` row at
    all) still resolves an exact-version request via the legacy `workflow_definitions` row —
    the 'implicit already-published v1' with zero data migration."""
    legacy_def = WorkflowDefinition(
        id="def_legacy", name="Legacy", version="1.0.0", tenant_id="tenant1", steps=[WorkflowStep(id="s1", name="s1")]
    )
    await test_store.save_definition(legacy_def, tenant_id="tenant1")

    resolved = await test_store.get_definition("def_legacy", tenant_id="tenant1", version="1.0.0")
    assert resolved is not None
    assert resolved.name == "Legacy"

    missing = await test_store.get_definition("def_legacy", tenant_id="tenant1", version="2.0.0")
    assert missing is None


@pytest.mark.asyncio
async def test_published_version_content_immutable_after_further_draft_edits(test_store: WorkflowStore) -> None:
    """Scenario F (persistence layer): once published, a version's content can never change,
    regardless of how many further draft edits and republishes happen afterward."""
    dv = await test_store.create_draft(
        "def_h", "tenant1", created_by="alice", name="original", steps=[WorkflowStep(id="s1", name="s1")]
    )
    v1 = await test_store.publish_draft("def_h", "tenant1", dv.lock_version, "1.0.0")
    assert v1.name == "original"

    updated = await test_store.update_draft("def_h", "tenant1", dv.lock_version + 1, name="mutated")
    await test_store.publish_draft("def_h", "tenant1", updated.lock_version, "2.0.0")

    v1_again = await test_store.get_published_version("def_h", "tenant1", "1.0.0")
    assert v1_again is not None
    assert v1_again.name == "original"  # unchanged despite the later draft edit + republish


@pytest.mark.asyncio
async def test_list_definition_versions_ordering(test_store: WorkflowStore) -> None:
    dv = await test_store.create_draft(
        "def_i", "tenant1", created_by="alice", name="I", steps=[WorkflowStep(id="s1", name="s1")]
    )
    await test_store.publish_draft("def_i", "tenant1", dv.lock_version, "1.0.0")
    versions = await test_store.list_definition_versions("def_i", "tenant1")
    statuses = [v.status for v in versions]
    assert WorkflowDefinitionStatus.DRAFT in statuses
    assert WorkflowDefinitionStatus.PUBLISHED in statuses
    assert len(versions) == 2


@pytest.mark.asyncio
async def test_tenant_isolation_at_persistence_layer(test_store: WorkflowStore) -> None:
    await test_store.create_draft("def_shared_id", "tenant_a", created_by="alice", name="A's draft")
    control_b = await test_store.get_control_row("def_shared_id", "tenant_b")
    assert control_b is None  # tenant B sees nothing of tenant A's draft under the same definition_id


# ============================================================================
# 2. Capability-layer tests (real Kernel dispatch — auth, tenant isolation, D11)
# ============================================================================

_ROLE = "F4_LIFECYCLE_TEST_ROLE"
_TENANT_A = "f4_tenant_alpha"
_TENANT_B = "f4_tenant_beta"


@pytest.fixture
async def kernel(tmp_path: Path) -> AsyncGenerator[Kernel, None]:
    db_file = tmp_path / f"test_f4_{uuid4().hex[:8]}.db"
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_file}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    k = Kernel()
    k._db_manager = db_manager

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_f4_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine()

    k.register_engine(storage_engine)
    k.register_engine(security_engine)
    k.register_engine(workflow_engine)
    await k.boot()

    hasher = PasswordHasher()

    async def _seed(session) -> None:
        perms = ["workflow:write", "workflow:read", "workflow:start"]
        session.add_all(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission=p) for p in perms)
        for tenant, principal_id, password in (
            (_TENANT_A, "user_alpha", "pass-alpha"),
            (_TENANT_B, "user_beta", "pass-beta"),
        ):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant,
                    principal_id=principal_id,
                    principal_type="USER",
                    credential_hash=hasher.hash(password),
                    roles=[_ROLE],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )
        await session.flush()

    await storage_engine.data.execute_in_transaction(_seed)
    yield k
    await k.shutdown()
    await db_manager.disconnect()


async def _token(kernel: Kernel, tenant_id: str, principal_id: str, password: str):
    security_engine: SecurityEngine = kernel.get_engine("security")
    auth = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": password}
    )
    return await security_engine.authentication_manager.issue_token(auth)


async def _invoke(kernel: Kernel, capability_name: str, token, tenant_id: str = _TENANT_A, **parameters):
    return await kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability_name,
            session_token=token,
            parameters=parameters,
            context={"resource_tenant_id": tenant_id},
        )
    )


@pytest.mark.asyncio
async def test_create_get_update_validate_publish_capability_roundtrip(kernel: Kernel) -> None:
    token = await _token(kernel, _TENANT_A, "user_alpha", "pass-alpha")

    created = await _invoke(
        kernel,
        "kortex.workflow.definition.create",
        token,
        name="Onboarding",
        steps=[{"id": "s1", "name": "Step 1"}],
    )
    definition_id = created["definition_id"]
    assert created["status"] == "DRAFT"

    fetched = await _invoke(kernel, "kortex.workflow.definition.get", token, definition_id=definition_id)
    assert fetched["draft"]["name"] == "Onboarding"

    report = await _invoke(kernel, "kortex.workflow.definition.validate", token, definition_id=definition_id)
    assert report["is_valid"] is True

    updated = await _invoke(
        kernel,
        "kortex.workflow.definition.update",
        token,
        definition_id=definition_id,
        expected_lock_version=created["lock_version"],
        description="Updated description",
    )
    assert updated["description"] == "Updated description"

    published = await _invoke(
        kernel,
        "kortex.workflow.definition.publish",
        token,
        definition_id=definition_id,
        expected_lock_version=updated["lock_version"],
    )
    assert published["status"] == "PUBLISHED"
    assert published["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_draft_cannot_start_workflow_instance(kernel: Kernel) -> None:
    """D5: a DRAFT is never bindable to a running instance — no `workflow_definitions` projection
    row exists until the first publish."""
    token = await _token(kernel, _TENANT_A, "user_alpha", "pass-alpha")
    created = await _invoke(
        kernel, "kortex.workflow.definition.create", token, name="Unpublished", steps=[{"id": "s1", "name": "s1"}]
    )
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    with pytest.raises(ResourceNotFoundError):
        await wf_engine.start_workflow(created["definition_id"], tenant_id=_TENANT_A)


@pytest.mark.asyncio
async def test_publish_makes_definition_startable(kernel: Kernel) -> None:
    token = await _token(kernel, _TENANT_A, "user_alpha", "pass-alpha")
    created = await _invoke(
        kernel, "kortex.workflow.definition.create", token, name="Startable", steps=[{"id": "s1", "name": "s1"}]
    )
    await _invoke(
        kernel,
        "kortex.workflow.definition.publish",
        token,
        definition_id=created["definition_id"],
        expected_lock_version=created["lock_version"],
    )
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    instance = await wf_engine.start_workflow(created["definition_id"], tenant_id=_TENANT_A)
    assert instance.definition_version == "1.0.0"


@pytest.mark.asyncio
async def test_archive_blocks_new_instance_creation(kernel: Kernel) -> None:
    """D15: archiving blocks new instance creation but is otherwise non-destructive."""
    token = await _token(kernel, _TENANT_A, "user_alpha", "pass-alpha")
    created = await _invoke(
        kernel, "kortex.workflow.definition.create", token, name="ToArchive", steps=[{"id": "s1", "name": "s1"}]
    )
    await _invoke(
        kernel,
        "kortex.workflow.definition.publish",
        token,
        definition_id=created["definition_id"],
        expected_lock_version=created["lock_version"],
    )
    # `publish` bumps the DRAFT control row's own lock_version too (the draft survives publish
    # unchanged in content, but its lock_version is not the published snapshot's) -- re-fetch it.
    after_publish = await _invoke(
        kernel, "kortex.workflow.definition.get", token, definition_id=created["definition_id"]
    )
    await _invoke(
        kernel,
        "kortex.workflow.definition.archive",
        token,
        definition_id=created["definition_id"],
        expected_lock_version=after_publish["draft"]["lock_version"],
    )
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    with pytest.raises(WorkflowDefinitionStateError):
        await wf_engine.start_workflow(created["definition_id"], tenant_id=_TENANT_A)


@pytest.mark.asyncio
async def test_clone_produces_independent_definition(kernel: Kernel) -> None:
    token = await _token(kernel, _TENANT_A, "user_alpha", "pass-alpha")
    created = await _invoke(
        kernel,
        "kortex.workflow.definition.create",
        token,
        name="Source",
        steps=[{"id": "s1", "name": "s1"}, {"id": "s2", "name": "s2"}],
    )
    cloned = await _invoke(
        kernel, "kortex.workflow.definition.clone", token, source_definition_id=created["definition_id"]
    )
    assert cloned["definition_id"] != created["definition_id"]
    assert len(cloned["steps"]) == 2
    assert cloned["name"] == "Source (copy)"

    # Mutating the clone must never affect the source.
    await _invoke(
        kernel,
        "kortex.workflow.definition.update",
        token,
        definition_id=cloned["definition_id"],
        expected_lock_version=cloned["lock_version"],
        name="Renamed clone",
    )
    source_after = await _invoke(
        kernel, "kortex.workflow.definition.get", token, definition_id=created["definition_id"]
    )
    assert source_after["draft"]["name"] == "Source"


@pytest.mark.asyncio
async def test_tenant_isolation_definition_not_visible_cross_tenant(kernel: Kernel) -> None:
    token_a = await _token(kernel, _TENANT_A, "user_alpha", "pass-alpha")
    token_b = await _token(kernel, _TENANT_B, "user_beta", "pass-beta")

    created = await _invoke(
        kernel, "kortex.workflow.definition.create", token_a, name="Alpha only", steps=[{"id": "s1", "name": "s1"}]
    )

    with pytest.raises(WorkflowDefinitionNotFoundError):
        await _invoke(
            kernel,
            "kortex.workflow.definition.get",
            token_b,
            tenant_id=_TENANT_B,
            definition_id=created["definition_id"],
        )


@pytest.mark.asyncio
async def test_update_rejects_stale_lock_version_via_capability(kernel: Kernel) -> None:
    token = await _token(kernel, _TENANT_A, "user_alpha", "pass-alpha")
    created = await _invoke(
        kernel, "kortex.workflow.definition.create", token, name="Concurrent", steps=[{"id": "s1", "name": "s1"}]
    )

    await _invoke(
        kernel,
        "kortex.workflow.definition.update",
        token,
        definition_id=created["definition_id"],
        expected_lock_version=created["lock_version"],
        name="First writer wins",
    )
    with pytest.raises(WorkflowDefinitionConflictError):
        await _invoke(
            kernel,
            "kortex.workflow.definition.update",
            token,
            definition_id=created["definition_id"],
            expected_lock_version=created["lock_version"],  # stale — already superseded above
            name="Second writer loses",
        )


@pytest.mark.asyncio
async def test_publish_rejects_unknown_referenced_capability(kernel: Kernel) -> None:
    """D10 — a definition referencing a nonexistent capability cannot be published."""
    token = await _token(kernel, _TENANT_A, "user_alpha", "pass-alpha")
    created = await _invoke(
        kernel,
        "kortex.workflow.definition.create",
        token,
        name="BadCapability",
        steps=[{"id": "s1", "name": "s1", "capability_name": "kortex.does.not.exist"}],
    )
    with pytest.raises(WorkflowDefinitionAuthorizationError):
        await _invoke(
            kernel,
            "kortex.workflow.definition.publish",
            token,
            definition_id=created["definition_id"],
            expected_lock_version=created["lock_version"],
        )


@pytest.mark.asyncio
async def test_publish_rejects_unauthorized_referenced_capability(kernel: Kernel) -> None:
    """D11 — the publishing principal must be freshly re-authorized against every referenced
    capability at publish time, not merely have the definition-level `workflow:write` permission."""
    token = await _token(kernel, _TENANT_A, "user_alpha", "pass-alpha")
    created = await _invoke(
        kernel,
        "kortex.workflow.definition.create",
        token,
        name="RestrictedCapability",
        # A real, registered capability this principal's role was never granted permission for.
        # (Not "kortex.workflow.schedule.trigger": it requires only "workflow:start", which this
        # fixture's _ROLE already grants -- a pre-existing test-design gap that a since-fixed ABAC
        # defect in lifecycle.py's publish() had been masking: that defect denied every publish
        # unconditionally regardless of RBAC, so this test's expected DENIED outcome coincided with
        # a bug rather than proving the RBAC-authorization property its own docstring claims.
        # "kortex.workflow.instance.approve" requires "workflow:approve", which _ROLE genuinely
        # lacks, making this a real unauthorized-capability case.)
        steps=[{"id": "s1", "name": "s1", "capability_name": "kortex.workflow.instance.approve"}],
    )
    with pytest.raises(WorkflowDefinitionAuthorizationError):
        await _invoke(
            kernel,
            "kortex.workflow.definition.publish",
            token,
            definition_id=created["definition_id"],
            expected_lock_version=created["lock_version"],
        )


@pytest.mark.asyncio
async def test_legacy_definition_lazy_materialization_on_first_update(kernel: Kernel) -> None:
    """Backward compatibility: a legacy definition (registered before F4, never touched by the F4
    lifecycle) gets its first DRAFT lazily materialized on first `update()`, seeded from its
    current published content — never rewritten in bulk, never eagerly materialized."""
    wf_engine: WorkflowEngine = kernel.get_engine("workflow")
    legacy_def = WorkflowDefinition(
        id="legacy_def_1",
        name="Legacy Flow",
        version="1.0.0",
        tenant_id=_TENANT_A,
        steps=[WorkflowStep(id="s1", name="s1")],
    )
    await wf_engine.register_definition_async(legacy_def, tenant_id=_TENANT_A)

    token = await _token(kernel, _TENANT_A, "user_alpha", "pass-alpha")
    # No draft exists yet -- get() must still succeed via the legacy projection alone.
    fetched = await _invoke(kernel, "kortex.workflow.definition.get", token, definition_id="legacy_def_1")
    assert fetched["draft"] is None
    assert fetched["latest_published_version"] == "1.0.0"

    updated = await _invoke(
        kernel,
        "kortex.workflow.definition.update",
        token,
        definition_id="legacy_def_1",
        expected_lock_version=1,  # caller cannot know the pre-materialization lock; ignored on first touch
        description="Now under F4 lifecycle",
    )
    assert updated["name"] == "Legacy Flow"  # seeded from the legacy row
    assert updated["description"] == "Now under F4 lifecycle"

    # The legacy `workflow_definitions` row itself is untouched -- still directly loadable/startable.
    still_runnable = await wf_engine.get_definition_async("legacy_def_1", tenant_id=_TENANT_A)
    assert still_runnable.name == "Legacy Flow"
