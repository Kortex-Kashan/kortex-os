/**
 * Python Automation tab (Workflow Engine, AI Studio functional
 * stabilization Phase E). Surfaces the already-complete, already-governed
 * `kortex.python.execute`/`kortex.python.action.list` backend capabilities
 * — this tab contains no execution logic of its own, no arbitrary-code
 * input, and no second Python execution mechanism. Every action shown here
 * is one the tenant already published elsewhere (`kortex.python.action.
 * publish`, not exposed by this minimal surface); "Run" always executes
 * that action's already-pinned latest published version through the exact
 * same `CapabilityDispatcher`-governed path every other capability in this
 * app goes through.
 */

import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Skeleton } from "@kortex/design-system";
import { executePythonAction, PythonAutomationAccessDeniedError } from "../python-automation/api";
import { usePythonActions } from "../python-automation/hooks/usePythonActions";
import type { PythonAction, PythonExecutionResult } from "../python-automation/types";

export function PythonAutomationTab() {
  const { data, isPending, isError, error, refetch, isFetching } = usePythonActions();

  if (isPending) {
    return (
      <PythonAutomationShell>
        <div className="space-y-3" role="status" aria-label="Loading Python Actions">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      </PythonAutomationShell>
    );
  }

  if (isError) {
    if (error instanceof PythonAutomationAccessDeniedError) {
      return (
        <PythonAutomationShell>
          <div className="space-y-2">
            <Badge variant="destructive">Access denied</Badge>
            <p className="text-body text-muted-foreground">
              You do not have permission to view Python Actions.
            </p>
            <p className="text-caption text-muted-foreground">{error.message}</p>
          </div>
        </PythonAutomationShell>
      );
    }
    return (
      <PythonAutomationShell>
        <div className="space-y-3">
          <p className="text-body text-muted-foreground">Something went wrong loading Python Actions.</p>
          <p className="text-caption text-muted-foreground">{error.message}</p>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            Retry
          </Button>
        </div>
      </PythonAutomationShell>
    );
  }

  const actions = data ?? [];

  return (
    <PythonAutomationShell
      action={
        <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          Refresh
        </Button>
      }
    >
      {actions.length === 0 ? (
        <p className="text-body text-muted-foreground">No Python Actions have been published yet.</p>
      ) : (
        <ul className="space-y-3">
          {actions.map((action) => (
            <PythonActionRow key={action.actionId} action={action} />
          ))}
        </ul>
      )}
    </PythonAutomationShell>
  );
}

function PythonAutomationShell({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Python Automation</CardTitle>
          <CardDescription>
            Trigger already-published, governed Python Actions. Execution runs through the same
            capability-authorization boundary as every other action in KORTEX.
          </CardDescription>
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function PythonActionRow({ action }: { action: PythonAction }) {
  const runMutation = useMutation({
    mutationFn: () => executePythonAction(action.actionId, action.latestVersion),
  });
  const result = runMutation.data;

  return (
    <li className="rounded-md border border-border p-4 space-y-3" data-testid="python-action-card">
      <div className="flex items-center justify-between gap-2">
        <div>
          <span className="text-body font-medium text-foreground">{action.name}</span>
          <p className="text-caption text-muted-foreground">
            {action.actionId} · v{action.latestVersion}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!action.isActive && <Badge variant="outline">Inactive</Badge>}
          <Button
            size="sm"
            onClick={() => runMutation.mutate()}
            disabled={runMutation.isPending || !action.isActive}
          >
            {runMutation.isPending ? "Running…" : "Run"}
          </Button>
        </div>
      </div>
      {action.description && <p className="text-body text-muted-foreground">{action.description}</p>}
      <PythonRunFeedback result={result} error={runMutation.error} />
    </li>
  );
}

function PythonRunFeedback({ result, error }: { result: PythonExecutionResult | undefined; error: Error | null }) {
  if (error) {
    return (
      <p role="alert" className="text-caption text-destructive" data-testid="python-run-feedback">
        {error.message}
      </p>
    );
  }
  if (!result) {
    return null;
  }
  if (result.status !== "SUCCEEDED") {
    return (
      <p role="alert" className="text-caption text-destructive" data-testid="python-run-feedback">
        {result.status}
        {result.error ? `: ${result.error}` : ""}
      </p>
    );
  }
  return (
    <p role="status" className="text-caption text-muted-foreground" data-testid="python-run-feedback">
      {result.status} in {Math.round(result.durationMs)}ms.
    </p>
  );
}
