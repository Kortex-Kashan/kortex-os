"""Unit tests for `kortex.engines.marketplace.registry.MarketplaceCatalogRegistry`."""

from __future__ import annotations

from kortex.engines.marketplace.models import MarketplaceItemStatus, MarketplaceItemType, MarketplaceListing
from kortex.engines.marketplace.registry import MarketplaceCatalogRegistry


def _listing(listing_id: str = "listing-1") -> MarketplaceListing:
    return MarketplaceListing(
        listing_id=listing_id,
        name="Sample Recipe Pack",
        description="A sample catalog entry.",
        version="1.0.0",
        item_type=MarketplaceItemType.RECIPE,
        publisher="KORTEX",
    )


def test_registry_starts_empty() -> None:
    registry = MarketplaceCatalogRegistry()
    assert registry.list_listings() == []


def test_register_and_list_listing() -> None:
    registry = MarketplaceCatalogRegistry()
    listing = _listing()

    result = registry.register_listing(listing)

    assert result == listing
    assert registry.list_listings() == [listing]


def test_register_overwrites_same_listing_id() -> None:
    registry = MarketplaceCatalogRegistry()
    registry.register_listing(_listing())
    updated = _listing().model_copy(update={"version": "2.0.0"})

    registry.register_listing(updated)
    listings = registry.list_listings()

    assert len(listings) == 1
    assert listings[0].version == "2.0.0"


def test_clear_removes_all_listings() -> None:
    registry = MarketplaceCatalogRegistry()
    registry.register_listing(_listing("listing-1"))
    registry.register_listing(_listing("listing-2"))

    registry.clear()

    assert registry.list_listings() == []


def test_default_status_is_available() -> None:
    listing = _listing()
    assert listing.status == MarketplaceItemStatus.AVAILABLE
