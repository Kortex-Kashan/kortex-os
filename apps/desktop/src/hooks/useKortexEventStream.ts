import { useEffect } from "react";
import { connectEventStream, onKortexEvent } from "@/ipc/events";
import { queryClient } from "@/lib/queryClient";

/** Centralizes the M3 event subscription (§13.1.4) — mounted once in
 * `App.tsx`, never per-feature. `connectEventStream` no-ops until a
 * session token exists, so this is safe to mount before any sign-in. */
export function useKortexEventStream() {
  useEffect(() => {
    let unlisten: (() => void) | undefined;

    void connectEventStream();
    void onKortexEvent((event) => {
      // §12.3's cache-invalidation-on-event mechanism: invalidate any
      // cached query keyed by the event's own topic. No real screen
      // queries by topic yet (no capability-backed query exists in this
      // codebase today) — this is the mechanism, not a claim that a
      // feature currently relies on it.
      queryClient.invalidateQueries({ queryKey: [event.topic] });
    }).then((fn) => {
      unlisten = fn;
    });

    return () => unlisten?.();
  }, []);
}
