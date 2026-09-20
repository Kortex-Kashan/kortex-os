// M4.1 session-custody commands. Mirrors `ipc/events.ts`'s pattern of a
// small, dedicated module per Rust command group rather than folding every
// Tauri command into `ipc/client.ts`. Rust (`apps/desktop/src-tauri/src/
// ipc.rs`) remains the sole custodian of the session token for its entire
// lifecycle — these two commands only ever expose a boolean or a
// side-effect, never the token value itself.

import { invoke } from "@tauri-apps/api/core";
import type { IpcResultEnvelope } from "./client";

/** Whether a session token is currently held in the OS-native credential
 * store. Never reveals the token itself — only its presence. */
export async function hasStoredSession(): Promise<boolean> {
  return invoke<boolean>("has_session");
}

/** Discards the held session token (logout). Phase F security correction:
 * Rust's `logout` command also discards the held refresh token, so a
 * single call here ends both credentials' usefulness. */
export async function clearStoredSession(): Promise<void> {
  await invoke("logout");
}

/**
 * Phase F security correction: exchanges the held refresh token for a
 * fresh access token via `kortex.security.auth.refresh`, exactly mirroring
 * `invoke_capability`'s own envelope contract — this module never sees
 * either token's value, only whether the exchange succeeded. Resolves to a
 * FAILURE envelope (never rejects) if no refresh token is held or the
 * backend refuses the exchange, matching `invoke_capability`'s own
 * never-throws discipline.
 */
export async function renewStoredSession(): Promise<IpcResultEnvelope> {
  return invoke<IpcResultEnvelope>("refresh_session");
}
