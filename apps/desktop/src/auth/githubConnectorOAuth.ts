// Integration Hub M2: connecting the GitHub API integration to a
// ConnectorProfile — distinct from `oauthCapability.ts`'s Google/Microsoft
// *sign-in* flow. This never mints or touches a KORTEX session; it only
// authorizes the backend to call the GitHub REST API on this tenant's
// behalf for one specific connector profile.

import { invokeCapability, type IpcResultEnvelope } from "@/ipc/client";

const INTEGRATION_OAUTH_BEGIN_CAPABILITY = "kortex.security.integration_oauth.begin";
const INTEGRATION_OAUTH_COMPLETE_CAPABILITY = "kortex.security.integration_oauth.complete";
const INTEGRATION_OAUTH_STATUS_CAPABILITY = "kortex.security.integration_oauth.status";

const GITHUB_PROVIDER = "github";

// Distinct from the SSO flow's `kortex-auth://oauth-callback` — a separate
// scheme so an in-flight sign-in and an in-flight "Connect GitHub" never
// share a deep-link listener (see `useConnectorOAuthDeepLink.ts`).
const GITHUB_REDIRECT_URI = "kortex-connector-auth://oauth-callback";

export type IntegrationBeginOutcome =
  | { ok: true; authorizationUrl: string; state: string }
  | { ok: false; message: string };

/** Calls `kortex.security.integration_oauth.begin` for GitHub. */
export async function beginGitHubOAuth(profileId: string): Promise<IntegrationBeginOutcome> {
  let envelope: IpcResultEnvelope;
  try {
    envelope = await invokeCapability({
      requestId: crypto.randomUUID(),
      capabilityName: INTEGRATION_OAUTH_BEGIN_CAPABILITY,
      parameters: { provider: GITHUB_PROVIDER, profile_id: profileId, redirect_uri: GITHUB_REDIRECT_URI },
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
  return { ok: false, message: envelope.errors[0]?.message ?? "Failed to start connecting GitHub." };
}

export type IntegrationCompleteOutcome = { ok: true; secretHandle: string } | { ok: false; message: string };

/** Calls `kortex.security.integration_oauth.complete` for GitHub. Returns
 * the opaque `secretHandle` the caller must pass, unmodified, as
 * `ConnectorProfile.secret_handle` when it registers the profile —
 * generated server-side, never derived from `profileId` (Integration Hub
 * M2's credential-identity boundary is `(tenant_id, profile_id)`, not the
 * handle string itself; see `IntegrationOAuthManager`). */
export async function completeGitHubOAuth(
  profileId: string,
  code: string,
  state: string,
): Promise<IntegrationCompleteOutcome> {
  let envelope: IpcResultEnvelope;
  try {
    envelope = await invokeCapability({
      requestId: crypto.randomUUID(),
      capabilityName: INTEGRATION_OAUTH_COMPLETE_CAPABILITY,
      parameters: {
        provider: GITHUB_PROVIDER,
        profile_id: profileId,
        code,
        state,
        redirect_uri: GITHUB_REDIRECT_URI,
      },
    });
  } catch {
    return { ok: false, message: "The backend is unreachable." };
  }

  if (envelope.status === "SUCCESS") {
    const result = envelope.payload?.result as Record<string, unknown> | undefined;
    const secretHandle = result?.secret_handle;
    if (typeof secretHandle === "string" && secretHandle) {
      return { ok: true, secretHandle };
    }
    return { ok: false, message: "Unexpected response from the backend." };
  }
  return { ok: false, message: envelope.errors[0]?.message ?? "Failed to complete connecting GitHub." };
}

export type IntegrationStatus = { connected: boolean; status: string; scopes: string | null; connectedAt: string };

/** Calls `kortex.security.integration_oauth.status` for GitHub. Returns
 * `null` when nothing is connected for this profile yet (a normal state,
 * never an error the caller needs to branch on further). */
export async function getGitHubOAuthStatus(profileId: string): Promise<IntegrationStatus | null> {
  try {
    const envelope = await invokeCapability({
      requestId: crypto.randomUUID(),
      capabilityName: INTEGRATION_OAUTH_STATUS_CAPABILITY,
      parameters: { provider: GITHUB_PROVIDER, profile_id: profileId },
    });
    if (envelope.status !== "SUCCESS") {
      return null;
    }
    const result = envelope.payload?.result as Record<string, unknown> | undefined;
    if (!result || typeof result.connected !== "boolean" || typeof result.status !== "string") {
      return null;
    }
    return {
      connected: result.connected,
      status: result.status,
      scopes: typeof result.scopes === "string" ? result.scopes : null,
      connectedAt: typeof result.connected_at === "string" ? result.connected_at : "",
    };
  } catch {
    return null;
  }
}
