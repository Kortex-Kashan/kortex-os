import * as React from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Spinner,
} from "@kortex/design-system";
import { open as openUrl } from "@tauri-apps/plugin-shell";

import { useAuth } from "@/auth/AuthProvider";
import { getOAuthConfig, type OAuthConfig, type OAuthProviderId } from "@/auth/oauthCapability";
import { useOAuthDeepLink } from "@/auth/useOAuthDeepLink";

import {
  beginOAuthLink,
  changePassword,
  completeOAuthLink,
  listOAuthLinks,
  registerPrincipal,
  setEmail,
  unlinkOAuth,
} from "../api";

/**
 * Phase A: the Account/security settings screen. Identity comes from
 * `useAuth().state.identity` (the same non-secret display identity
 * `TopBar` already reads) — never re-fetched or cached separately.
 *
 * Self-service for every authenticated user: change password, set contact
 * email, sign out. The "Team members" panel is additionally shown only to
 * a principal with the `admin` role — a UI-only convenience, not a
 * security boundary; the real gate is server-side RBAC on
 * `kortex.security.principal.register` (`security:principal:write`).
 *
 * Deliberately does not include a "Users & Roles" list/management surface —
 * out of scope for this milestone (see the sidebar's still-disabled
 * placeholder for that broader administrative feature).
 */
export function AccountApp() {
  const auth = useAuth();
  const identity = auth.state.status === "AUTHENTICATED" ? auth.state.identity : null;
  const isAdmin = identity?.roles.includes("admin") ?? false;

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 p-6">
      <div>
        <h1 className="text-title text-foreground">Account</h1>
        <p className="text-body text-muted-foreground">Manage your identity, password, and sign-in.</p>
      </div>

      <IdentityCard identity={identity} onSignOut={() => void auth.logout()} />
      <ChangePasswordCard />
      <EmailCard />
      <LinkedProvidersCard />
      {isAdmin && <CreateUserCard />}
    </div>
  );
}

function IdentityCard({
  identity,
  onSignOut,
}: {
  identity: { principalId: string; tenantId: string; roles: string[] } | null;
  onSignOut: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Identity</CardTitle>
        <CardDescription>Your current session.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-caption text-muted-foreground">Username</span>
          <span className="text-body text-foreground">{identity?.principalId ?? "Unknown"}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-caption text-muted-foreground">Tenant</span>
          <span className="text-body text-foreground">{identity?.tenantId ?? "Unknown"}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-caption text-muted-foreground">Roles</span>
          <div className="flex flex-wrap gap-1.5">
            {(identity?.roles ?? []).length > 0 ? (
              identity!.roles.map((role) => (
                <Badge key={role} variant="secondary">
                  {role}
                </Badge>
              ))
            ) : (
              <span className="text-body text-muted-foreground">No roles</span>
            )}
          </div>
        </div>
        <Button type="button" variant="outline" onClick={onSignOut} className="self-start">
          Sign out
        </Button>
      </CardContent>
    </Card>
  );
}

