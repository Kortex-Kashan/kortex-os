import * as React from "react";
import {
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

import { useAuth } from "./AuthProvider";
import { EyeIcon, EyeOffIcon } from "./icons";

const MIN_PASSWORD_LENGTH = 8;

/**
 * The KORTEX Desktop first-run setup screen (M7.1). `AuthGate` renders this
 * in place of `LoginScreen` whenever `AuthProvider`'s state is
 * `BOOTSTRAP_REQUIRED`/`BOOTSTRAPPING`/`BOOTSTRAP_ERROR` — i.e. the backend
 * is reachable but reports zero principals (`GET /health`'s
 * `bootstrap_required`, Milestone M7.1). This is the *only* place a
 * KORTEX install's first tenant/administrator can ever be created: once
 * this succeeds, the backend's `kortex.security.bootstrap.create_admin`
 * capability closes permanently (see `AuthenticationManager.
 * bootstrap_first_admin`) and this screen becomes unreachable for the
 * lifetime of that install.
 *
 * Deliberately the same three fields as `LoginScreen` (Tenant ID,
 * Username, Password) plus a client-side-only Confirm Password check —
 * the account created here is exactly the account the user immediately
 * signs in as (`AuthProvider.tsx::bootstrap`).
 */
export function BootstrapScreen() {
  const auth = useAuth();
  const [tenantId, setTenantId] = React.useState("");
  const [principalId, setPrincipalId] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [localError, setLocalError] = React.useState<string | null>(null);

  const tenantIdRef = React.useRef<HTMLInputElement>(null);
  const previousStatusRef = React.useRef(auth.state.status);

  React.useEffect(() => {
    tenantIdRef.current?.focus();
  }, []);

  // Same password-clearing discipline as LoginScreen: never the tenant ID
  // or username, only the password fields, the moment an attempt fails.
  React.useEffect(() => {
    const previous = previousStatusRef.current;
    previousStatusRef.current = auth.state.status;
    if (previous === "BOOTSTRAPPING" && auth.state.status !== "AUTHENTICATING" && auth.state.status !== "AUTHENTICATED") {
      setPassword("");
      setConfirmPassword("");
    }
  }, [auth.state.status]);

  React.useEffect(() => {
    return () => {
      setPassword("");
      setConfirmPassword("");
    };
  }, []);

  const isSubmitting = auth.state.status === "BOOTSTRAPPING" || auth.state.status === "AUTHENTICATING";

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    setLocalError(null);

    const trimmedTenantId = tenantId.trim();
    const trimmedPrincipalId = principalId.trim();
    if (!trimmedTenantId || !trimmedPrincipalId) {
      setLocalError("Tenant ID and username are required.");
      return;
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setLocalError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (password !== confirmPassword) {
      setLocalError("Passwords do not match.");
      return;
    }

    void auth.bootstrap({ tenantId: trimmedTenantId, principalId: trimmedPrincipalId, password });
  }

  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 bg-background px-4">
      <div className="flex flex-col items-center gap-1 text-center">
        <span className="text-display text-foreground">KORTEX</span>
        <span className="text-body text-muted-foreground">Set up your workspace to get started</span>
      </div>

      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Create your administrator account</CardTitle>
          <CardDescription>
            This is a new KORTEX install. Choose a tenant ID and create the first administrator account.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="bootstrap-tenant-id">Tenant ID</Label>
              <Input
                id="bootstrap-tenant-id"
                ref={tenantIdRef}
                type="text"
                autoComplete="organization"
                required
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
                disabled={isSubmitting}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="bootstrap-username">Administrator username</Label>
              <Input
                id="bootstrap-username"
                type="text"
                autoComplete="username"
                required
                value={principalId}
                onChange={(event) => setPrincipalId(event.target.value)}
                disabled={isSubmitting}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="bootstrap-password">Password</Label>
              <div className="relative">
                <Input
                  id="bootstrap-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="new-password"
                  required
                  minLength={MIN_PASSWORD_LENGTH}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={isSubmitting}
                  className="pr-9"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((shown) => !shown)}
                  disabled={isSubmitting}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {showPassword ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
                </button>
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="bootstrap-confirm-password">Confirm password</Label>
              <Input
                id="bootstrap-confirm-password"
                type={showPassword ? "text" : "password"}
                autoComplete="new-password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                disabled={isSubmitting}
              />
            </div>

            {localError && (
              <p role="alert" className="text-body text-destructive">
                {localError}
              </p>
            )}
            {auth.state.status === "BOOTSTRAP_ERROR" && (
              <p role="alert" className="text-body text-destructive">
                {auth.state.message}
              </p>
            )}

            <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting} className="mt-1">
              {isSubmitting ? (
                <>
                  <Spinner size={14} />
                  Setting up…
                </>
              ) : (
                "Create account"
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
