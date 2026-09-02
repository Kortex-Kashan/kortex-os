"""M6.2-1/M6.2-2 regression suite: AI system identity and tool-invocation
identity propagation.

Prior to this fix, `KernelToolExecutionPort.execute_tool` never supplied a
`session_token` to `IKernelBridge.invoke_capability` — every AI tool call
against an authenticated capability failed closed with `AuthenticationError`.
This suite drives the real Kernel capability-dispatch boundary end to end:
real `SecurityEngine`, real Argon2id authentication, real RBAC, real
`kernel.invoke_capability` — using the exact production bootstrap helper
(`kortex.api.kernel_bootstrap._build_ai_system_identity`), not a
reimplementation of it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.kernel_bootstrap import _build_ai_system_identity
from kortex.core.db import DatabaseEngineManager
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.engine import KernelToolExecutionPort
from kortex.engines.ai.identity import AI_SYSTEM_PRINCIPAL_ID, AI_SYSTEM_ROLE
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\xaa" * 32
_TEST_SIGNING_KEY = b"\xbb" * 32

_GATED_CAPABILITY = "test.ai_identity.gated_action"
_FORBIDDEN_CAPABILITY = "test.ai_identity.forbidden_action"


async def _gated_handler(principal: Any = None, **kwargs: Any) -> dict[str, Any]:
    return {
        "status": "SUCCESS",
        "principal_id": principal.principal_id if principal is not None else None,
        "principal_type": principal.principal_type.value if principal is not None else None,
        "tenant_id": principal.tenant_id if principal is not None else None,
    }


async def _forbidden_handler(**kwargs: Any) -> dict[str, Any]:
    return {"status": "SUCCESS"}


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_ai_identity_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_ai_identity_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    kernel.register_capability(
        name=_GATED_CAPABILITY,
        description="Test-only capability granted to AI_SYSTEM_ACTOR.",
        provider="test",
        handler=_gated_handler,
        required_permissions=["test:invoke"],
    )
    kernel.register_capability(
        name=_FORBIDDEN_CAPABILITY,
        description="Test-only capability never granted to AI_SYSTEM_ACTOR.",
        provider="test",
        handler=_forbidden_handler,
        required_permissions=["test:forbidden"],
    )

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=AI_SYSTEM_ROLE, permission="test:invoke"))

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    try:
        yield kernel
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


def _make_port(kernel: Kernel) -> KernelToolExecutionPort:
    security_engine: SecurityEngine = kernel.get_engine("security")
    ai_identity = _build_ai_system_identity(security_engine)
    bridge = KernelBridgeAdapter(kernel)
    return KernelToolExecutionPort(kernel_bridge=bridge, ai_identity=ai_identity)


@pytest.mark.asyncio
async def test_ai_tool_call_reaches_authenticated_capability_with_real_principal(kernel_env: Kernel) -> None:
    """The core M6.2-1/M6.2-2 fix: a tool call the AI makes must actually
    authenticate through the real Kernel dispatch boundary and receive the
    real, verified `AI_SYSTEM_ACTOR`-scoped principal in the handler."""
    port = _make_port(kernel_env)

    result = await port.execute_tool(
        tenant_id="default",
        capability_name=_GATED_CAPABILITY,
        arguments={},
    )

    assert result["status"] == "SUCCESS"
    assert result["principal_id"] == AI_SYSTEM_PRINCIPAL_ID
    assert result["principal_type"] == "AGENT"
    assert result["tenant_id"] == "default"


@pytest.mark.asyncio
async def test_ai_tool_call_denied_for_ungranted_permission(kernel_env: Kernel) -> None:
    """Real RBAC still applies: the AI system principal has no special
    bypass. A capability requiring a permission never granted to
    AI_SYSTEM_ACTOR must be denied, not silently allowed."""
    port = _make_port(kernel_env)

    with pytest.raises(AuthorizationDeniedError):
        await port.execute_tool(
            tenant_id="default",
            capability_name=_FORBIDDEN_CAPABILITY,
            arguments={},
        )


@pytest.mark.asyncio
async def test_without_ai_identity_tool_call_fails_closed_not_bypassed(kernel_env: Kernel) -> None:
    """Regression guard for the pre-M6.2 behavior this fix replaces: with no
    `ai_identity` configured, the call must still fail closed (no session
    token reaches the dispatcher) rather than silently succeeding through
    some fallback path."""
    bridge = KernelBridgeAdapter(kernel_env)
    port = KernelToolExecutionPort(kernel_bridge=bridge, ai_identity=None)

    from kortex.engines.security.exceptions import AuthenticationError

    with pytest.raises(AuthenticationError):
        await port.execute_tool(
            tenant_id="default",
            capability_name=_GATED_CAPABILITY,
            arguments={},
        )


@pytest.mark.asyncio
async def test_ai_principal_provisioning_is_idempotent_across_repeated_calls(kernel_env: Kernel) -> None:
    """Repeated AI tool calls (simulating repeated application boots /
    repeated agent turns) must never create duplicate PrincipalRecord rows."""
    port = _make_port(kernel_env)

    for _ in range(3):
        result = await port.execute_tool(
            tenant_id="default",
            capability_name=_GATED_CAPABILITY,
            arguments={},
        )
        assert result["status"] == "SUCCESS"

    storage_engine: StorageEngine = kernel_env.get_engine("storage")

    async def _count(session: AsyncSession) -> int:
        stmt = select(PrincipalRecord).where(
            PrincipalRecord.tenant_id == "default",
            PrincipalRecord.principal_id == AI_SYSTEM_PRINCIPAL_ID,
            PrincipalRecord.principal_type == "AGENT",
        )
        res = await session.execute(stmt)
        return len(list(res.scalars().all()))

    assert await storage_engine.data.execute_in_transaction(_count) == 1


@pytest.mark.asyncio
async def test_ai_identity_is_tenant_scoped_not_shared(kernel_env: Kernel) -> None:
    """One principal/credential per tenant: acting within tenant 'acme' must
    provision (and authenticate as) a principal distinct from tenant
    'default' — compromise of one tenant's credential grants no access to
    another's."""
    # `RolePermissionRecord` grants are global, not tenant-scoped (see its own
    # docstring) — the single grant seeded in the fixture already covers
    # every tenant's `AI_SYSTEM_ACTOR` principal.
    port = _make_port(kernel_env)

    result_default = await port.execute_tool(tenant_id="default", capability_name=_GATED_CAPABILITY, arguments={})
    result_acme = await port.execute_tool(tenant_id="acme", capability_name=_GATED_CAPABILITY, arguments={})

    assert result_default["tenant_id"] == "default"
    assert result_acme["tenant_id"] == "acme"

    storage_engine: StorageEngine = kernel_env.get_engine("storage")

    async def _rows(session: AsyncSession) -> list[str]:
        stmt = select(PrincipalRecord).where(
            PrincipalRecord.principal_id == AI_SYSTEM_PRINCIPAL_ID,
            PrincipalRecord.principal_type == "AGENT",
        )
        res = await session.execute(stmt)
        return sorted(r.tenant_id for r in res.scalars().all())

    tenant_ids = await storage_engine.data.execute_in_transaction(_rows)
    assert tenant_ids == ["acme", "default"]
