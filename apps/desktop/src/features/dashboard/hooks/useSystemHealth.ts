import { useQuery } from "@tanstack/react-query";

import { getSystemHealth } from "../api";

export const SYSTEM_HEALTH_QUERY_KEY = ["dashboard", "system-health"] as const;

// Polls at a light interval so the overview stays current without the
// user needing to remember to refresh — on top of (not instead of) the
// manual Refresh control the Dashboard also exposes. Refetches replace the
// query's data in place; they never fall back through an empty/loading
// state (see `useQuery`'s own `isPending`/`isFetching` distinction, which
// is what makes that safe).
const REFETCH_INTERVAL_MS = 30_000;

/**
 * Wraps `getSystemHealth` in the app's shared TanStack Query v5 client.
 *
 * `isPending` (not `isLoading`) is the correct "nothing to show yet" check:
 * v5's `isLoading` is `isPending && isFetching`, which already only holds
 * during the initial unsettled fetch — including through the global
 * `retry: 1` backoff window, since `isFetching` stays true for the whole
 * retry sequence. Either one is safe here for that reason; `isPending` is
 * used directly so a slow-appearing loading state can never be mistaken
 * for "no data" once a background refetch is in flight for existing data.
 */
export function useSystemHealth() {
  return useQuery({
    queryKey: SYSTEM_HEALTH_QUERY_KEY,
    queryFn: getSystemHealth,
    refetchInterval: REFETCH_INTERVAL_MS,
  });
}
