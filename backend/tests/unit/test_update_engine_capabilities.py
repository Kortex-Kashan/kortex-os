"""Unit tests for Update Engine capabilities, security enforcement, and diagnostics.

Phase 7 — Production Hardening — Update Engine.
Verifies capability registration, execution context authentication, RBAC authorization,
and IEngineDiagnostics safety guarantees.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.security.models import PrincipalType, SecurityPrincipal
from kortex.engines.update.constants import (
    ALL_UPDATE_CAPABILITIES,
    PERMISSION_UPDATE_MANAGE,
    PERMISSION_UPDATE_READ,
)
from kortex.engines.update.engine import UpdateEngine
from kortex.engines.update.exceptions import (
    UpdateAuthenticationError,
    UpdateAuthorizationError,
)


@pytest.fixture
def test_engine(tmp_path: Path) -> UpdateEngine:
    """Instantiate an UpdateEngine bound to a temporary directory."""
    engine = UpdateEngine(update_dir=tmp_path / ".update")
    return engine


def make_context(
    tenant_id: str = "tenant-test",
    principal_id: str = "user-test",
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
    authenticated: bool = True,
) -> CapabilityExecutionContext | None:
    """Helper to build CapabilityExecutionContext."""
    if not authenticated:
        return None
    principal = SecurityPrincipal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        principal_type=PrincipalType.USER,
        roles=roles if roles is not None else ["TENANT_ADMIN"],
        attributes={"permissions": permissions or [PERMISSION_UPDATE_READ, PERMISSION_UPDATE_MANAGE]},
    )
    return CapabilityExecutionContext(
        request_id="req-123",
        correlation_id="corr-123",
        capability_name="kortex.update.test",
        principal=principal,
        tenant_id=tenant_id,
    )


@pytest.mark.asyncio
async def test_capabilities_registered(tmp_path: Path) -> None:
    """Verify that all 6 approved capabilities are registered during initialize()."""
    engine = UpdateEngine(update_dir=tmp_path / ".update")
    kernel = MagicMock()
    kernel.register_capability = MagicMock()

    await engine.initialize(kernel=kernel)
    assert kernel.register_capability.call_count == 6

    registered_names = {call.kwargs["name"] for call in kernel.register_capability.call_args_list}
    assert registered_names == set(ALL_UPDATE_CAPABILITIES)
    assert len(engine.capabilities()) == 6


@pytest.mark.asyncio
async def test_capability_authentication_gate(test_engine: UpdateEngine) -> None:
    """Verify that unauthenticated execution context is strictly rejected."""
    with pytest.raises(UpdateAuthenticationError):
        await test_engine.handle_update_check(
            execution_context=None,
        )


@pytest.mark.asyncio
async def test_capability_authorization_gate(test_engine: UpdateEngine) -> None:
    """Verify that execution context without required permission is strictly rejected."""
    # Context has only read permission and non-admin role, attempting manage capability
    read_only_ctx = make_context(roles=[], permissions=[PERMISSION_UPDATE_READ])

    with pytest.raises(UpdateAuthorizationError) as exc_info:
        await test_engine.handle_update_cancel(
            update_id="upd-01",
            execution_context=read_only_ctx,
        )
    assert "missing required permission" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_handle_check_capability(test_engine: UpdateEngine) -> None:
    """Verify kortex.update.check capability handler."""
    ctx = make_context(permissions=[PERMISSION_UPDATE_READ])

    res = await test_engine.handle_update_check(channel="stable", execution_context=ctx)
    assert res["current_version"] == "1.0.0"
    assert res["update_available"] is False  # No candidate manifest loaded


@pytest.mark.asyncio
async def test_handle_get_capability(test_engine: UpdateEngine) -> None:
    """Verify kortex.update.get capability handler."""
    ctx = make_context(permissions=[PERMISSION_UPDATE_READ])

    res = await test_engine.handle_update_get(execution_context=ctx)
    assert res["current_version"] == "1.0.0"
    assert res["active_update_id"] is None


@pytest.mark.asyncio
async def test_handle_cancel_capability(test_engine: UpdateEngine) -> None:
    """Verify kortex.update.cancel capability handler."""
    ctx = make_context(permissions=[PERMISSION_UPDATE_MANAGE])

    res = await test_engine.handle_update_cancel(update_id="upd-test-cancel", execution_context=ctx)
    assert res["update_id"] == "upd-test-cancel"
    assert res["cancelled"] is True


@pytest.mark.asyncio
async def test_diagnostics_structure_and_safety(test_engine: UpdateEngine) -> None:
    """Verify IEngineDiagnostics returns structured diagnostic data with zero secrets."""
    diag = test_engine.diagnostics()

    assert diag["engine"] == "update"
    assert diag["version"] == "1.0.0"
    assert diag["health"]["status"] in ("HEALTHY", "DEGRADED")
    assert "active_operation" in diag
    assert "has_active_journal" in diag

    # Verify no secret fields exist in details
    details_str = str(diag).lower()
    for sensitive in ["private_key", "password", "secret", "token", "credential"]:
        assert sensitive not in details_str
