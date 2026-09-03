"""
Unit tests for ProcessIntelligenceEngine lifecycle, diagnostics, and capability registration.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kortex.core.base_engine import EngineState
from kortex.engines.process_intelligence.engine import ProcessIntelligenceEngine


@pytest.mark.asyncio
async def test_engine_lifecycle_and_diagnostics() -> None:
    engine = ProcessIntelligenceEngine()

    assert engine.name == "process_intelligence"
    assert engine.dependencies == ["storage", "registry", "configuration"]
    assert engine.state == EngineState.UNINITIALIZED

    # Mock Kernel and Storage
    mock_kernel = MagicMock()
    mock_storage = MagicMock()
    mock_data_store = MagicMock()
    mock_storage.data = mock_data_store
    mock_kernel.get_engine.return_value = mock_storage

    # Initialize
    await engine.initialize(mock_kernel)
    assert engine.state == EngineState.READY

    # Verify 4 capabilities registered with requires_execution_context=True
    assert mock_kernel.register_capability.call_count == 4
    registered_names = [call.kwargs["name"] for call in mock_kernel.register_capability.call_args_list]
    assert "kortex.process_intelligence.summary.get" in registered_names
    assert "kortex.process_intelligence.bottlenecks.get" in registered_names
    assert "kortex.process_intelligence.process_graph.get" in registered_names
    assert "kortex.process_intelligence.variants.list" in registered_names

    for call in mock_kernel.register_capability.call_args_list:
        assert call.kwargs["requires_execution_context"] is True
        assert call.kwargs["required_permissions"] == ["workflow:read"]

    # Start
    await engine.start()
    assert engine.state == EngineState.RUNNING

    # Health check
    health = await engine.health_check()
    assert health["engine"] == "process_intelligence"
    assert health["status"] == "healthy"
    assert health["state"] == "RUNNING"
    assert health["queries_executed_total"] == 0

    # Stop
    await engine.stop()
    assert engine.state == EngineState.STOPPED
