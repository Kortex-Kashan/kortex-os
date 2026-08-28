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

/**
 * The KORTEX Desktop login screen (M4.1). Rendered by `AuthGate` for every
 * non-CHECKING, non-AUTHENTICATED state — `useAuth()`'s `state.status`
 * distinguishes UNAUTHENTICATED / AUTHENTICATING / AUTHENTICATION_ERROR /
 * BACKEND_UNAVAILABLE without this component ever remounting between them.
 *
 * Deliberately plain: no illustration, no marketing copy, no animated
 * background — an enterprise desktop sign-in, not a landing page. Every
 * color/spacing/typography value below comes from `@kortex/design-system`'s
 * existing tokens (no arbitrary colors introduced).
 */
export function LoginScreen() {
  const auth = useAuth();
  const [tenantId, setTenantId] = React.useState("");
  const [principalId, setPrincipalId] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);

  const tenantIdRef = React.useRef<HTMLInputElement>(null);
  const previousStatusRef = React.useRef(auth.state.status);

  // Autofocus the first meaningful field once, on mount.
  React.useEffect(() => {
    tenantIdRef.current?.focus();
  }, []);

  // Password discipline (Phase 5 of the M4.1 brief): clear the password —
  // never the tenant ID or username — the moment an attempt fails. Watches
  // the *transition away from* AUTHENTICATING rather than the destination
  // status directly, so a fresh AUTHENTICATION_ERROR always clears the
  // field that was just rejected without depending on render timing.
  React.useEffect(() => {
    const previous = previousStatusRef.current;
    previousStatusRef.current = auth.state.status;
    if (previous === "AUTHENTICATING" && auth.state.status !== "AUTHENTICATED") {
      setPassword("");
    }
  }, [auth.state.status]);

  // Clear on unmount too (e.g. a successful login swaps this component out
  // via AuthGate) — belt-and-suspenders alongside the effect above, since
  // this component's own state disappears with it regardless.
  React.useEffect(() => {
    return () => setPassword("");
  }, []);

  const isAuthenticating = auth.state.status === "AUTHENTICATING";

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isAuthenticating) {
      return;
    }
    void auth.login({ tenantId: tenantId.trim(), principalId: principalId.trim(), password });
  }

  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 bg-background px-4">
      <div className="flex flex-col items-center gap-1 text-center">
        <span className="text-display text-foreground">KORTEX</span>
        <span className="text-body text-muted-foreground">Sign in to your desktop workspace</span>
      </div>

      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>Enter your tenant, username, and password to continue.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="login-tenant-id">Tenant ID</Label>
              <Input
                id="login-tenant-id"
                ref={tenantIdRef}
                type="text"
                autoComplete="organization"
                required
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
                disabled={isAuthenticating}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="login-username">Username</Label>
              <Input
                id="login-username"
                type="text"
                autoComplete="username"
                required
                value={principalId}
                onChange={(event) => setPrincipalId(event.target.value)}
                disabled={isAuthenticating}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="login-password">Password</Label>
              <div className="relative">
                <Input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={isAuthenticating}
                  className="pr-9"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((shown) => !shown)}
                  disabled={isAuthenticating}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {showPassword ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
                </button>
              </div>
            </div>

            {auth.state.status === "AUTHENTICATION_ERROR" && (
              <p role="alert" className="text-body text-destructive">
                {auth.state.message}
              </p>
            )}
            {auth.state.status === "BACKEND_UNAVAILABLE" && (
              <p role="alert" className="text-body text-destructive">
                Can&apos;t reach the KORTEX backend. Check that it&apos;s running and try again.
              </p>
            )}

            <Button type="submit" disabled={isAuthenticating} aria-busy={isAuthenticating} className="mt-1">
              {isAuthenticating ? (
                <>
                  {/* Spinner already carries role="status"/aria-label="Loading"
                      (@kortex/design-system) — this IS the accessible loading
                      indication, not a decorative icon, so it must stay out of
                      aria-hidden. */}
                  <Spinner size={14} />
                  Signing in…
                </>
              ) : (
                "Sign In"
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
