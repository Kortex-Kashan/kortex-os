// M3 event relay, frontend half. Mirrors `apps/desktop/src-tauri/src/events.rs`:
// the webview never opens its own WebSocket connection (§11.1's network
// egress isolation) — it only listens for the two Tauri events Rust
// re-emits after relaying the backend's `WS /events/stream`.
//
// `connectEventStream` is a thin wrapper around the `connect_event_stream`
// command, which itself no-ops silently if no session token is held yet
// (see `events.rs::start_event_relay`), and is a no-op while a relay is
// already running. It is called once at app mount (`useKortexEventStream`)
// and again whenever `AuthProvider` observes the session become
// AUTHENTICATED — the mount-time call alone never connects when the app
// starts signed out, which left the relay (and with it the Browser
// execution bridge, `browser_bridge.rs`) permanently disconnected.

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
