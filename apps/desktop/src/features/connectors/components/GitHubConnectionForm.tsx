/**
 * GitHubConnectionForm (Integration Hub M2)
 *
 * "Connect with GitHub" — an OAuth Authorization Code flow, not a manual
 * credential field: the user names the connection, clicks Connect, the
 * system browser opens GitHub's consent screen (`beginGitHubOAuth`), and the
 * deep-link callback (`useConnectorOAuthDeepLink`, a distinct scheme from
 * the SSO sign-in flow's own deep link) completes it
 * (`completeGitHubOAuth`) before the profile is registered
 * (`useRegisterIntegrationConnectorProfile`) with the backend-issued opaque
 * secret handle — never a client-derived one, and never a separate
 * `kortex.security.secret.put` call (the credential is already written by
 * `.complete`).
 */

import { useState } from "react";
import type { FormEvent } from "react";
import { Button, DialogFooter, Input, Label, Spinner } from "@kortex/design-system";
import { open as openUrl } from "@tauri-apps/plugin-shell";
import { useConnectorOAuthDeepLink } from "@/auth/useConnectorOAuthDeepLink";
import { beginGitHubOAuth, completeGitHubOAuth } from "@/auth/githubConnectorOAuth";
import { useRegisterIntegrationConnectorProfile } from "../hooks/useConnectorProfiles";

const GITHUB_API_BASE_URL = "https://api.github.com";
const GITHUB_DRIVER_ID = "connector-http-rest";
const GITHUB_PROVIDER = "github";

export interface GitHubConnectionFormProps {
  onSuccess: () => void;
  onCancel: () => void;
}

export function GitHubConnectionForm({ onSuccess, onCancel }: GitHubConnectionFormProps) {
  const register = useRegisterIntegrationConnectorProfile();
  const [profileId, setProfileId] = useState("");
  const [name, setName] = useState("");
  const [status, setStatus] = useState<"idle" | "authorizing" | "waiting" | "connecting">("idle");
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingState, setPendingState] = useState<string | null>(null);

  useConnectorOAuthDeepLink(({ code, state }) => {
    if (!pendingState || state !== pendingState) {
      return;
    }
    setPendingState(null);
    setStatus("connecting");
    void completeGitHubOAuth(profileId.trim(), code, state)
      .then((outcome) => {
        if (!outcome.ok) {
          setStatus("idle");
          setFormError(outcome.message);
          return;
        }
        return register
          .mutateAsync({
            profileId: profileId.trim(),
            name: name.trim(),
            driverId: GITHUB_DRIVER_ID,
            secretHandle: outcome.secretHandle,
            integrationProvider: GITHUB_PROVIDER,
            options: { base_url: GITHUB_API_BASE_URL },
          })
          .then(() => onSuccess());
      })
      .catch((err: unknown) => {
        setStatus("idle");
        setFormError(err instanceof Error ? err.message : "Failed to complete connecting GitHub.");
      });
  });

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);

    if (!profileId.trim() || !name.trim()) {
      setFormError("Connection ID and name are required.");
      return;
    }

    setStatus("authorizing");
    const begin = await beginGitHubOAuth(profileId.trim());
    if (!begin.ok) {
      setStatus("idle");
      setFormError(begin.message);
      return;
    }

    setPendingState(begin.state);
    setStatus("waiting");
    await openUrl(begin.authorizationUrl);
  }

  const isBusy = status !== "idle";

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4" data-testid="github-connection-form">
      <div className="space-y-1.5">
        <Label htmlFor="github-profile-id">Connection ID</Label>
        <Input
          id="github-profile-id"
          placeholder="e.g. github-main"
          value={profileId}
          onChange={(e) => setProfileId(e.target.value)}
          disabled={isBusy}
          required
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="github-name">Display Name</Label>
        <Input
          id="github-name"
          placeholder="e.g. GitHub"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={isBusy}
          required
        />
      </div>

      {formError && (
        <p className="text-caption text-destructive" role="alert">
          {formError}
        </p>
      )}

      <DialogFooter>
        <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={isBusy}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={isBusy} aria-busy={isBusy} data-testid="connect-github-btn">
          {isBusy ? (
            <>
              <Spinner size={14} />
              {status === "waiting" ? "Waiting for GitHub…" : "Connecting…"}
            </>
          ) : (
            "Connect with GitHub"
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
