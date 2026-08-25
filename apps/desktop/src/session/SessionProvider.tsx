import * as React from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { resolveRouteForApplicationId, WORKSPACE_ROOT_ROUTE } from "@/navigation/applicationRouter";
import { useUiStore } from "@/stores/uiStore";
import { useWorkspace } from "@/workspace/WorkspaceProvider";

import { SessionManager, type SessionUpdate } from "./SessionManager";
import type { KortexSession } from "./sessionTypes";

interface SessionContextValue {
  session: KortexSession;
  updateSession: (update: SessionUpdate) => void;
  clearSession: () => void;
}

const SessionContext = React.createContext<SessionContextValue | null>(null);

export function useSession(): SessionContextValue {
  const context = React.useContext(SessionContext);
  if (!context) {
    throw new Error("useSession must be used within a SessionProvider");
  }
  return context;
}

export interface SessionProviderProps {
  children: React.ReactNode;
}

/**
 * Plain React Context + useState — no Zustand, per the M2.5 task brief
 * (mirrors WorkspaceProvider/PanelProvider's own reasoning for a
 * dedicated context rather than folding this into uiStore or a new
 * store). Owns exactly one SessionManager instance, ref-guarded against
 * StrictMode's double-invocation of render-phase code — the same pattern
 * WorkspaceProvider's registryRef and PanelProvider's registryRef use.
 *
 * Mounted outermost (routes/index.tsx: SessionProvider > WorkspaceProvider
 * > PanelProvider > DesktopShell) so it can restore the session before any
 * of those providers render. It only handles theme restoration directly,
 * though — restoring the active application requires the workspace's
 * application registry, and mirroring live workspace state back into the
 * session requires its context, neither of which exist at this level.
 * Both are handled by `SessionSync` below, mounted one layer further in.
 * Panel layout is intentionally out of scope here — PanelProvider owns
 * its own persistence independently (see sessionTypes.ts's KortexSession
 * doc comment / ADR-0003).
 */
export function SessionProvider({ children }: SessionProviderProps) {
  const managerRef = React.useRef<SessionManager | null>(null);
  if (!managerRef.current) {
    managerRef.current = new SessionManager();
  }
  const manager = managerRef.current;

  const [session, setSession] = React.useState<KortexSession>(
    () => manager.restoreSession() ?? manager.createSession(),
  );

  // Applies the restored/created theme to uiStore before first paint,
  // avoiding a flash of the store's own "light" default. useLayoutEffect
  // (not useEffect) matters here: React flushes every layout effect in
  // the tree, parent and child alike, before any passive effect runs —
  // so by the time SessionSync's passive effects read the theme below,
  // it's already correct. Runs once; ongoing theme changes flow the
  // other way (uiStore -> session) via SessionSync.
  React.useLayoutEffect(() => {
    useUiStore.getState().setTheme(session.theme);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally runs once on mount only.
  }, []);

  const updateSession = React.useCallback(
    (update: SessionUpdate) => {
      setSession(manager.updateSession(update));
    },
    [manager],
  );

  const clear = React.useCallback(() => {
    manager.clearSession();
    setSession(manager.createSession());
  }, [manager]);

  const value = React.useMemo<SessionContextValue>(
    () => ({ session, updateSession, clearSession: clear }),
    [session, updateSession, clear],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/**
 * Mounted once, inside WorkspaceProvider + PanelProvider (routes/
 * index.tsx) — the same placement navigation/navigationBridge.tsx uses
 * for WorkspaceNavigationSync, since it needs useWorkspace() context that
 * doesn't exist at SessionProvider's own, higher position in the tree.
 * Two jobs:
 *
 * 1. One-time restore: if the app launched at the workspace root and the
 *    restored session names an application, navigate to it. Only ever
 *    runs its restore branch once (`restoredRef`), and only when nothing
 *    has already claimed the URL — WorkspaceNavigationSync's own "URL
 *    drives workspace state, never the reverse" invariant still holds;
 *    this is just the one-time seed for what the URL starts as.
 * 2. Ongoing sync: mirrors workspace/theme state into the session as it
 *    changes, so the next restore has something to read. Panel state is
 *    deliberately excluded — PanelProvider persists and restores itself
 *    independently via panels/panelPersistence.ts, and an earlier version
 *    of this effect that also mirrored panel state into the session was
 *    removed (ADR-0003): it was a second, independently-written copy of
 *    the same data with no ordering guarantee against the first.
 */
export function SessionSync(): null {
  const { session, updateSession } = useSession();
  const { activeApplicationId, applications } = useWorkspace();
  const theme = useUiStore((state) => state.theme);
  const navigate = useNavigate();
  const location = useLocation();

  const restoredRef = React.useRef(false);
  // Captured once, from the session the provider above already
  // restored/created on its first render — not read reactively off
  // `session` inside the effect below, so that effect never needs
  // `session` itself as a dependency (and never risks re-firing its
  // restore branch when `updateSession` changes the very value it just
  // wrote).
  const persistedActiveApplicationRef = React.useRef(session.activeApplication);

  React.useEffect(() => {
    if (!restoredRef.current) {
      restoredRef.current = true;
      const persistedApplicationId = persistedActiveApplicationRef.current;
      if (location.pathname === WORKSPACE_ROOT_ROUTE && persistedApplicationId) {
        const route = resolveRouteForApplicationId(applications, persistedApplicationId);
        if (route) {
          navigate(route, { replace: true });
          // Skip the sync below on this run: activeApplicationId is still
          // `null` here — WorkspaceProvider hasn't caught up to the
          // navigation yet — and writing it now would overwrite the value
          // just restored before WorkspaceNavigationSync confirms it for
          // real on a later run.
          return;
        }
      }
    }
    updateSession({ activeApplication: activeApplicationId });
  }, [activeApplicationId, applications, location.pathname, navigate, updateSession]);

  React.useEffect(() => {
    updateSession({ theme });
  }, [theme, updateSession]);

  return null;
}