function ChangePasswordCard() {
  const [currentPassword, setCurrentPassword] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [status, setStatus] = React.useState<"idle" | "submitting" | "success" | "error">("idle");
  const [message, setMessage] = React.useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === "submitting") {
      return;
    }
    setStatus("submitting");
    try {
      await changePassword({ currentPassword, newPassword });
      setStatus("success");
      setMessage("Your password has been changed.");
      setCurrentPassword("");
      setNewPassword("");
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "Failed to change your password.");
      setCurrentPassword("");
      setNewPassword("");
    }
  }

  const isSubmitting = status === "submitting";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Change password</CardTitle>
        <CardDescription>Choose a new password for your account.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={(event) => void handleSubmit(event)} noValidate>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="account-current-password">Current password</Label>
            <Input
              id="account-current-password"
              type="password"
              autoComplete="current-password"
              required
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              disabled={isSubmitting}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="account-new-password">New password</Label>
            <Input
              id="account-new-password"
              type="password"
              autoComplete="new-password"
              required
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              disabled={isSubmitting}
            />
          </div>
          {message && (
            <p role={status === "error" ? "alert" : "status"} className={status === "error" ? "text-body text-destructive" : "text-body text-foreground"}>
              {message}
            </p>
          )}
          <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting} className="self-start">
            {isSubmitting ? (
              <>
                <Spinner size={14} />
                Changing…
              </>
            ) : (
              "Change password"
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function EmailCard() {
  const [email, setEmailValue] = React.useState("");
  const [status, setStatus] = React.useState<"idle" | "submitting" | "success" | "error">("idle");
  const [message, setMessage] = React.useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === "submitting") {
      return;
    }
    setStatus("submitting");
    try {
      await setEmail({ email: email.trim() });
      setStatus("success");
      setMessage("Your email has been updated.");
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "Failed to update your email.");
    }
  }

  const isSubmitting = status === "submitting";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Contact email</CardTitle>
        <CardDescription>Used only for password-reset links.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={(event) => void handleSubmit(event)} noValidate>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="account-email">Email</Label>
            <Input
              id="account-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmailValue(event.target.value)}
              disabled={isSubmitting}
            />
          </div>
          {message && (
            <p role={status === "error" ? "alert" : "status"} className={status === "error" ? "text-body text-destructive" : "text-body text-foreground"}>
              {message}
            </p>
          )}
          <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting} className="self-start">
            {isSubmitting ? (
              <>
                <Spinner size={14} />
                Saving…
              </>
            ) : (
              "Save email"
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

const PROVIDER_LABELS: Record<OAuthProviderId, string> = {
  google: "Google",
  microsoft: "Microsoft",
};

/**
 * Phase A: shows each configured OAuth provider's linked/not-linked state
 * with a Connect/Unlink action. An unconfigured provider is omitted
 * entirely (nothing for a self-service Account page to offer about a
 * provider only an administrator can configure).
 */
function LinkedProvidersCard() {
  const [config, setConfig] = React.useState<OAuthConfig | null>(null);
  const [linked, setLinked] = React.useState<OAuthProviderId[]>([]);
  const [pendingProvider, setPendingProvider] = React.useState<OAuthProviderId | null>(null);
  const [message, setMessage] = React.useState<string | null>(null);
  const pendingStateRef = React.useRef<string | null>(null);

  const refresh = React.useCallback(() => {
    void listOAuthLinks().then(setLinked);
  }, []);

  React.useEffect(() => {
    void getOAuthConfig().then(setConfig);
    refresh();
  }, [refresh]);

  useOAuthDeepLink(({ code, state }) => {
    if (!pendingProvider || state !== pendingStateRef.current) {
      return;
    }
    const provider = pendingProvider;
    pendingStateRef.current = null;
    setPendingProvider(null);
    void completeOAuthLink(provider, code, state)
      .then(() => {
        setMessage(`${PROVIDER_LABELS[provider]} is now linked.`);
        refresh();
      })
      .catch((error: unknown) => {
        setMessage(error instanceof Error ? error.message : `Failed to link ${provider}.`);
      });
  });

  async function handleConnect(provider: OAuthProviderId) {
    setPendingProvider(provider);
    setMessage(null);
    try {
      const begin = await beginOAuthLink(provider);
      pendingStateRef.current = begin.state;
      await openUrl(begin.authorizationUrl);
    } catch (error) {
      setPendingProvider(null);
      setMessage(error instanceof Error ? error.message : `Failed to start linking ${provider}.`);
    }
  }

  async function handleUnlink(provider: OAuthProviderId) {
    try {
      await unlinkOAuth(provider);
      setMessage(`${PROVIDER_LABELS[provider]} was removed.`);
      refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Failed to remove ${provider}.`);
    }
  }

  const configuredProviders = (["google", "microsoft"] as const).filter((provider) => config?.[provider]);
  if (config !== null && configuredProviders.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Linked sign-in providers</CardTitle>
        <CardDescription>Use Google or Microsoft to sign in instead of your password.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {configuredProviders.map((provider) => {
          const isLinked = linked.includes(provider);
          const isBusy = pendingProvider === provider;
          return (
            <div key={provider} className="flex items-center justify-between gap-3">
              <span className="text-body text-foreground">{PROVIDER_LABELS[provider]}</span>
              {isLinked ? (
                <Button type="button" variant="outline" size="sm" onClick={() => void handleUnlink(provider)}>
                  Unlink
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={isBusy}
                  aria-busy={isBusy}
                  onClick={() => void handleConnect(provider)}
                >
                  {isBusy ? (
                    <>
                      <Spinner size={14} />
                      Waiting…
                    </>
                  ) : (
                    "Connect"
                  )}
                </Button>
              )}
            </div>
          );
        })}
        {message && <p className="text-body text-muted-foreground">{message}</p>}
      </CardContent>
    </Card>
  );
}

function CreateUserCard() {
  const [principalId, setPrincipalId] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [status, setStatus] = React.useState<"idle" | "submitting" | "success" | "error">("idle");
  const [message, setMessage] = React.useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === "submitting") {
      return;
    }
    setStatus("submitting");
    try {
      await registerPrincipal({ principalId: principalId.trim(), password, roles: ["member"] });
      setStatus("success");
      setMessage(`User "${principalId.trim()}" was created.`);
      setPrincipalId("");
      setPassword("");
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "Failed to create the user.");
      setPassword("");
    }
  }

  const isSubmitting = status === "submitting";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Team members</CardTitle>
        <CardDescription>Create a new user under your tenant.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={(event) => void handleSubmit(event)} noValidate>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="account-new-user-username">Username</Label>
            <Input
              id="account-new-user-username"
              type="text"
              required
              value={principalId}
              onChange={(event) => setPrincipalId(event.target.value)}
              disabled={isSubmitting}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="account-new-user-password">Temporary password</Label>
            <Input
              id="account-new-user-password"
              type="password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={isSubmitting}
            />
          </div>
          {message && (
            <p role={status === "error" ? "alert" : "status"} className={status === "error" ? "text-body text-destructive" : "text-body text-foreground"}>
              {message}
            </p>
          )}
          <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting} className="self-start">
            {isSubmitting ? (
              <>
                <Spinner size={14} />
                Creating…
              </>
            ) : (
              "Create user"
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
