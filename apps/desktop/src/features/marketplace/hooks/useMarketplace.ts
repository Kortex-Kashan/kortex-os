import { useQuery } from "@tanstack/react-query";
import { listMarketplaceListings, MarketplaceAccessDeniedError } from "../api";

/** No Marketplace Engine event is wired to trigger an automatic refresh in
 * this milestone (see `apps/desktop/src/hooks/useKortexEventStream.ts`) —
 * "refresh" is the explicit, user-triggered `refetch()` this hook exposes
 * via TanStack Query. */
export const MARKETPLACE_QUERY_KEY = ["marketplace", "listings"] as const;

/**
 * Server-derived state for the Marketplace workspace, per ADR-0002 §12
 * (TanStack Query owns all server-derived state). Exposes exactly
 * TanStack Query's own loading/success/error/refetch surface —
 * `MarketplaceApp` derives "empty" itself from `data.length === 0`.
 */
export function useMarketplace() {
  return useQuery({
    queryKey: MARKETPLACE_QUERY_KEY,
    queryFn: listMarketplaceListings,
    // An access-denied result is deterministic — see
    // `features/connectors/hooks/useConnectors.ts`'s identical rationale.
    retry: (failureCount, error) => !(error instanceof MarketplaceAccessDeniedError) && failureCount < 1,
  });
}
