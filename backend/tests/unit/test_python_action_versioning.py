"""Versioned, immutable, tenant-owned Python Actions.

Exercises `PythonActionManager` against a real SQLite database through the
real `RelationalDataStore`, because the three immutability guarantees this
milestone claims are not all expressible in Python: one of them is a database
unique constraint, and a test using an in-memory fake would assert the
constraint exists without ever asking the database to enforce it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.engines.python_exec.actions import PythonActionManager, compute_source_digest
from kortex.engines.python_exec.exceptions import (
    PythonActionImmutabilityError,
    PythonActionNotFoundError,
    PythonActionValidationError,
)
from kortex.engines.python_exec.models import PythonExecutionLimits, PythonTrustLevel
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TENANT_A = "tenant-alpha"
_TENANT_B = "tenant-beta"

_SOURCE_V1 = "def main(payload):\n    return {'v': 1}\n"
_SOURCE_V2 = "def main(payload):\n    return {'v': 2}\n"


@pytest_asyncio.fixture
async def manager(tmp_path: Path) -> AsyncIterator[PythonActionManager]:
    db_path = (tmp_path / f"pyactions_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()
    try:
        yield PythonActionManager(RelationalDataStore(db_manager))
    finally:
        await db_manager.disconnect()


async def _publish(manager: PythonActionManager, /, **overrides: object) -> object:
    payload: dict[str, object] = {
        "tenant_id": _TENANT_A,
        "action_id": "invoice-total",
        "name": "Invoice total",
        "source_code": _SOURCE_V1,
    }
    payload.update(overrides)
    return await manager.publish_version(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_save_creates_a_new_immutable_version(manager: PythonActionManager) -> None:
    first = await _publish(manager, source_code=_SOURCE_V1)
    second = await _publish(manager, source_code=_SOURCE_V2)

    assert first.version == 1  # type: ignore[attr-defined]
    assert second.version == 2  # type: ignore[attr-defined]

    # The earlier version is still resolvable and still carries its original
    # source: publishing never rewrote it.
    pinned = await manager.get_version(tenant_id=_TENANT_A, action_id="invoice-total", version=1)
    assert pinned.source_code == _SOURCE_V1
    assert pinned.source_sha256 == compute_source_digest(_SOURCE_V1)


@pytest.mark.asyncio
async def test_omitting_a_version_resolves_the_latest(manager: PythonActionManager) -> None:
    await _publish(manager, source_code=_SOURCE_V1)
    await _publish(manager, source_code=_SOURCE_V2)

    latest = await manager.get_version(tenant_id=_TENANT_A, action_id="invoice-total")
    assert latest.version == 2
    assert latest.source_code == _SOURCE_V2


@pytest.mark.asyncio
async def test_publishing_a_new_version_does_not_change_what_a_pinned_workflow_runs(
    manager: PythonActionManager,
) -> None:
    """The core reason versions are pinned rather than tracked.

    A workflow authored against version 1 must keep executing version 1's exact
    source after version 2 is published.
    """
    await _publish(manager, source_code=_SOURCE_V1)
    pinned_before = await manager.get_version(tenant_id=_TENANT_A, action_id="invoice-total", version=1)

    await _publish(manager, source_code=_SOURCE_V2)
    pinned_after = await manager.get_version(tenant_id=_TENANT_A, action_id="invoice-total", version=1)

    assert pinned_after.source_code == pinned_before.source_code == _SOURCE_V1


@pytest.mark.asyncio
async def test_version_list_omits_source_code(manager: PythonActionManager) -> None:
    """Discovery must not double as a bulk export of tenant source."""
    await _publish(manager, source_code=_SOURCE_V1)
    await _publish(manager, source_code=_SOURCE_V2)

    versions = await manager.list_versions(tenant_id=_TENANT_A, action_id="invoice-total")
    assert [entry["version"] for entry in versions] == [2, 1]
    for entry in versions:
        assert "source_code" not in entry
        assert entry["source_sha256"]


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_out_of_band_source_modification_is_detected_and_refused(
    manager: PythonActionManager, tmp_path: Path
) -> None:
    """A row edited directly in SQL must fail to resolve, not execute.

    This is the guarantee that does not depend on KORTEX's own code paths being
    the only writer: the stored digest is recomputed on every load, so a
    tampered backup restore or a direct UPDATE is caught before the source ever
    reaches a workspace.
    """
    await _publish(manager, source_code=_SOURCE_V1)

    async def _tamper(session: AsyncSession) -> None:
        await session.execute(
            text(
                "UPDATE python_action_versions SET source_code = :evil "
                "WHERE tenant_id = :tenant AND action_id = :action AND version = 1"
            ),
            {
                "evil": "import os\ndef main(payload):\n    return os.environ\n",
                "tenant": _TENANT_A,
                "action": "invoice-total",
            },
        )

    await manager._require_store().execute_in_transaction(_tamper)

    with pytest.raises(PythonActionImmutabilityError, match="integrity check"):
        await manager.get_version(tenant_id=_TENANT_A, action_id="invoice-total", version=1)


@pytest.mark.asyncio
async def test_untrusted_actions_cannot_declare_capability_access(manager: PythonActionManager) -> None:
    with pytest.raises(PythonActionValidationError, match="cannot declare KORTEX capability access"):
        await _publish(
            manager,
            trust_level=PythonTrustLevel.UNTRUSTED,
            allowed_capabilities=("kortex.knowledge.query.search",),
        )


@pytest.mark.asyncio
async def test_source_that_does_not_compile_is_rejected_at_publish_time(manager: PythonActionManager) -> None:
    with pytest.raises(PythonActionValidationError, match="does not compile"):
        await _publish(manager, source_code="def main(payload)\n    return 1\n")


@pytest.mark.asyncio
async def test_action_id_cannot_be_a_path_component(manager: PythonActionManager) -> None:
    """`action_id` reaches audit records and workspace-adjacent naming."""
    for candidate in ("../escape", "..", ".", ".hidden", "with space", "semi;colon"):
        with pytest.raises(PythonActionValidationError):
            await _publish(manager, action_id=candidate)


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_a_cannot_read_tenant_b_action(manager: PythonActionManager) -> None:
    """A cross-tenant miss is indistinguishable from "no such action"."""
    await manager.publish_version(
        tenant_id=_TENANT_B,
        action_id="secret-pricing-model",
        name="Tenant B pricing",
        source_code=_SOURCE_V1,
    )

    with pytest.raises(PythonActionNotFoundError):
        await manager.get_version(tenant_id=_TENANT_A, action_id="secret-pricing-model")
    with pytest.raises(PythonActionNotFoundError):
        await manager.get_action(tenant_id=_TENANT_A, action_id="secret-pricing-model")

    # Listing never crosses the boundary either.
    assert await manager.list_actions(tenant_id=_TENANT_A) == []
    assert await manager.list_versions(tenant_id=_TENANT_A, action_id="secret-pricing-model") == []


@pytest.mark.asyncio
async def test_two_tenants_may_hold_the_same_action_id_independently(manager: PythonActionManager) -> None:
    """`(tenant_id, action_id)` is the natural key, not `action_id` alone."""
    await manager.publish_version(tenant_id=_TENANT_A, action_id="shared-name", name="A", source_code=_SOURCE_V1)
    await manager.publish_version(tenant_id=_TENANT_B, action_id="shared-name", name="B", source_code=_SOURCE_V2)

    from_a = await manager.get_version(tenant_id=_TENANT_A, action_id="shared-name")
    from_b = await manager.get_version(tenant_id=_TENANT_B, action_id="shared-name")

    assert from_a.source_code == _SOURCE_V1
    assert from_b.source_code == _SOURCE_V2
    assert from_a.version == from_b.version == 1


@pytest.mark.asyncio
async def test_action_metadata_round_trips(manager: PythonActionManager) -> None:
    version = await _publish(
        manager,
        trust_level=PythonTrustLevel.TRUSTED,
        allowed_capabilities=("kortex.knowledge.query.search",),
        limits=PythonExecutionLimits(timeout_seconds=12.5, memory_bytes=256 * 1024 * 1024),
        entrypoint="handler",
        source_code="def handler(payload, kortex):\n    return 1\n",
        created_by="admin-user",
    )
    assert version.version == 1  # type: ignore[attr-defined]

    loaded = await manager.get_version(tenant_id=_TENANT_A, action_id="invoice-total", version=1)
    assert loaded.trust_level is PythonTrustLevel.TRUSTED
    assert loaded.allowed_capabilities == ("kortex.knowledge.query.search",)
    assert loaded.limits.timeout_seconds == 12.5
    assert loaded.limits.memory_bytes == 256 * 1024 * 1024
    assert loaded.entrypoint == "handler"
    assert loaded.created_by == "admin-user"

    action = await manager.get_action(tenant_id=_TENANT_A, action_id="invoice-total")
    assert action.latest_version == 1
    assert action.tenant_id == _TENANT_A
