import * as React from "react";
import { Button, CardContent, CardDescription, CardHeader, CardTitle, Input, Label, Spinner } from "@kortex/design-system";

import { resetPassword } from "./authCapability";

export interface ResetPasswordFormProps {
  onBack: () => void;
  onResetSuccess: () => void;
}

/**
 * Phase A: redeems a password-reset token pasted from the reset email
 * (dev-mode-logged today, since no real email provider is configured — see
 * `DevLogEmailProvider`). Deliberately a manual paste field, not a deep-link
 * auto-fill: the OAuth deep-link plumbing this milestone also builds is the
 * higher-risk, credential-blocked piece, so the core email/password journey
 * must not depend on it working.
 */
export function ResetPasswordForm({ onBack, onResetSuccess }: ResetPasswordFormProps) {
  const [token, setToken] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [status, setStatus] = React.useState<"idle" | "submitting" | "error">("idle");
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);

  const tokenRef = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    tokenRef.current?.focus();
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === "submitting") {
      return;
    }
    setStatus("submitting");
    const outcome = await resetPassword({ token: token.trim(), newPassword });
    if (outcome.ok) {
      onResetSuccess();
      return;
    }
    setErrorMessage(outcome.message);
    setStatus("error");
    setNewPassword("");
  }

  const isSubmitting = status === "submitting";

  return (
    <>
      <CardHeader>
        <CardTitle>Set a new password</CardTitle>
        <CardDescription>Paste the reset token from your email, then choose a new password.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={(event) => void handleSubmit(event)} noValidate>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="reset-password-token">Reset token</Label>
            <Input
              id="reset-password-token"
              ref={tokenRef}
              type="text"
              required
              value={token}
              onChange={(event) => setToken(event.target.value)}
              disabled={isSubmitting}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="reset-password-new">New password</Label>
            <Input
              id="reset-password-new"
              type="password"
              autoComplete="new-password"
              required
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              disabled={isSubmitting}
            />
          </div>

          {status === "error" && (
            <p role="alert" className="text-body text-destructive">
              {errorMessage}
            </p>
          )}

          <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting} className="mt-1">
            {isSubmitting ? (
              <>
                <Spinner size={14} />
                Resetting…
              </>
            ) : (
              "Reset password"
            )}
          </Button>
          <Button type="button" variant="ghost" onClick={onBack} disabled={isSubmitting}>
            Back to sign in
          </Button>
        </form>
      </CardContent>
    </>
  );
}
