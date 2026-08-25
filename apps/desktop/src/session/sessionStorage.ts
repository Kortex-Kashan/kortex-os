import type { KortexSession, SessionPreferences } from "./sessionTypes";

const STORAGE_KEY = "kortex.session.v1";

function isSessionPreferences(value: unknown): value is SessionPreferences {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return typeof candidate.sidebarCollapsed === "boolean";
}

function isKortexSession(value: unknown): value is KortexSession {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.version === "number" &&
    (candidate.activeApplication === null || typeof candidate.activeApplication === "string") &&
    (candidate.theme === "light" || candidate.theme === "dark") &&
    isSessionPreferences(candidate.preferences) &&
    typeof candidate.updatedAt === "string"
  );
}

/**
 * localStorage abstraction for the KORTEX session document — no backend,
 * mirrors panels/panelPersistence.ts. Reads and writes are wrapped
 * defensively: a disabled/unavailable localStorage (privacy mode) or
 * corrupted/malformed JSON must degrade to "no persisted session" rather
 * than crash the OS. Only ever stores the shape declared in
 * sessionTypes.ts — workspace/layout/UI state, never API keys, tokens,
 * credentials, or backend data.
 */
export function loadSession(): KortexSession | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    return isKortexSession(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function saveSession(session: KortexSession): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Best-effort persistence — a full or unavailable localStorage must
    // never break session restore/update.
  }
}

export function clearSession(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Best-effort — see saveSession.
  }
}
