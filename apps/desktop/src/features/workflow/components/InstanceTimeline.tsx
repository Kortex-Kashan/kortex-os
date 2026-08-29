/**
 * Workflow Instance Timeline — execution history and live instance inspector.
 *
 * Displays the list of workflow instances with status, progress, and
 * step-level detail. Supports cancel and resume actions.
 */

import type { ReactNode } from "react";
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
import { useWorkflowInstances } from "../hooks/useWorkflows";
import { WorkflowAccessDeniedError, cancelWorkflowInstance } from "../api";
import type { WorkflowInstance, StepRecord } from "../types";
import { WorkflowStatusBadge, StepStatusBadge, formatDateTime } from "./StatusBadge";

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function InstanceTimeline() {
  const { data, isPending, isError, error, refetch, isFetching } = useWorkflowInstances();

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
        <h3 className="text-heading leading-none tracking-tight">Execution Timeline</h3>
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
        <div className="space-y-3" role="list" aria-label="Workflow instance list">
          {instances.map((instance) => (
            <InstanceCard key={instance.instanceId} instance={instance} onRefresh={() => void refetch()} />
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function InstanceCard({ instance, onRefresh }: { instance: WorkflowInstance; onRefresh: () => void }) {
  const isTerminal = ["COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "COMPENSATED"].includes(
    instance.status,
  );

  async function handleCancel() {
    try {
      await cancelWorkflowInstance(instance.instanceId, "Cancelled by operator");
      onRefresh();
    } catch {
      /* surface via refetch error state */
    }
  }

  return (
    <Card role="listitem">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <CardTitle className="text-base truncate">{instance.workflowName}</CardTitle>
            <CardDescription className="font-mono text-xs truncate">
              {instance.instanceId}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <WorkflowStatusBadge status={instance.status} />
            {!isTerminal && (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => void handleCancel()}
                aria-label={`Cancel instance ${instance.instanceId}`}
              >
                Cancel
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>Started</span>
          <span>{formatDateTime(instance.startedAt)}</span>
          <span>Completed</span>
          <span>{formatDateTime(instance.completedAt)}</span>
          <span>Steps</span>
          <span>
            {instance.currentStepIndex + 1} / {instance.totalSteps}
          </span>
          <span>Priority</span>
          <span>
            <Badge variant="outline">{instance.priority}</Badge>
          </span>
        </div>

        {instance.errorMessage && (
          <p
            className="text-sm text-destructive bg-destructive/10 rounded-md px-3 py-2"
            role="alert"
          >
            {instance.errorMessage}
          </p>
        )}

        {instance.steps.length > 0 && <StepList steps={instance.steps} />}
      </CardContent>
    </Card>
  );
}

function StepList({ steps }: { steps: StepRecord[] }) {
  return (
    <div role="list" aria-label="Step timeline" className="space-y-1">
      {steps.map((step, idx) => (
        <div
          key={step.stepId}
          role="listitem"
          className="flex items-center gap-3 text-sm py-1 border-l-2 pl-3"
          style={{
            borderColor:
              step.status === "COMPLETED"
                ? "var(--color-success, #22c55e)"
                : step.status === "RUNNING"
                  ? "var(--color-primary)"
                  : step.status === "FAILED"
                    ? "var(--color-destructive)"
                    : "var(--color-border)",
          }}
        >
          <span className="text-muted-foreground w-4 text-xs">{idx + 1}</span>
          <span className="flex-1 truncate font-medium">{step.stepName}</span>
          <StepStatusBadge status={step.status} />
          {step.errorMessage && (
            <span className="text-xs text-destructive truncate max-w-[200px]">
              {step.errorMessage}
            </span>
          )}
        </div>
      ))}
    </div>
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
