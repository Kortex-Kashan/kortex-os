import * as React from "react";

import type { IpcResultEnvelope } from "@/ipc/client";
import { clearStoredSession, hasStoredSession } from "@/ipc/session";

import { checkStoredSession, classifyIpcFailure, login as loginCapability } from "./authCapability";
import type { AuthState, LoginCredentials } from "./authTypes";
import { clearCachedIdentity, loadCachedIdentity, saveCachedIdentity } from "./identityCache";

interface AuthContextValue {
  state: AuthState;
  login: (credentials: LoginCredentials) => Promise<void>;
  logout: () => Promise<void>;
  /**
   * Applies the 401-vs-403 rule (Phase 7 of the M4.1 brief) for any future
   * authenticated-capability call site: a 401 means the session itself is
   * no longer valid, so it is cleared and the app is forced back to
   * UNAUTHENTICATED; a 403 means the caller is still genuinely
   * authenticated but forbidden from this one request, so it is a no-op
   * here — the caller displays its own permission-denied UI without ever
   * being signed out. No feature currently calls this (M4.1 adds no
   * feature capability calls beyond auth itself), but the rule must exist
   * and be tested now rather than invented ad hoc by the first feature
   * that needs it.
   */
  reportIpcResult: (envelope: IpcResultEnvelope) => void;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const context = React.useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

export interface AuthProviderProps {
  children: React.ReactNode;
}

/**
 * Owns the M4.1 authentication state machine (`authTypes.ts`'s `AuthState`).
 * Plain React Context + `useState`, mirroring `SessionProvider`'s own
 * precedent (a dedicated context per concern rather than folding this into
 * an existing store). Rust remains the sole custodian of the session token
 * itself for its entire lifecycle — this provider only ever holds UI-facing
 * state (`AuthState`) and non-secret display identity, never the token.
 */
export function AuthProvider({ children }: AuthProviderProps) {
  const [state, setState] = React.useState<AuthState>({ status: "CHECKING" });
  // A synchronous guard against duplicate submissions — belt-and-suspenders
  // alongside the LoginScreen disabling its own submit button, since a
  // `setState` call does not synchronously block a second `login()`
  // invocation that arrives before React re-renders.
  const loginInFlightRef = React.useRef(false);

  React.useEffect(() => {
    let cancelled = false;

    async function resolveInitialSession() {
      let has: boolean;
      try {
        has = await hasStoredSession();
      } catch {
        // The Rust IPC bridge itself failed to answer a purely local
        // question (no network involved) — a different, lower-level
        // failure than "backend unreachable". There is no stored-session
        // evidence to act on either way, so this fails closed to
        // UNAUTHENTICATED (never to AUTHENTICATED) rather than getting
        // stuck in CHECKING forever.
        if (!cancelled) {
          setState({ status: "UNAUTHENTICATED" });
        }
        return;
      }
      if (cancelled) {
        return;
      }
      if (!has) {
        setState({ status: "UNAUTHENTICATED" });
        return;
      }

      const result = await checkStoredSession();
      if (cancelled) {
        return;
      }
      if (result === "VALID") {
        setState({ status: "AUTHENTICATED", identity: loadCachedIdentity() });
      } else if (result === "INVALID") {
        await clearStoredSession();
        clearCachedIdentity();
        if (!cancelled) {
          setState({ status: "UNAUTHENTICATED" });
        }
      } else {
        setState({ status: "BACKEND_UNAVAILABLE" });
      }
    }

    void resolveInitialSession();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = React.useCallback(async (credentials: LoginCredentials) => {
    if (loginInFlightRef.current) {
      return;
    }
    loginInFlightRef.current = true;
    setState({ status: "AUTHENTICATING" });
    try {
      // `loginCapability` (authCapability.ts) already normalizes every
      // failure mode — including a rejected `invokeCapability` promise —
      // into a resolved `LoginOutcome`, so this call should never reject.
      // The try/catch is a defensive backstop only: if it ever does
      // reject regardless, AUTHENTICATING must not become a stuck,
      // unrecoverable terminal state with no way back to the login form.
      const outcome = await loginCapability(credentials);
      if (outcome.ok) {
        saveCachedIdentity(outcome.identity);
        setState({ status: "AUTHENTICATED", identity: outcome.identity });
      } else if (outcome.kind === "BACKEND_UNAVAILABLE") {
        setState({ status: "BACKEND_UNAVAILABLE" });
      } else {
        setState({ status: "AUTHENTICATION_ERROR", message: outcome.message });
      }
    } catch {
      setState({ status: "BACKEND_UNAVAILABLE" });
    } finally {
      loginInFlightRef.current = false;
    }
  }, []);

  const logout = React.useCallback(async () => {
    await clearStoredSession();
    clearCachedIdentity();
    setState({ status: "UNAUTHENTICATED" });
  }, []);

  const reportIpcResult = React.useCallback((envelope: IpcResultEnvelope) => {
    if (classifyIpcFailure(envelope) !== "UNAUTHORIZED") {
      // FORBIDDEN or OTHER — never a reason to sign the user out.
      return;
    }
    void clearStoredSession();
    clearCachedIdentity();
    setState({ status: "UNAUTHENTICATED" });
  }, []);

  const value = React.useMemo<AuthContextValue>(
    () => ({ state, login, logout, reportIpcResult }),
    [state, login, logout, reportIpcResult],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
