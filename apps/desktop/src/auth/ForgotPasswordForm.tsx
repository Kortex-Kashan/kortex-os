import * as React from "react";
import { Button, CardContent, CardDescription, CardHeader, CardTitle, Input, Label, Spinner } from "@kortex/design-system";

import { requestPasswordReset } from "./authCapability";

export interface ForgotPasswordFormProps {
  onBack: () => void;
  onGoToReset: () => void;
}

/**
 * Phase A: the "Forgot password?" request form, rendered by `LoginScreen`'s
 * own local `view` toggle (never part of the global `AuthState` machine —
 * this doesn't change whether the user is authenticated, only which screen
 * is showing while they remain UNAUTHENTICATED).
 *
 * Always shows the identical generic message on submit, regardless of
 * whether the email matched an account — the backend itself never reveals
 * this (`kortex.security.auth.request_password_reset`'s own
 * enumeration-resistance guarantee), so this form has nothing further to
 * distinguish.
 */
export function ForgotPasswordForm({ onBack, onGoToReset }: ForgotPasswordFormProps) {
  const [email, setEmail] = React.useState("");
  const [status, setStatus] = React.useState<"idle" | "submitting" | "done">("idle");
  const [message, setMessage] = React.useState<string | null>(null);

  const emailRef = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    emailRef.current?.focus();
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === "submitting") {
      return;
    }
    setStatus("submitting");
    const outcome = await requestPasswordReset({ email: email.trim() });
    setMessage(outcome.message);
    setStatus("done");
  }

  const isSubmitting = status === "submitting";

  return (
    <>
      <CardHeader>
        <CardTitle>Reset your password</CardTitle>
        <CardDescription>Enter your account email and we'll send you a reset link.</CardDescription>
      </CardHeader>
      <CardContent>
        {status === "done" ? (
          <div className="flex flex-col gap-4">
            <p role="status" className="text-body text-foreground">
              {message}
            </p>
            <Button type="button" variant="outline" onClick={onGoToReset}>
              I have a reset token
            </Button>
            <Button type="button" variant="ghost" onClick={onBack}>
              Back to sign in
            </Button>
          </div>
        ) : (
          <form className="flex flex-col gap-4" onSubmit={(event) => void handleSubmit(event)} noValidate>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="forgot-password-email">Email</Label>
              <Input
                id="forgot-password-email"
                ref={emailRef}
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={isSubmitting}
              />
            </div>

            <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting} className="mt-1">
              {isSubmitting ? (
                <>
                  <Spinner size={14} />
                  Sending…
                </>
              ) : (
                "Send reset link"
              )}
            </Button>
            <Button type="button" variant="ghost" onClick={onBack} disabled={isSubmitting}>
              Back to sign in
            </Button>
          </form>
        )}
      </CardContent>
    </>
  );
}
