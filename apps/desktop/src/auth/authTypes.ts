/**
 * Authentication + local-runtime-startup state machine shape.
 *
 * CHECKING is the only state the app starts in — it must never render the
 * authenticated shell, the startup screen, the bootstrap screen, or the
 * login screen until the startup effect (`AuthProvider.tsx`) begins
 * resolving it:
 *
 *   CHECKING -> STARTING             (waiting for the backend to become
 *                                     reachable — M7.1; bounded retries,
 *                                     see `backendReadiness.ts`)
 *   STARTING -> BACKEND_UNAVAILABLE  (retries exhausted — a terminal state
 *                                     until the user asks to retry via
 *                                     `retryConnection()`)
 *   STARTING -> BOOTSTRAP_REQUIRED   (backend reachable, but the system has
 *                                     no tenant/administrator yet — M7.1
 *                                     first-run setup)
 *   STARTING -> AUTHENTICATED        (backend reachable, already
 *                                     bootstrapped, a stored token
 *                                     validated successfully)
 *   STARTING -> UNAUTHENTICATED      (backend reachable, already
 *                                     bootstrapped, no stored token)
 *
 * From BOOTSTRAP_REQUIRED, submitting the setup form moves to
 * BOOTSTRAPPING, which resolves to AUTHENTICATED (bootstrap succeeded, then
 * immediately signed in with the same credentials — see
 * `AuthProvider.tsx::bootstrap`) or BOOTSTRAP_ERROR.
 *
 * From the login screen (UNAUTHENTICATED / AUTHENTICATION_ERROR),
 * submitting credentials moves to AUTHENTICATING, which resolves to
 * AUTHENTICATED or AUTHENTICATION_ERROR. AUTHENTICATED moves back to
 * UNAUTHENTICATED on logout or on a 401 from any authenticated call (see
 * `classifyIpcFailure`) — never on a 403, which stays AUTHENTICATED (see
 * `authCapability.ts`).
 */
export type AuthStatus =
  | "CHECKING"
  | "STARTING"
  | "BOOTSTRAP_REQUIRED"
  | "BOOTSTRAPPING"
  | "BOOTSTRAP_ERROR"
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

/**
 * M7.1 first-run setup form input. Deliberately the same three fields as
 * `LoginCredentials` — the administrator created here is the same identity
 * the user then signs in with, using this exact tenant ID/username/
 * password (see `AuthProvider.tsx::bootstrap`, which calls `login()` with
 * these same values immediately after a successful bootstrap).
 */
export interface BootstrapCredentials {
  tenantId: string;
  principalId: string;
  password: string;
}

export type AuthState =
  | { status: "CHECKING" }
  | { status: "STARTING"; attempt: number; maxAttempts: number }
  | { status: "BOOTSTRAP_REQUIRED" }
  | { status: "BOOTSTRAPPING" }
  | { status: "BOOTSTRAP_ERROR"; message: string }
  | { status: "UNAUTHENTICATED" }
  | { status: "AUTHENTICATING" }
  | { status: "AUTHENTICATED"; identity: AuthIdentity | null }
  | { status: "AUTHENTICATION_ERROR"; message: string }
  | { status: "BACKEND_UNAVAILABLE" };
