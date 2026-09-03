"""
Unit tests for Process Intelligence security invariants and adversarial boundaries.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.process_intelligence.engine import ProcessIntelligenceEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.models import PrincipalType, SecurityPrincipal


@pytest.fixture
def mock_engine() -> ProcessIntelligenceEngine:
    engine = ProcessIntelligenceEngine()
    engine._data_store = MagicMock()
    engine._analyzer = MagicMock()
    engine._miner = MagicMock()
    return engine


@pytest.mark.asyncio
async def test_tenant_mismatch_raises_authorization_denied(
    mock_engine: ProcessIntelligenceEngine,
) -> None:
    # Principal is locked to tenant_A
    principal = SecurityPrincipal(
        principal_id="user_1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_A",
        roles=["analyst"],
    )
    context = CapabilityExecutionContext(
        request_id="req_1",
        correlation_id="corr_1",
        capability_name="kortex.process_intelligence.summary.get",
        principal=principal,
        tenant_id="tenant_A",
        session_token=None,
    )

    # Caller attempts to query tenant_B
    with pytest.raises(AuthorizationDeniedError) as exc_info:
        await mock_engine.get_summary(
            execution_context=context,
            definition_id="flow_1",
            tenant_id="tenant_B",  # Spoofed tenant parameter
        )

    assert "does not match the authenticated tenant" in str(exc_info.value)


@pytest.mark.asyncio
async def test_missing_execution_context_raises_authorization_denied(
    mock_engine: ProcessIntelligenceEngine,
) -> None:
    # In case execution_context is None
    with pytest.raises(AuthorizationDeniedError):
        await mock_engine.get_summary(
            execution_context=None,  # type: ignore[arg-type]
            definition_id="flow_1",
        )


@pytest.mark.asyncio
async def test_matching_tenant_id_succeeds(
    mock_engine: ProcessIntelligenceEngine,
) -> None:
    principal = SecurityPrincipal(
        principal_id="user_1",
        principal_type=PrincipalType.USER,
        tenant_id="tenant_A",
        roles=["analyst"],
    )
    context = CapabilityExecutionContext(
        request_id="req_2",
        correlation_id="corr_2",
        capability_name="kortex.process_intelligence.summary.get",
        principal=principal,
        tenant_id="tenant_A",
        session_token=None,
    )

    mock_engine._get_scoped_repository = MagicMock()
    mock_repo = AsyncMock()
    mock_engine._get_scoped_repository.return_value = mock_repo

    await mock_engine.get_summary(
        execution_context=context,
        definition_id="flow_1",
        tenant_id="tenant_A",  # Explicit matching tenant
    )

    mock_engine._get_scoped_repository.assert_called_once_with(context, caller_tenant_id="tenant_A")
