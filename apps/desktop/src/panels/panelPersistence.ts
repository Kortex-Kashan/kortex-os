const STORAGE_KEY = "kortex.panels.v1";

/** The persisted subset of panel state — open/closed and sizes only. */
export interface PersistedPanelState {
  openPanelIds: string[];
  sizes: Record<string, number>;
}

function isPersistedPanelState(value: unknown): value is PersistedPanelState {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    Array.isArray(candidate.openPanelIds) &&
    candidate.openPanelIds.every((id) => typeof id === "string") &&
    typeof candidate.sizes === "object" &&
    candidate.sizes !== null &&
    Object.values(candidate.sizes as Record<string, unknown>).every((size) => typeof size === "number")
  );
}

/**
 * localStorage foundation for panel open/closed state and sizes — no
 * backend, per M2.4 scope. Reads and writes are wrapped defensively: a
 * disabled/unavailable localStorage (privacy mode) or corrupted JSON must
 * degrade to "no persisted state" rather than crash the workspace.
 */
export function loadPanelState(): PersistedPanelState | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    return isPersistedPanelState(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function savePanelState(state: PersistedPanelState): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Best-effort persistence — a full or unavailable localStorage must
    // never break panel open/close or resize interaction.
  }
}
