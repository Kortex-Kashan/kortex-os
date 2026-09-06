// Phase A: Google/Microsoft OAuth sign-in for an existing account. OAuth is
// a second sign-in method, never a self-registration path — a callback that
// resolves no linked account fails honestly (`NO_LINKED_ACCOUNT`), it never
// silently creates one.

import { invokeCapability, type IpcResultEnvelope } from "@/ipc/client";
import type { AuthIdentity } from "./authTypes";

const OAUTH_GET_CONFIG_CAPABILITY = "kortex.security.oauth.get_config";
const OAUTH_LOGIN_BEGIN_CAPABILITY = "kortex.security.oauth.login_begin";
const OAUTH_LOGIN_COMPLETE_CAPABILITY = "kortex.security.oauth.login_complete";

export type OAuthProviderId = "google" | "microsoft";

export interface OAuthConfig {
  google: boolean;
  microsoft: boolean;
}

/** Calls `kortex.security.oauth.get_config`. Never reveals secret material —
 * only whether each provider is configured, used to show a real
 * "not configured" state on the login screen rather than a button that
 * silently fails. Fails safe (both `false`) on any transport error, since a
 * disabled button is the correct fallback when we can't even ask. */
export async function getOAuthConfig(): Promise<OAuthConfig> {
  try {
    const envelope = await invokeCapability({
      requestId: crypto.randomUUID(),
      capabilityName: OAUTH_GET_CONFIG_CAPABILITY,
      parameters: {},
    });
    if (envelope.status === "SUCCESS") {
      const result = envelope.payload?.result as Record<string, unknown> | undefined;
      return { google: result?.google === true, microsoft: result?.microsoft === true };
    }
  } catch {
    // fall through to the safe default below
  }
  return { google: false, microsoft: false };
}

export type OAuthBeginOutcome =
  | { ok: true; authorizationUrl: string; state: string }
  | { ok: false; message: string };

/** Calls `kortex.security.oauth.login_begin`. */
export async function beginOAuthLogin(provider: OAuthProviderId): Promise<OAuthBeginOutcome> {
  let envelope: IpcResultEnvelope;
  try {
    envelope = await invokeCapability({
      requestId: crypto.randomUUID(),
      capabilityName: OAUTH_LOGIN_BEGIN_CAPABILITY,
      parameters: { provider },
    });
  } catch {
    return { ok: false, message: "The backend is unreachable." };
  }

  if (envelope.status === "SUCCESS") {
    const result = envelope.payload?.result as Record<string, unknown> | undefined;
    const authorizationUrl = result?.authorization_url;
    const state = result?.state;
    if (typeof authorizationUrl === "string" && typeof state === "string") {
      return { ok: true, authorizationUrl, state };
    }
    return { ok: false, message: "Unexpected response from the backend." };
  }
  return { ok: false, message: envelope.errors[0]?.message ?? `Failed to start ${provider} sign-in.` };
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

export type OAuthLoginCompleteOutcome =
  | { ok: true; identity: AuthIdentity }
  | { ok: false; kind: "NO_LINKED_ACCOUNT"; message: string }
  | { ok: false; kind: "INVALID_STATE"; message: string }
  | { ok: false; kind: "BACKEND_UNAVAILABLE"; message: string };

/**
 * Calls `kortex.security.oauth.login_complete`. On success, the backend has
 * already minted a real session token via the same mechanism `login()` uses
 * (`kortex.api.main._invoke`'s bootstrap-exempt-capability-returning-a-
 * SecurityPrincipal rule) — Rust's IPC layer stores it exactly like any
 * other successful authenticated response; nothing further is needed here
 * beyond returning the identity for `AuthProvider` to adopt.
 */
export async function completeOAuthLogin(
  provider: OAuthProviderId,
  code: string,
  state: string,
): Promise<OAuthLoginCompleteOutcome> {
  let envelope: IpcResultEnvelope;
  try {
    envelope = await invokeCapability({
      requestId: crypto.randomUUID(),
      capabilityName: OAUTH_LOGIN_COMPLETE_CAPABILITY,
      parameters: { provider, code, state },
    });
  } catch {
    return { ok: false, kind: "BACKEND_UNAVAILABLE", message: "The backend is unreachable." };
  }

  if (envelope.status === "SUCCESS") {
    const identity = parseIdentity(envelope);
    if (identity) {
      return { ok: true, identity };
    }
    return { ok: false, kind: "BACKEND_UNAVAILABLE", message: "Unexpected response from the backend." };
  }

  const category = envelope.errors[0]?.category;
  if (category === "SERVICE_UNAVAILABLE" || category === "TIMEOUT_EXCEEDED") {
    return { ok: false, kind: "BACKEND_UNAVAILABLE", message: "The backend is unreachable." };
  }
  const message = envelope.errors[0]?.message ?? `Failed to complete ${provider} sign-in.`;
  // `OAuthNoLinkedAccountError`'s message is distinctive by construction
  // ("No KORTEX account is linked to this ... identity." —
  // `SecurityEngine.oauth_login_complete_capability`) — matched by
  // substring since the IPC error contract carries no structured `details`
  // to branch on instead (`kortex.api.errors.error_details` always returns
  // `None` today).
  if (message.includes("No KORTEX account is linked to")) {
    return { ok: false, kind: "NO_LINKED_ACCOUNT", message };
  }
  return { ok: false, kind: "INVALID_STATE", message };
}
