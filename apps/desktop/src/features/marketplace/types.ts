/**
 * Mirrors `kortex.engines.marketplace.models.MarketplaceListing`
 * (backend/src/kortex/engines/marketplace/models.py) field-for-field — the
 * exact shape `kortex.marketplace.listing.list` returns. Every field on
 * that backend model is already display-safe by design (no credential,
 * secret, token, or license-key field exists on it) — this type is a
 * one-to-one mirror, not a filtered subset.
 */
export type MarketplaceItemType = "RECIPE" | "TEMPLATE" | "CONNECTOR" | "MODULE" | "KNOWLEDGE_PACK" | "THEME";

export type MarketplaceItemStatus = "AVAILABLE" | "DEPRECATED";

export interface MarketplaceListing {
  listingId: string;
  name: string;
  description: string;
  version: string;
  itemType: MarketplaceItemType;
  publisher: string;
  status: MarketplaceItemStatus;
  compatibility: string;
}
