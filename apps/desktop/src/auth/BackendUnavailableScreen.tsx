import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@kortex/design-system";

import { useAuth } from "./AuthProvider";

/**
 * M7.1: the terminal state after `waitForBackendReady` (`backendReadiness.ts`)
 * exhausts its bounded retries, reachable *before* any login attempt — a
 * gap `LoginScreen`'s existing inline "backend unavailable" message can't
 * cover on its own, since it requires typing into three fields and
 * submitting just to trigger another connectivity check. This screen's one
 * job is a single, clear "Retry" action, calling `AuthProvider`'s
 * `retryConnection()` to re-run the startup readiness poll from scratch.
 */
export function BackendUnavailableScreen() {
  const auth = useAuth();

  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 bg-background px-4">
      <div className="flex flex-col items-center gap-1 text-center">
        <span className="text-display text-foreground">KORTEX</span>
      </div>

      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Can&apos;t reach the backend</CardTitle>
          <CardDescription>
            KORTEX couldn&apos;t connect to its backend service. It may still be starting up, or it may have
            failed to start.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p role="alert" className="text-body text-destructive">
            The KORTEX backend is not responding. Check that it&apos;s running and try again.
          </p>
          <Button type="button" onClick={() => auth.retryConnection()} className="mt-4 w-full">
            Retry
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
