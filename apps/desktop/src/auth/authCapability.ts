// M4.1 authentication capability calls. Both capabilities used here are
// the real, already-implemented Security Engine capabilities audited for
// this milestone (`backend/src/kortex/engines/security/engine.py`'s
// `_CANONICAL_CAPABILITIES`) — this module introduces no new backend
// capability or endpoint, per the milestone's "integrate, don't invent"
// constraint.

import { invokeCapability, type IpcResultEnvelope } from "@/ipc/client";
import type { AuthIdentity, LoginCredentials } from "./authTypes";

const AUTHENTICATE_CAPABILITY = "kortex.security.auth.authenticate";

/**
 * `kortex.security.signature.verify` (Security Engine, M6) — used here
 * purely as a session-validation ping on startup. It is the only one of
 * the four canonical Security Engine capabilities that is authenticated,
 * side-effect-free, and requires no caller-supplied identity/authorization
 * context of its own (`kortex.security.access.authorize` would require the
 * caller to already supply a `SecurityPrincipal`, which defeats the point
 * of asking "is my session still valid"). The placeholder arguments below
 * are deliberately inert: verification of a single dummy byte against a
 * dummy key can only ever return `false`, and that boolean is never read —
 * only whether the *call itself* was authenticated (200/403) or not (401)
 * matters. This is the "existing authenticated IPC path" Phase 6 of the
 * M4.1 brief calls for, not a new capability.
 */
const SESSION_CHECK_CAPABILITY = "kortex.security.signature.verify";

function newRequestId(): string {
  return crypto.randomUUID();
}

function parseIdentity(envelope: IpcResultEnvelope): AuthIdentity | null {
  const result = envelope.payload?.result;
  if (!result || typeof result !== "object") {
    return null;
  }
  const candidate = result as Record<string, unknown>;
  if (
    typeof candidate.principal_id !== "string" ||
    typeof candidate.principal_type !== "string" ||
    typeof candidate.tenant_id !== "string"
  ) {
    return null;
  }
  return {
    principalId: candidate.principal_id,
    principalType: candidate.principal_type,
    tenantId: candidate.tenant_id,
    roles: Array.isArray(candidate.roles) ? candidate.roles.filter((r): r is string => typeof r === "string") : [],
  };
}

export type LoginOutcome =
  | { ok: true; identity: AuthIdentity }
  | { ok: false; kind: "INVALID_CREDENTIALS"; message: string }
  | { ok: false; kind: "BACKEND_UNAVAILABLE"; message: string };

/**
 * Calls the real `kortex.security.auth.authenticate` capability. Never
 * throws — a rejected `invokeCapability` promise (the Tauri command itself
 * failing to dispatch, not a business-level authentication failure, which
 * always arrives as a `FAILURE` envelope instead) is caught and reported
 * as BACKEND_UNAVAILABLE rather than propagating an unhandled rejection
 * into the caller's UI state.
 */
export async function login(credentials: LoginCredentials): Promise<LoginOutcome> {
  let envelope: IpcResultEnvelope;
  try {
    envelope = await invokeCapability({
      requestId: newRequestId(),
      capabilityName: AUTHENTICATE_CAPABILITY,
      parameters: {
        credentials: {
          principal_type: "USER",
          tenant_id: credentials.tenantId,
          principal_id: credentials.principalId,
          password: credentials.password,
        },
      },
    });
  } catch {
    return { ok: false, kind: "BACKEND_UNAVAILABLE", message: "The backend is unreachable." };
  }

  if (envelope.status === "SUCCESS") {
    const identity = parseIdentity(envelope);
    if (identity) {
      return { ok: true, identity };
    }
    // A SUCCESS envelope with an unparseable payload is itself a backend
    // contract violation, not a credential problem — never claim a
    // successful sign-in with no identity to show for it.
    return { ok: false, kind: "BACKEND_UNAVAILABLE", message: "Unexpected response from the backend." };
  }

  const category = envelope.errors[0]?.category;
  if (category === "SERVICE_UNAVAILABLE" || category === "TIMEOUT_EXCEEDED") {
    return { ok: false, kind: "BACKEND_UNAVAILABLE", message: "The backend is unreachable." };
  }
  // Every credential-related failure (`AuthenticationError`, wrong
  // password, unknown principal, disabled principal) arrives as the
  // identical generic `PERMISSION_DENIED` category/message by design
  // (`AuthenticationManager`'s own enumeration-resistance guarantee) — this
  // is surfaced verbatim, never re-derived or guessed at.
  return {
    ok: false,
    kind: "INVALID_CREDENTIALS",
    message: envelope.errors[0]?.message ?? "Authentication failed.",
  };
}

export type SessionCheckResult = "VALID" | "INVALID" | "BACKEND_UNAVAILABLE";

/**
 * Validates a token already held in Rust's keyring by making one real,
 * inert, authenticated capability call. Interprets the *real* HTTP status
 * (see `classifyIpcFailure`) rather than the collapsed `PERMISSION_DENIED`
 * category alone — a 401 means the token itself is invalid/expired
 * (INVALID); a 403 means the token is valid but this particular
 * unprivileged principal isn't authorized for `security:read` (still
 * VALID — the session itself is genuine). Anything else unresolvable
 * (unreachable backend, timeout, an unrelated 5xx) is reported honestly as
 * BACKEND_UNAVAILABLE rather than guessed at in either direction.
 */
export async function checkStoredSession(): Promise<SessionCheckResult> {
  let envelope: IpcResultEnvelope;
  try {
    envelope = await invokeCapability({
      requestId: newRequestId(),
      capabilityName: SESSION_CHECK_CAPABILITY,
      parameters: {
        data: "kortex-desktop-session-check",
        signature: "00",
        public_key: "00",
      },
    });
  } catch {
    return "BACKEND_UNAVAILABLE";
  }

  if (envelope.status === "SUCCESS") {
    return "VALID";
  }
  if (envelope.httpStatus === 401) {
    return "INVALID";
  }
  if (envelope.httpStatus === 403) {
    return "VALID";
  }
  return "BACKEND_UNAVAILABLE";
}

export type IpcFailureKind = "UNAUTHORIZED" | "FORBIDDEN" | "OTHER";

/**
 * Phase 7's 401-vs-403 rule, as a pure, independently-testable function:
 * 401 means the session itself is no longer valid (caller should force
 * re-authentication); 403 means the caller remains authenticated but this
 * particular request is forbidden (caller must NOT log the user out).
 * Never fabricates a distinction the transport doesn't actually carry —
 * anything other than a real 401/403 on a PERMISSION_DENIED failure
 * resolves to OTHER, deliberately inert.
 */
export function classifyIpcFailure(envelope: IpcResultEnvelope): IpcFailureKind {
  if (envelope.status !== "FAILURE" || envelope.errors[0]?.category !== "PERMISSION_DENIED") {
    return "OTHER";
  }
  if (envelope.httpStatus === 401) {
    return "UNAUTHORIZED";
  }
  if (envelope.httpStatus === 403) {
    return "FORBIDDEN";
  }
  return "OTHER";
}
