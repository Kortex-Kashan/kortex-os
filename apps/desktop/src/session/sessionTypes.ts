import type { Theme } from "@/stores/uiStore";

/**
 * Bumped whenever KortexSession's shape changes in a way SessionManager
 * must migrate old persisted data for. SessionManager checks this on
 * restore rather than trusting persisted data blindly (see
 * SessionManager.ts's `restoreSession`).
 */
export const CURRENT_SESSION_VERSION = 1;
export type SessionVersion = number;

/**
 * Mirrors PersistedPanelState (panels/panelPersistence.ts) — panel
 * open/closed ids and sizes only — folded into the unified session
 * document. PanelProvider remains the single writer of the *authoritative*
 * panel state (its own "kortex.panels.v1" key, untouched by this
 * milestone); this is a read-model mirror SessionSync keeps in sync so a
 * full session snapshot exists in one place.
 */
export interface SessionPanelState {
  openPanelIds: string[];
  sizes: Record<string, number>;
}

/**
 * UI-only preferences with no dedicated top-level KortexSession field of
 * their own. `sidebarCollapsed` is declared and fully round-tripped by
 * SessionManager now, ahead of the DesktopShell wiring that will consume
 * it — the same "declare now, wire to a real consumer later" precedent
 * already used by WorkspaceApplication.permissions and
 * PanelDefinition.permissions.
 */
export interface SessionPreferences {
  sidebarCollapsed: boolean;
}

export const DEFAULT_SESSION_PREFERENCES: SessionPreferences = {
  sidebarCollapsed: false,
};

/**
 * The complete persisted KORTEX workspace session: active application,
 * panel layout, theme, and UI preferences only. Never API keys, tokens,
 * credentials, or backend/business data (AGENTS.md Security; ADR-0002
 * §11.7 — secrets never cross into the webview).
 */
export interface KortexSession {
  version: SessionVersion;
  activeApplication: string | null;
  panelState: SessionPanelState;
  theme: Theme;
  preferences: SessionPreferences;
  /** ISO-8601 timestamp of the last update, set by SessionManager. */
  updatedAt: string;
}
