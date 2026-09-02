// M7.1 bounded backend-startup readiness polling.
//
// Before this module existed, `AuthProvider.tsx`'s startup effect made
// exactly one attempt to validate a stored session and, on any failure,
// landed in a static BACKEND_UNAVAILABLE state with no automatic recovery
// — meaning the desktop app launching before the newly-auto-spawned
// backend finished booting (which it always will, briefly, at minimum) had
// no way to reach the app without the user manually retrying. This module
// is the fix: bounded, exponential-backoff polling of the already-existing
// `GET /health` surface (via `fetchSystemHealth`/`ipc.rs::get_system_health`
// — no new Tauri command), reusing rather than reinventing the exact
// health-check plumbing that already existed unused for this purpose.
//
// Also reads `/health`'s `bootstrap_required` flag (Milestone M7.1,
// `Kernel.health_check`) — the canonical, backend-authoritative signal for
// whether first-run setup is still needed, never guessed at from the
// frontend.

import { fetchSystemHealth } from "@/ipc/client";

export interface BackendReadyOutcome {
  ready: true;
  bootstrapRequired: boolean;
}

export interface BackendNotReadyOutcome {
  ready: false;
}

export type BackendReadinessOutcome = BackendReadyOutcome | BackendNotReadyOutcome;

// 8 attempts with a 250ms initial backoff doubling up to a 5s cap resolves
// in ~19s worst case (250+500+1000+2000+4000+5000+5000+5000ms of waiting
// between attempts) — long enough to ride out a normal backend boot
// (engine initialization, DB table creation) without looking hung, short
// enough that a genuinely broken install fails closed to a clear,
// recoverable error state instead of leaving the user staring at a spinner
// indefinitely. Never infinite, never busy-looping (every wait is a real
// timer delay, not a tight poll).
const DEFAULT_MAX_ATTEMPTS = 8;
const INITIAL_BACKOFF_MS = 250;
const MAX_BACKOFF_MS = 5_000;
const BACKOFF_MULTIPLIER = 2;

function backoffForAttempt(attempt: number): number {
  const delay = INITIAL_BACKOFF_MS * BACKOFF_MULTIPLIER ** (attempt - 1);
  return Math.min(delay, MAX_BACKOFF_MS);
}

function parseBootstrapRequired(body: unknown): boolean {
  if (!body || typeof body !== "object") {
    return false;
  }
  const candidate = body as Record<string, unknown>;
  return candidate.bootstrap_required === true;
}

export interface WaitForBackendReadyOptions {
  maxAttempts?: number;
  /** Called before each attempt (including the first) — lets the caller
   * render live progress (`STARTING`'s `attempt`/`maxAttempts`). */
  onAttempt?: (attempt: number, maxAttempts: number) => void;
  /** Injectable delay function — real `setTimeout`-backed by default;
   * tests supply a fake to make backoff waiting deterministic and instant
   * rather than actually sleeping for up to 19 real seconds. */
  sleep?: (ms: number) => Promise<void>;
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Polls `GET /health` with bounded exponential backoff until it succeeds
 * or `maxAttempts` is exhausted. Never throws — every failure mode
 * (backend unreachable, an unparseable response, a thrown promise from the
 * IPC bridge itself) is treated identically as "not ready yet" for this
 * attempt.
 */
export async function waitForBackendReady(
  options: WaitForBackendReadyOptions = {},
): Promise<BackendReadinessOutcome> {
  const maxAttempts = options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
  const sleep = options.sleep ?? defaultSleep;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    options.onAttempt?.(attempt, maxAttempts);

    let outcome: Awaited<ReturnType<typeof fetchSystemHealth>>;
    try {
      outcome = await fetchSystemHealth();
    } catch {
      outcome = { ok: false };
    }

    if (outcome.ok) {
      return { ready: true, bootstrapRequired: parseBootstrapRequired(outcome.body) };
    }

    if (attempt < maxAttempts) {
      await sleep(backoffForAttempt(attempt));
    }
  }

  return { ready: false };
}
