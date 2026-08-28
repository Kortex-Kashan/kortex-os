import { invokeCapability } from "@/ipc/client";
import type { MarketplaceItemStatus, MarketplaceItemType, MarketplaceListing } from "./types";

const LISTING_LIST_CAPABILITY = "kortex.marketplace.listing.list";

/**
 * Thrown when the backend denies the call with `PERMISSION_DENIED` — see
 * `apps/desktop/src/features/connectors/api.ts`'s `ConnectorAccessDeniedError`
 * for why this stays a single, unified category (the IPC transport doesn't
 * distinguish 401 from 403 in `errors[].category`, only in the newer
 * `httpStatus` field this feature does not yet consume).
 */
export class MarketplaceAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MarketplaceAccessDeniedError";
  }
}

/** Any other `FAILURE` envelope — a generic, recoverable failure. */
export class MarketplaceRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MarketplaceRequestError";
  }
}

/** Raw wire shape of one entry in the capability's `result` array —
 * snake_case, since `MarketplaceListing` has no camelCase alias generator
 * on the Python side (matching `DriverMetadata`/`WorkflowDefinition`'s own
 * wire shape in M5/M6). Only the fields this model actually declares are
 * read — an unexpected extra field on the wire (e.g. a future backend
 * regression that added a credential field) is never forwarded, because
 * `toMarketplaceListing` builds its result field-by-field rather than
 * spreading the raw object. */
interface RawMarketplaceListing {
  listing_id: string;
  name: string;
  description: string;
  version: string;
  item_type: string;
  publisher: string;
  status: string;
  compatibility?: string;
}

function toMarketplaceListing(raw: RawMarketplaceListing): MarketplaceListing {
  return {
    listingId: raw.listing_id,
    name: raw.name,
    description: raw.description,
    version: raw.version,
    itemType: raw.item_type as MarketplaceItemType,
    publisher: raw.publisher,
    status: raw.status as MarketplaceItemStatus,
    compatibility: raw.compatibility ?? "",
  };
}

/**
 * Calls the existing `kortex.marketplace.listing.list` capability through
 * the existing generic IPC path (React -> `ipc/client.ts` -> Tauri
 * `invoke_capability` -> Rust -> backend `CapabilityDispatcher`). No
 * dedicated Tauri command is introduced.
 */
export async function listMarketplaceListings(): Promise<MarketplaceListing[]> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: LISTING_LIST_CAPABILITY,
    parameters: {},
  });

  if (envelope.status === "SUCCESS") {
    const raw = (envelope.payload?.result as RawMarketplaceListing[] | undefined) ?? [];
    return raw.map(toMarketplaceListing);
  }

  const failure = envelope.errors[0];
  const message = failure?.message ?? "Failed to load the marketplace catalog.";
  if (failure?.category === "PERMISSION_DENIED") {
    throw new MarketplaceAccessDeniedError(message);
  }
  throw new MarketplaceRequestError(message);
}
