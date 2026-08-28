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
import { WorkflowAccessDeniedError } from "../api";
import { useWorkflows } from "../hooks/useWorkflows";
import type { WorkflowDefinition } from "../types";

/** The Workflow workspace: a read-only registry/overview of workflow
 * definitions, and nothing else — no designer, no creation/editing, no
 * execution UI, no scheduling. See `WorkflowAccessDeniedError`'s own
 * docstring (in `../api.ts`) for why a `PERMISSION_DENIED` failure renders
 * one unified "access denied" state here rather than branching on
 * session-expired vs. forbidden. */
export function WorkflowApp() {
  const { data, isPending, isError, error, refetch, isFetching } = useWorkflows();

  if (isPending) {
    return <LoadingState />;
  }

  if (isError) {
    if (error instanceof WorkflowAccessDeniedError) {
      return <AccessDeniedState message={error.message} />;
    }
    return <ErrorState message={error.message} onRetry={() => void refetch()} />;
  }

  const definitions = data ?? [];

  if (definitions.length === 0) {
    return <EmptyState onRefresh={() => void refetch()} isRefreshing={isFetching} />;
  }

  return <PopulatedState definitions={definitions} onRefresh={() => void refetch()} isRefreshing={isFetching} />;
}

function WorkspaceCard({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function LoadingState() {
  return (
    <WorkspaceCard title="Workflows" description="Workflow definition registry.">
      <div className="space-y-3" role="status" aria-label="Loading workflow registry">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    </WorkspaceCard>
  );
}

function EmptyState({ onRefresh, isRefreshing }: { onRefresh: () => void; isRefreshing: boolean }) {
  return (
    <WorkspaceCard
      title="Workflows"
      description="Workflow definition registry."
      action={
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
          Refresh
        </Button>
      }
    >
      <p className="text-body text-muted-foreground">No workflows are currently registered.</p>
    </WorkspaceCard>
  );
}

function AccessDeniedState({ message }: { message: string }) {
  return (
    <WorkspaceCard title="Workflows" description="Workflow definition registry.">
      <div className="space-y-2">
        <Badge variant="destructive">Access denied</Badge>
        <p className="text-body text-muted-foreground">
          You do not have permission to view the workflow registry.
        </p>
        <p className="text-caption text-muted-foreground">{message}</p>
      </div>
    </WorkspaceCard>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <WorkspaceCard title="Workflows" description="Workflow definition registry.">
      <div className="space-y-3">
        <p className="text-body text-muted-foreground">
          Something went wrong loading the workflow registry.
        </p>
        <p className="text-caption text-muted-foreground">{message}</p>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </div>
    </WorkspaceCard>
  );
}

function PopulatedState({
  definitions,
  onRefresh,
  isRefreshing,
}: {
  definitions: WorkflowDefinition[];
  onRefresh: () => void;
  isRefreshing: boolean;
}) {
  return (
    <WorkspaceCard
      title="Workflows"
      description={`Workflow definition registry — ${definitions.length} registered.`}
      action={
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
          Refresh
        </Button>
      }
    >
      <ul className="space-y-3">
        {definitions.map((definition) => (
          <li
            key={definition.id}
            className="rounded-md border border-border p-4"
            data-testid="workflow-definition-card"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-body font-medium text-foreground">{definition.name}</span>
              <Badge variant="secondary">v{definition.version}</Badge>
            </div>
            <p className="text-caption text-muted-foreground">
              {definition.id} · {definition.trigger} · {definition.priority} · {definition.steps.length}{" "}
              step{definition.steps.length === 1 ? "" : "s"}
            </p>
            <p className="mt-2 text-body text-muted-foreground">{definition.description}</p>
          </li>
        ))}
      </ul>
    </WorkspaceCard>
  );
}
