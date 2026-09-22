import * as React from "react";
import { Badge, Button, Skeleton } from "@kortex/design-system";
import { cancelAgentTask, listAgentTasks, type AgentTaskSummaryDto } from "@/features/ai-studio/chat-api";

export interface AgentsTabProps {
  tenantId: string;
}

const STATUS_VARIANTS: Record<string, { label: string; className: string }> = {
  RUNNING: { label: "Running", className: "border-primary/40 bg-primary/10 text-primary animate-pulse" },
  RESUMING: { label: "Resuming", className: "border-primary/40 bg-primary/10 text-primary animate-pulse" },
  PAUSED_FOR_APPROVAL: { label: "Awaiting Approval", className: "border-warning/40 bg-warning/10 text-warning" },
  COMPLETED: { label: "Completed", className: "border-success/40 bg-success/10 text-success" },
  FAILED: { label: "Failed", className: "border-destructive/40 bg-destructive/10 text-destructive" },
  CANCELLED: { label: "Cancelled", className: "border-muted bg-muted/40 text-muted-foreground" },
  TIMED_OUT: { label: "Timed Out", className: "border-destructive/40 bg-destructive/10 text-destructive" },
  STEP_LIMIT_EXCEEDED: { label: "Limit Exceeded", className: "border-destructive/40 bg-destructive/10 text-destructive" },
  LOOP_DETECTED: { label: "Loop Detected", className: "border-destructive/40 bg-destructive/10 text-destructive" },
};

export function AgentsTab({ tenantId }: AgentsTabProps) {
  const [tasks, setTasks] = React.useState<AgentTaskSummaryDto[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [cancellingId, setCancellingId] = React.useState<string | null>(null);

  const fetchTasks = React.useCallback(async () => {
    if (!tenantId) {
      setTasks([]);
      setIsLoading(false);
      return;
    }
    try {
      setError(null);
      const data = await listAgentTasks(tenantId);
      setTasks(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load agent tasks");
    } finally {
      setIsLoading(false);
    }
  }, [tenantId]);

  React.useEffect(() => {
    void fetchTasks();
  }, [fetchTasks]);

  const handleCancel = async (taskId: string) => {
    setCancellingId(taskId);
    try {
      await cancelAgentTask(taskId, tenantId);
      await fetchTasks();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel agent task");
    } finally {
      setCancellingId(null);
    }
  };

  if (isLoading) {
    return (
      <div className="flex flex-1 flex-col gap-3 p-3" role="status" aria-label="Loading agent tasks">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-center">
        <p className="text-caption text-destructive">{error}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => void fetchTasks()}>
          Retry
        </Button>
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center p-6 text-center text-muted-foreground">
        <div className="mb-2 size-8 rounded-full border border-border/80 bg-background/50 p-1.5 text-primary">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <circle cx="12" cy="12" r="10" />
            <path d="M12 6v6l4 2" />
          </svg>
        </div>
        <p className="text-body font-medium text-foreground">No active reasoning agents</p>
        <p className="text-caption max-w-xs mt-1">
          When you dispatch multi-step tasks in Chat or Workflows, autonomous reasoning agents appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-3">
      <div className="flex items-center justify-between pb-1">
        <span className="text-caption font-semibold uppercase tracking-wider text-muted-foreground">
          Reasoning Agents ({tasks.length})
        </span>
        <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => void fetchTasks()}>
          Refresh
        </Button>
      </div>
      {tasks.map((task) => {
        const variant = STATUS_VARIANTS[task.status] ?? {
          label: task.status,
          className: "border-border bg-muted/20 text-muted-foreground",
        };
        const isActive = task.status === "RUNNING" || task.status === "RESUMING" || task.status === "PAUSED_FOR_APPROVAL";

        return (
          <div
            key={task.taskId}
            className="flex flex-col gap-1.5 rounded-lg border border-border/80 bg-card/60 p-3 shadow-sm transition-colors hover:border-border"
          >
            <div className="flex items-start justify-between gap-2">
              <span className="text-body font-medium leading-snug line-clamp-2 text-foreground">
                {task.goal || "Agent Task"}
              </span>
              <Badge variant="outline" className={`shrink-0 text-caption ${variant.className}`}>
                {variant.label}
              </Badge>
            </div>

            <div className="flex items-center justify-between text-caption text-muted-foreground">
              <span className="capitalize">Role: {task.agentRole}</span>
              <span>
                Step {task.currentStep} of {task.maxSteps}
              </span>
            </div>

            {task.totalTokens > 0 && (
              <span className="text-caption text-muted-foreground">
                Tokens: {task.totalTokens.toLocaleString()}
              </span>
            )}

            {isActive && (
              <div className="mt-1 flex justify-end">
                <Button
                  variant="destructive"
                  size="sm"
                  className="h-6 px-2 text-xs"
                  onClick={() => void handleCancel(task.taskId)}
                  disabled={cancellingId === task.taskId}
                >
                  {cancellingId === task.taskId ? "Cancelling..." : "Cancel Task"}
                </Button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
