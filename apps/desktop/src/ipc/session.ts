// M4.1 session-custody commands. Mirrors `ipc/events.ts`'s pattern of a
// small, dedicated module per Rust command group rather than folding every
// Tauri command into `ipc/client.ts`. Rust (`apps/desktop/src-tauri/src/
// ipc.rs`) remains the sole custodian of the session token for its entire
// lifecycle — these two commands only ever expose a boolean or a
// side-effect, never the token value itself.

import { invoke } from "@tauri-apps/api/core";

/** Whether a session token is currently held in the OS-native credential
 * store. Never reveals the token itself — only its presence. */
export async function hasStoredSession(): Promise<boolean> {
  return invoke<boolean>("has_session");
}

/** Discards the held session token (logout). */
export async function clearStoredSession(): Promise<void> {
  await invoke("logout");
}
