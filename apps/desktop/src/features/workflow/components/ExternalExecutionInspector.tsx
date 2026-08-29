/**
 * External Execution Inspector — read-only audit view of governed subprocess executions (M5.4).
 */

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
} from "@kortex/design-system";
import { useExternalExecutions } from "../hooks/useExternalExecutions";
import { WorkflowAccessDeniedError } from "../api";
import type { ExternalExecution } from "../types";
import { ExecStatusBadge, formatDateTime } from "./StatusBadge";

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function ExternalExecutionInspector() {
  const { data, isPending, isError, error, refetch, isFetching } = useExternalExecutions();

  if (isPending) {
    return (
      <section aria-label="External Execution Audit">
        <h3 className="text-heading mb-4 leading-none tracking-tight">Governed Executions</h3>
        <div className="space-y-3" role="status" aria-label="Loading executions">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      </section>
    );
  }

  if (isError) {
    const msg = error instanceof WorkflowAccessDeniedError
      ? `Access denied: ${error.message}`
      : error.message;
    return (
      <section aria-label="External Execution Audit">
        <h3 className="text-heading mb-4 leading-none tracking-tight">Governed Executions</h3>
        <div className="space-y-2" role="alert">
          <p className="text-sm text-destructive">{msg}</p>
          {!(error instanceof WorkflowAccessDeniedError) && (
            <Button variant="outline" size="sm" onClick={() => void refetch()}>Retry</Button>
          )}
        </div>
      </section>
    );
  }

  const executions = data ?? [];

  return (
    <section aria-label="External Execution Audit">
      <div className="mb-4 flex items-center justify-between gap-4">
        <h3 className="text-heading leading-none tracking-tight">Governed Executions</h3>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refetch()}
          disabled={isFetching}
          aria-label="Refresh execution list"
        >
          Refresh
        </Button>
      </div>

      {executions.length === 0 ? (
        <p className="text-body text-muted-foreground" role="status">
          No external executions recorded.
        </p>
      ) : (
        <div className="space-y-3" role="list" aria-label="External execution audit records">
          {executions.map((exec) => (
            <ExecutionCard key={exec.executionId} execution={exec} />
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Execution Card
// ---------------------------------------------------------------------------

function ExecutionCard({ execution }: { execution: ExternalExecution }) {
  return (
    <Card role="listitem" aria-label={`Execution ${execution.executionId}`}>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <CardTitle className="text-base font-mono truncate">
              {execution.executable}
            </CardTitle>
            <CardDescription className="font-mono text-xs truncate">
              {execution.executionId}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <ExecStatusBadge status={execution.status} />
            {execution.circuitBreakerOpen && (
              <Badge variant="destructive" aria-label="Circuit breaker open">Circuit Open</Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>Started</span>
          <span>{formatDateTime(execution.startedAt)}</span>
          <span>Completed</span>
          <span>{formatDateTime(execution.completedAt)}</span>
          <span>Exit Code</span>
          <span>
            {execution.exitCode !== null ? (
              <Badge variant={execution.exitCode === 0 ? "default" : "destructive"}>
                {execution.exitCode}
              </Badge>
            ) : "—"}
          </span>
          <span>Timeout</span>
          <span>{execution.timeoutSeconds}s</span>
          {execution.arguments.length > 0 && (
            <>
              <span>Arguments</span>
              <span className="font-mono text-xs truncate">{execution.arguments.join(" ")}</span>
            </>
          )}
          {execution.approvalRequestId && (
            <>
              <span>Approval ID</span>
              <span className="font-mono text-xs truncate">{execution.approvalRequestId}</span>
            </>
          )}
        </div>

        {execution.stdout && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">stdout</p>
            <pre className="text-xs bg-muted rounded-md p-2 max-h-24 overflow-auto whitespace-pre-wrap">
              {execution.stdout}
            </pre>
          </div>
        )}

        {execution.stderr && (
          <div>
            <p className="text-xs font-medium text-destructive mb-1">stderr</p>
            <pre className="text-xs bg-destructive/10 rounded-md p-2 max-h-24 overflow-auto whitespace-pre-wrap">
              {execution.stderr}
            </pre>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
