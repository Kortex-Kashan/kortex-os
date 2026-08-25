import type { Theme } from "@/stores/uiStore";

/**
 * Bumped whenever KortexSession's shape changes in a way SessionManager
 * must migrate old persisted data for. SessionManager checks this on
 * restore rather than trusting persisted data blindly (see
 * SessionManager.ts's `restoreSession`). Bumped 1 -> 2 when the
 * `panelState` read-model mirror was removed (ADR-0003) — a v1 session
 * would otherwise carry a stale, silently-ignored `panelState` field
 * forever; treating it as an incompatible version discards it cleanly
 * instead.
 */
export const CURRENT_SESSION_VERSION = 2;
export type SessionVersion = number;

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
 * theme, and UI preferences only. Never API keys, tokens, credentials, or
 * backend/business data (AGENTS.md Security; ADR-0002 §11.7 — secrets
 * never cross into the webview). Panel layout is deliberately NOT part of
 * this document: PanelProvider's own "kortex.panels.v1" key
 * (panels/panelPersistence.ts) is the single source of truth for panel
 * open/size state — an earlier read-model mirror of it here was removed
 * as a stabilization fix (ADR-0003) once it was identified as a second,
 * independently-written copy of the same data with no ordering guarantee
 * between the two.
 */
export interface KortexSession {
  version: SessionVersion;
  activeApplication: string | null;
  theme: Theme;
  preferences: SessionPreferences;
  /** ISO-8601 timestamp of the last update, set by SessionManager. */
  updatedAt: string;
}
