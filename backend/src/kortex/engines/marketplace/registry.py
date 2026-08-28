"""In-memory Marketplace catalog registry.

Deliberately as small as `kortex.engines.connector.registry`'s own
constructor-time state: a thread-safe dict keyed by `listing_id`. No
persistence, no versioning/SemVer resolution, no dependency graph — M7 is
a read-only catalog visibility slice, not a distribution system. An empty
registry is the correct, honest starting state (no seed data is fabricated
here or anywhere in this engine).
"""

from __future__ import annotations

import threading

from kortex.engines.marketplace.models import MarketplaceListing


class MarketplaceCatalogRegistry:
    """Thread-safe in-memory store of `MarketplaceListing` entries."""

    def __init__(self) -> None:
        self._listings: dict[str, MarketplaceListing] = {}
        self._lock = threading.RLock()

    def register_listing(self, listing: MarketplaceListing) -> MarketplaceListing:
        """Add a listing to the catalog.

        Not exposed as a Kernel capability in M7 — publishing is
        explicitly out of scope. This exists only so tests (and, later,
        a real publishing milestone) have a way to populate the catalog.
        """
        with self._lock:
            self._listings[listing.listing_id] = listing
            return listing

    def list_listings(self) -> list[MarketplaceListing]:
        """Return every registered listing."""
        with self._lock:
            return list(self._listings.values())

    def clear(self) -> None:
        """Remove every listing from the catalog."""
        with self._lock:
            self._listings.clear()


__all__ = ["MarketplaceCatalogRegistry"]
