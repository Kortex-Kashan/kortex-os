/**
 * M4.1 authentication state machine shape.
 *
 * CHECKING is the only state the app starts in — it must never render the
 * authenticated shell or the login screen until this resolves, and it must
 * resolve to exactly one of AUTHENTICATED/UNAUTHENTICATED/
 * BACKEND_UNAVAILABLE (see `AuthProvider.tsx`'s startup effect):
 *
 *   CHECKING -> AUTHENTICATED        (a stored token validated successfully)
 *   CHECKING -> UNAUTHENTICATED      (no stored token)
 *   CHECKING -> BACKEND_UNAVAILABLE  (a stored token exists but couldn't be
 *                                     validated because the backend is
 *                                     unreachable — never silently treated
 *                                     as either signed-in or signed-out)
 *
 * From the login screen (UNAUTHENTICATED / AUTHENTICATION_ERROR /
 * BACKEND_UNAVAILABLE), submitting credentials moves to AUTHENTICATING,
 * which resolves to AUTHENTICATED or AUTHENTICATION_ERROR. AUTHENTICATED
 * moves back to UNAUTHENTICATED on logout or on a 401 from any
 * authenticated call (see `classifyIpcFailure`) — never on a 403, which
 * stays AUTHENTICATED (see `authCapability.ts`).
 */
export type AuthStatus =
  | "CHECKING"
  | "UNAUTHENTICATED"
  | "AUTHENTICATING"
  | "AUTHENTICATED"
  | "AUTHENTICATION_ERROR"
  | "BACKEND_UNAVAILABLE";

/**
 * The minimum identity data the frontend ever holds — exactly what the
 * `kortex.security.auth.authenticate` capability's own success payload
 * already contains (`backend/src/kortex/engines/security/models.py`'s
 * `SecurityPrincipal`, dumped by `main.py::_invoke`). Never the token: the
 * token itself never leaves Rust custody (see `ipc.rs`'s `RawBackendResponse`
 * split).
 */
export interface AuthIdentity {
  principalId: string;
  principalType: string;
  tenantId: string;
  roles: string[];
}

export interface LoginCredentials {
  tenantId: string;
  principalId: string;
  password: string;
}

export type AuthState =
  | { status: "CHECKING" }
  | { status: "UNAUTHENTICATED" }
  | { status: "AUTHENTICATING" }
  | { status: "AUTHENTICATED"; identity: AuthIdentity | null }
  | { status: "AUTHENTICATION_ERROR"; message: string }
  | { status: "BACKEND_UNAVAILABLE" };
