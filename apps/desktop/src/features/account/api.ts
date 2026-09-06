import { invokeCapability } from "@/ipc/client";
import type { OAuthProviderId } from "@/auth/oauthCapability";
import type { ChangePasswordInput, RegisterPrincipalInput, SetEmailInput } from "./types";

const CHANGE_PASSWORD_CAPABILITY = "kortex.security.auth.change_password";
const SET_EMAIL_CAPABILITY = "kortex.security.principal.set_email";
const REGISTER_PRINCIPAL_CAPABILITY = "kortex.security.principal.register";
const OAUTH_LINK_BEGIN_CAPABILITY = "kortex.security.oauth.link_begin";
const OAUTH_LINK_COMPLETE_CAPABILITY = "kortex.security.oauth.link_complete";
const OAUTH_UNLINK_CAPABILITY = "kortex.security.oauth.unlink";
const OAUTH_LIST_LINKS_CAPABILITY = "kortex.security.oauth.list_links";

/** Mirrors `ConnectorAccessDeniedError`'s exact precedent
 * (`features/connectors/api.ts`) — thrown for a `PERMISSION_DENIED` failure,
 * which covers both "no/invalid session" and "authenticated but not
 * authorized" (`backend/src/kortex/api/errors.py`). */
export class AccountAccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AccountAccessDeniedError";
  }
}

/** Any other `FAILURE` envelope — a generic, recoverable failure whose
 * message is safe to display verbatim (validation errors, conflicts). */
export class AccountRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AccountRequestError";
  }
}

function throwForFailure(envelope: Awaited<ReturnType<typeof invokeCapability>>, fallbackMessage: string): never {
  const failure = envelope.errors[0];
  const message = failure?.message ?? fallbackMessage;
  if (failure?.category === "PERMISSION_DENIED") {
    throw new AccountAccessDeniedError(message);
  }
  throw new AccountRequestError(message);
}

/** Calls `kortex.security.auth.change_password` (Phase A). Identity comes
 * exclusively from the caller's own authenticated session server-side. */
export async function changePassword(input: ChangePasswordInput): Promise<void> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: CHANGE_PASSWORD_CAPABILITY,
    parameters: { current_password: input.currentPassword, new_password: input.newPassword },
  });
  if (envelope.status !== "SUCCESS") {
    throwForFailure(envelope, "Failed to change your password.");
  }
}

/** Calls `kortex.security.principal.set_email` (Phase A). */
export async function setEmail(input: SetEmailInput): Promise<void> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: SET_EMAIL_CAPABILITY,
    parameters: { email: input.email },
  });
  if (envelope.status !== "SUCCESS") {
    throwForFailure(envelope, "Failed to update your email.");
  }
}

/** Calls `kortex.security.principal.register` (Phase A) — admin-only,
 * RBAC-gated server-side (`security:principal:write`). The new principal's
 * tenant comes exclusively from the caller's own authenticated session. */
export async function registerPrincipal(input: RegisterPrincipalInput): Promise<void> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: REGISTER_PRINCIPAL_CAPABILITY,
    parameters: {
      principal_id: input.principalId,
      password: input.password,
      roles: input.roles,
      email: input.email ?? null,
    },
  });
  if (envelope.status !== "SUCCESS") {
    throwForFailure(envelope, "Failed to create the user.");
  }
}

export interface OAuthLinkBeginResult {
  authorizationUrl: string;
  state: string;
}

/** Calls `kortex.security.oauth.link_begin` (Phase A) — self-service, from
 * Account settings. Identity comes exclusively from the caller's own
 * authenticated session. */
export async function beginOAuthLink(provider: OAuthProviderId): Promise<OAuthLinkBeginResult> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: OAUTH_LINK_BEGIN_CAPABILITY,
    parameters: { provider },
  });
  if (envelope.status !== "SUCCESS") {
    throwForFailure(envelope, `Failed to start linking ${provider}.`);
  }
  const result = envelope.payload?.result as Record<string, unknown> | undefined;
  const authorizationUrl = result?.authorization_url;
  const state = result?.state;
  if (typeof authorizationUrl !== "string" || typeof state !== "string") {
    throw new AccountRequestError("Unexpected response from the backend.");
  }
  return { authorizationUrl, state };
}

/** Calls `kortex.security.oauth.link_complete` (Phase A). */
export async function completeOAuthLink(provider: OAuthProviderId, code: string, state: string): Promise<void> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: OAUTH_LINK_COMPLETE_CAPABILITY,
    parameters: { provider, code, state },
  });
  if (envelope.status !== "SUCCESS") {
    throwForFailure(envelope, `Failed to link ${provider}.`);
  }
}

/** Calls `kortex.security.oauth.unlink` (Phase A). */
export async function unlinkOAuth(provider: OAuthProviderId): Promise<void> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: OAUTH_UNLINK_CAPABILITY,
    parameters: { provider },
  });
  if (envelope.status !== "SUCCESS") {
    throwForFailure(envelope, `Failed to remove ${provider}.`);
  }
}

/** Calls `kortex.security.oauth.list_links` (Phase A) — the caller's own
 * linked provider names. */
export async function listOAuthLinks(): Promise<OAuthProviderId[]> {
  const envelope = await invokeCapability({
    requestId: crypto.randomUUID(),
    capabilityName: OAUTH_LIST_LINKS_CAPABILITY,
    parameters: {},
  });
  if (envelope.status !== "SUCCESS") {
    throwForFailure(envelope, "Failed to load your linked sign-in providers.");
  }
  const result = envelope.payload?.result as Record<string, unknown> | undefined;
  const providers = result?.providers;
  return Array.isArray(providers) ? providers.filter((p): p is OAuthProviderId => typeof p === "string") : [];
}
