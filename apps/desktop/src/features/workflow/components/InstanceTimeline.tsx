/**
 * Workflow Instance Timeline — execution history and live instance inspector.
 *
 * M5-A6: no capability exposes per-step execution history
 * (`WorkflowStepRunModel`/`list_step_runs` exists in the persistence layer
 * but is not registered as a Kernel capability anywhere) — the previous
 * per-step timeline rendered a `steps[]` array the backend's
 * `WorkflowInstance` response never actually contains. This shows the real,
 * available instance-level state instead: the state machine value, the
 * separate operational status indicator, and the current step index/ID.
 *
 * M5-A7: the Cancel action now confirms before acting, surfaces failures
 * instead of swallowing them, and is paginated.
 */

import { useState } from "react";
import type { ReactNode } from "react";
import {
  Badge,
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
import { useCancelWorkflowInstance, useWorkflowInstances } from "../hooks/useWorkflows";
import { usePagedList } from "../hooks/usePagedList";
import { WorkflowAccessDeniedError } from "../api";
import type { WorkflowInstance } from "../types";
import { WorkflowStateBadge, WorkflowStatusBadge, formatDateTime } from "./StatusBadge";
import { PaginationControls } from "./PaginationControls";

const TERMINAL_STATES = ["COMPLETED", "FAILED", "CANCELLED"];

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function InstanceTimeline() {
  const { data, isPending, isError, error, refetch, isFetching } = useWorkflowInstances();
  const { pageItems, page, pageCount, hasPrev, hasNext, goPrev, goNext } = usePagedList(
    data ?? [],
    10,
  );

  if (isPending) return <InstancesSkeleton />;

  if (isError) {
    if (error instanceof WorkflowAccessDeniedError) {
      return <AccessDenied message={error.message} />;
    }
    return <ErrorState message={error.message} onRetry={() => void refetch()} />;
  }

  const instances = data ?? [];

  return (
    <section aria-label="Workflow Instances">
      <div className="mb-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h3 className="text-heading leading-none tracking-tight">Execution Timeline</h3>
          <span className="text-xs text-muted-foreground">{instances.length} total</span>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refetch()}
          disabled={isFetching}
          aria-label="Refresh instances"
        >
          Refresh
        </Button>
      </div>

      {instances.length === 0 ? (
        <p className="text-body text-muted-foreground" role="status">
          No workflow instances found.
        </p>
      ) : (
        <>
          <div className="space-y-3" role="list" aria-label="Workflow instance list">
            {pageItems.map((instance) => (
              <InstanceCard key={instance.id} instance={instance} />
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
// Sub-components
// ---------------------------------------------------------------------------

function InstanceCard({ instance }: { instance: WorkflowInstance }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const cancelMutation = useCancelWorkflowInstance();
  const isTerminal = TERMINAL_STATES.includes(instance.state);

  async function handleConfirmCancel() {
    try {
      await cancelMutation.mutateAsync({ instanceId: instance.id, reason: "Cancelled by operator" });
      setConfirmOpen(false);
    } catch {
      // Error is surfaced below via cancelMutation.isError; keep the dialog
      // open so the operator can see the message and retry or back out.
    }
  }

  return (
    <>
      <Card role="listitem">
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div className="min-w-0">
              <CardTitle className="text-base font-mono truncate">{instance.definitionId}</CardTitle>
              <CardDescription className="font-mono text-xs truncate">
                {instance.id} · tenant {instance.tenantId}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <WorkflowStateBadge status={instance.state} />
              <WorkflowStatusBadge status={instance.status} />
              {!isTerminal && (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setConfirmOpen(true)}
                  aria-label={`Cancel instance ${instance.id}`}
                >
                  Cancel
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span>Created</span>
            <span>{formatDateTime(instance.createdAt)}</span>
            <span>Updated</span>
            <span>{formatDateTime(instance.updatedAt)}</span>
            <span>Current Step</span>
            <span>
              #{instance.currentStepIndex}
              {instance.currentStepId ? ` (${instance.currentStepId})` : ""}
            </span>
            <span>Version</span>
            <span>
              <Badge variant="outline">v{instance.version}</Badge>
            </span>
            <span>Trace ID</span>
            <span className="font-mono text-xs truncate">{instance.traceId}</span>
          </div>
        </CardContent>
      </Card>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel workflow instance?</DialogTitle>
            <DialogDescription>
              This will stop execution of instance <span className="font-mono">{instance.id}</span>.
              This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {cancelMutation.isError && (
            <p className="text-sm text-destructive" role="alert">
              {cancelMutation.error instanceof Error
                ? cancelMutation.error.message
                : "Failed to cancel instance."}
            </p>
          )}
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
              onClick={() => void handleConfirmCancel()}
              disabled={cancelMutation.isPending}
            >
              {cancelMutation.isPending ? "Cancelling…" : "Confirm Cancel"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function InstancesSkeleton(): ReactNode {
  return (
    <div className="space-y-3" role="status" aria-label="Loading instances">
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

function AccessDenied({ message }: { message: string }) {
  return (
    <p className="text-sm text-muted-foreground" role="alert">
      Access denied: {message}
    </p>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="space-y-2" role="alert">
      <p className="text-sm text-destructive">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
