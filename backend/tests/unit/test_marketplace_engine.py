"""Unit tests for KORTEX OS Marketplace Engine facade (M7)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.exceptions import EngineStateError
from kortex.engines.marketplace.engine import MarketplaceEngine
from kortex.engines.marketplace.models import MarketplaceItemType, MarketplaceListing


def test_engine_properties_and_initial_state() -> None:
    engine = MarketplaceEngine()

    assert engine.name == "marketplace"
    assert engine.dependencies == []
    assert engine.state == EngineState.UNINITIALIZED
    assert engine.status() == "UNINITIALIZED"
    assert engine.version() == "1.0.0"
    assert engine.registry is not None


@pytest.mark.asyncio
async def test_initialize_registers_capability_and_transitions_to_ready() -> None:
    engine = MarketplaceEngine()
    mock_kernel = MagicMock()

    await engine.initialize(mock_kernel)

    assert engine.state == EngineState.READY
    registered_names = [call.kwargs["name"] for call in mock_kernel.register_capability.call_args_list]
    assert registered_names == ["kortex.marketplace.listing.list"]
    call_kwargs = mock_kernel.register_capability.call_args_list[0].kwargs
    assert call_kwargs["required_permissions"] == ["marketplace:read"]
    assert call_kwargs["handler"] == engine.list_listings


@pytest.mark.asyncio
async def test_duplicate_initialize_raises_state_error() -> None:
    engine = MarketplaceEngine()
    await engine.initialize(MagicMock())

    with pytest.raises(EngineStateError):
        await engine.initialize(MagicMock())


@pytest.mark.asyncio
async def test_start_and_stop_lifecycle_transitions() -> None:
    engine = MarketplaceEngine()
    await engine.initialize(MagicMock())

    await engine.start()
    assert engine.state == EngineState.RUNNING

    await engine.stop()
    assert engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_health_check_and_diagnostics_delegation() -> None:
    engine = MarketplaceEngine()
    await engine.initialize(MagicMock())
    await engine.start()

    health = await engine.health_check()
    assert health["status"] == "healthy"
    assert health["listing_count"] == 0

    diagnostics = engine.diagnostics()
    assert diagnostics["engine"] == "marketplace"
    assert diagnostics["capabilities"] == ["kortex.marketplace.listing.list"]


def test_list_listings_before_ready_raises_state_error() -> None:
    engine = MarketplaceEngine()
    with pytest.raises(EngineStateError):
        engine.list_listings()


@pytest.mark.asyncio
async def test_list_listings_returns_registered_data() -> None:
    engine = MarketplaceEngine()
    await engine.initialize(MagicMock())

    listing = MarketplaceListing(
        listing_id="listing-1",
        name="Sample Recipe Pack",
        description="A sample catalog entry.",
        version="1.0.0",
        item_type=MarketplaceItemType.RECIPE,
        publisher="KORTEX",
    )
    engine.registry.register_listing(listing)

    assert engine.list_listings() == [listing]
