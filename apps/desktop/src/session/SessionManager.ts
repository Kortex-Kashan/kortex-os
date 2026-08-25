import {
  clearSession as clearPersistedSession,
  loadSession as loadPersistedSession,
  saveSession as savePersistedSession,
} from "./sessionStorage";
import {
  CURRENT_SESSION_VERSION,
  DEFAULT_SESSION_PREFERENCES,
  type KortexSession,
} from "./sessionTypes";

/** Fields SessionManager computes itself — callers never set these directly. */
type ComputedField = "version" | "updatedAt";

export type SessionUpdate = Partial<Omit<KortexSession, ComputedField>>;

function defaultSession(): KortexSession {
  return {
    version: CURRENT_SESSION_VERSION,
    activeApplication: null,
    panelState: { openPanelIds: [], sizes: {} },
    theme: "light",
    preferences: { ...DEFAULT_SESSION_PREFERENCES },
    updatedAt: new Date().toISOString(),
  };
}

/**
 * Owns the in-memory KortexSession and its localStorage round-trip.
 * Deliberately a plain, instantiable class rather than a module-level
 * singleton — mirrors WorkspaceRegistry/PanelRegistry's own reasoning (ES
 * module caching would make a singleton a de facto global shared by every
 * importer, which AGENTS.md's Dependency Injection rules forbid).
 * SessionProvider constructs exactly one instance per app via a ref, the
 * same way WorkspaceProvider/PanelProvider own their registries.
 */
export class SessionManager {
  private session: KortexSession | null = null;

  /** Creates a brand-new default session, persists it, and makes it current. */
  createSession(): KortexSession {
    this.session = defaultSession();
    savePersistedSession(this.session);
    return this.session;
  }

  /**
   * Loads a previously persisted session. Returns null — never throws —
   * if none exists, storage is unavailable/corrupted (sessionStorage's own
   * defensive parsing already handles that), or the persisted version
   * doesn't match CURRENT_SESSION_VERSION. Only version 1 exists today, so
   * a mismatch currently just means "discard and let the caller create a
   * fresh session"; this is the seam a future migration (e.g. "if
   * persisted.version === 1, upgrade to 2 instead of discarding") plugs
   * into once CURRENT_SESSION_VERSION advances.
   */
  restoreSession(): KortexSession | null {
    const persisted = loadPersistedSession();
    if (!persisted || persisted.version !== CURRENT_SESSION_VERSION) {
      return null;
    }
    this.session = persisted;
    return persisted;
  }

  /**
   * Merges a partial update onto the current session — creating a default
   * one first if nothing is active yet — recomputes `version`/`updatedAt`,
   * and persists the result. `preferences` is shallow-merged (a partial
   * preferences update must not silently reset the rest of the bag);
   * every other field is a full replacement, since callers (SessionSync)
   * always compute those wholesale.
   */
  updateSession(update: SessionUpdate): KortexSession {
    const base = this.session ?? this.createSession();
    this.session = {
      ...base,
      ...update,
      preferences: update.preferences
        ? { ...base.preferences, ...update.preferences }
        : base.preferences,
      version: CURRENT_SESSION_VERSION,
      updatedAt: new Date().toISOString(),
    };
    savePersistedSession(this.session);
    return this.session;
  }

  /** Discards the current session, both in memory and in localStorage. */
  clearSession(): void {
    this.session = null;
    clearPersistedSession();
  }

  /** The current in-memory session, or null if none has been created/restored yet. */
  getSession(): KortexSession | null {
    return this.session;
  }
}
