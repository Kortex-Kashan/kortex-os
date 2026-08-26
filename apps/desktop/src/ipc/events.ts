// M3 event relay, frontend half. Mirrors `apps/desktop/src-tauri/src/events.rs`:
// the webview never opens its own WebSocket connection (§11.1's network
// egress isolation) — it only listens for the two Tauri events Rust
// re-emits after relaying the backend's `WS /events/stream`.
//
// `connectEventStream` is a thin wrapper around the `connect_event_stream`
// command, which itself no-ops silently if no session token is held yet
// (see `events.rs::start_event_relay`) — there is no login screen in this
// codebase yet to call it after a real sign-in, so this is wired as an
// available, centrally-callable primitive (per §13.1.4, "the frontend
// subscribes once, centrally, in `app/`"), not yet exercised by a real
// authenticated user flow.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export interface KortexEvent {
  eventId: string;
  topic: string;
  payload: Record<string, unknown>;
  correlationId: string;
  timestampUtc: string;
}

export type EventStreamStatus = "connecting" | "connected" | "reconnecting" | "disconnected";

/** Starts the backend event relay. Safe to call more than once — Rust
 * treats a second call as a no-op rather than a duplicate subscription. */
export async function connectEventStream(topic?: string): Promise<boolean> {
  return invoke<boolean>("connect_event_stream", { topic });
}

export function onKortexEvent(handler: (event: KortexEvent) => void): Promise<UnlistenFn> {
  return listen<KortexEvent>("kortex://event", (event) => handler(event.payload));
}

export function onEventStreamStatus(handler: (status: EventStreamStatus) => void): Promise<UnlistenFn> {
  return listen<EventStreamStatus>("kortex://event-stream-status", (event) => handler(event.payload));
}
