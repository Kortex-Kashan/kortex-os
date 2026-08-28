"""Pydantic v2 data models for the KORTEX OS Marketplace Engine.

M7 scope only: a read-only catalog listing. Deliberately excludes every
field `docs/architecture/marketplace_architecture.md` describes for later
milestones — signatures, checksums, pricing/licensing, installation
credentials, dependency graphs — none of that exists yet, and this model
must not imply otherwise by carrying placeholder fields for it.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MarketplaceItemType(str, Enum):
    """Canonical asset type categories a catalog listing may declare.

    A subset of `marketplace_architecture.md` §3's 11 canonical asset
    types — only the categories a listing can be tagged with for
    display purposes today. Adding the remaining types later is a
    additive, backwards-compatible change, not a redesign.
    """

    RECIPE = "RECIPE"
    TEMPLATE = "TEMPLATE"
    CONNECTOR = "CONNECTOR"
    MODULE = "MODULE"
    KNOWLEDGE_PACK = "KNOWLEDGE_PACK"
    THEME = "THEME"


class MarketplaceItemStatus(str, Enum):
    """Display-only lifecycle status for a catalog listing.

    No update/deprecation *behavior* is implemented in M7 (see module
    docstring) — this is metadata a listing can carry, not a state
    machine this engine enforces or transitions.
    """

    AVAILABLE = "AVAILABLE"
    DEPRECATED = "DEPRECATED"


class MarketplaceListing(BaseModel):
    """Immutable, display-safe catalog entry.

    Every field here is safe to render verbatim in a UI — there is no
    credential, secret, token, license key, or connection-configuration
    field on this model, by design (see `docs/security/` conventions
    followed by `DriverMetadata`/`WorkflowDefinition` in M5/M6, which
    this model mirrors).
    """

    model_config = ConfigDict(frozen=True)

    listing_id: str
    name: str
    description: str
    version: str
    item_type: MarketplaceItemType
    publisher: str
    status: MarketplaceItemStatus = MarketplaceItemStatus.AVAILABLE
    compatibility: str = Field(
        default="",
        description="Free-text compatibility note (e.g. a minimum Kernel version) — not a resolved dependency graph.",
    )


__all__ = ["MarketplaceItemStatus", "MarketplaceItemType", "MarketplaceListing"]
