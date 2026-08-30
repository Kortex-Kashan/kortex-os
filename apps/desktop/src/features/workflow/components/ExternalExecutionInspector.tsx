/**
 * External Execution Inspector — audit view of governed external operations (M5.4).
 *
 * M5-A6: the real `execute_external_operation`/`get_external_execution`/
 * `list_external_executions` response has `target`/`output`/`error`/
 * `attempts`/`executionTimeMs`, not the subprocess-shaped
 * `executable`/`arguments`/`workingDirectory`/`exitCode`/`stdout`/`stderr`
 * the previous version of this file invented.
 *
 * M5-A7: adds a cancel control for pending/waiting-approval/running
 * executions — the backend (`kortex.workflow.workflow.external.cancel`)
 * always supported this; no UI control reached it before.
 */

import { useState } from "react";
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Skeleton,
} from "@kortex/design-system";
import { useCancelExternalExecution, useExternalExecutions } from "../hooks/useExternalExecutions";
import { usePagedList } from "../hooks/usePagedList";
import { WorkflowAccessDeniedError } from "../api";
import type { ExternalExecution } from "../types";
import { ExecStatusBadge } from "./StatusBadge";
import { PaginationControls } from "./PaginationControls";

const CANCELLABLE_STATUSES = ["PENDING", "WAITING_APPROVAL", "RUNNING"];

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function ExternalExecutionInspector() {
  const { data, isPending, isError, error, refetch, isFetching } = useExternalExecutions();
  const { pageItems, page, pageCount, hasPrev, hasNext, goPrev, goNext } = usePagedList(
    data ?? [],
    10,
  );

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
        <>
          <div className="space-y-3" role="list" aria-label="External execution audit records">
            {pageItems.map((exec) => (
              <ExecutionCard key={exec.id} execution={exec} />
            ))}
          </div>
          <PaginationControls
            page={page}
            pageCount={pageCount}
            hasPrev={hasPrev}
            hasNext={hasNext}
            onPrev={goPrev}
            onNext={goNext}
          />
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Execution Card
// ---------------------------------------------------------------------------

function ExecutionCard({ execution }: { execution: ExternalExecution }) {
  const cancelMutation = useCancelExternalExecution();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const canCancel = CANCELLABLE_STATUSES.includes(execution.status);

  const outputText =
    execution.output === null || execution.output === undefined
      ? null
      : typeof execution.output === "string"
        ? execution.output
        : JSON.stringify(execution.output, null, 2);

  return (
    <>
      <Card role="listitem" aria-label={`Execution ${execution.id}`}>
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div className="min-w-0">
              <CardTitle className="text-base font-mono truncate">{execution.target}</CardTitle>
              <CardDescription className="font-mono text-xs truncate">
                {execution.id} · tenant {execution.tenantId}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <ExecStatusBadge status={execution.status} />
              {canCancel && (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setConfirmOpen(true)}
                  aria-label={`Cancel execution ${execution.id}`}
                >
                  Cancel
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span>Attempts</span>
            <span>{execution.attempts}</span>
            <span>Duration</span>
            <span>{execution.executionTimeMs.toFixed(0)}ms</span>
            {execution.approvalRequestId && (
              <>
                <span>Approval ID</span>
                <span className="font-mono text-xs truncate">{execution.approvalRequestId}</span>
              </>
            )}
          </div>

          {outputText && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Output</p>
              <pre className="text-xs bg-muted rounded-md p-2 max-h-24 overflow-auto whitespace-pre-wrap">
                {outputText}
              </pre>
            </div>
          )}

          {execution.error && (
            <div>
              <p className="text-xs font-medium text-destructive mb-1">Error</p>
              <pre className="text-xs bg-destructive/10 rounded-md p-2 max-h-24 overflow-auto whitespace-pre-wrap">
                {execution.error}
              </pre>
            </div>
          )}

          {cancelMutation.isError && (
            <p className="text-sm text-destructive" role="alert">
              {cancelMutation.error instanceof Error
                ? cancelMutation.error.message
                : "Failed to cancel execution."}
            </p>
          )}
        </CardContent>
      </Card>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel this execution?</DialogTitle>
            <DialogDescription>
              Stops governed execution of <span className="font-mono">{execution.target}</span>. This
              cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmOpen(false)}
              disabled={cancelMutation.isPending}
            >
              Keep Running
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={cancelMutation.isPending}
              onClick={() => {
                void cancelMutation.mutateAsync(execution.id).then(() => setConfirmOpen(false));
              }}
            >
              {cancelMutation.isPending ? "Cancelling…" : "Confirm Cancel"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
