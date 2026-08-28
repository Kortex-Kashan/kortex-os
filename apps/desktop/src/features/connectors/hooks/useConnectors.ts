import { useQuery } from "@tanstack/react-query";
import { ConnectorAccessDeniedError, listConnectorDrivers } from "../api";

/** Not keyed by an event topic (compare `useKortexEventStream.ts`'s
 * topic-keyed invalidation): no Connector Engine event is wired to trigger
 * an automatic refresh in this milestone, so "refresh" is the explicit,
 * user-triggered `refetch()` this hook already exposes via TanStack Query —
 * not a second, bespoke state machine. */
export const CONNECTORS_QUERY_KEY = ["connectors", "drivers"] as const;

/**
 * Server-derived state for the Connectors workspace, per ADR-0002 §12
 * (TanStack Query owns all server-derived state; never mixed with Zustand
 * local UI state). Exposes exactly TanStack Query's own
 * loading/success/error/refetch surface — `ConnectorsApp` derives "empty"
 * itself from `data.length === 0` rather than this hook inventing a
 * redundant status value.
 */
export function useConnectors() {
  return useQuery({
    queryKey: CONNECTORS_QUERY_KEY,
    queryFn: listConnectorDrivers,
    // An access-denied result is deterministic — the global default
    // (`lib/queryClient.ts`, `retry: 1`) would otherwise burn one wasted
    // round-trip before ever showing the access-denied state.
    retry: (failureCount, error) => !(error instanceof ConnectorAccessDeniedError) && failureCount < 1,
  });
}
