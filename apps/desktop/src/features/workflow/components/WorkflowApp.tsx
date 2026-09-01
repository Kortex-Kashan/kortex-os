/**
 * Workflow workspace shell (M5.6) — tabbed navigation across:
 *  - Definitions: read-only workflow definition catalog (existing, preserved)
 *  - Instances: execution timeline with step detail
 *  - Approvals: human governance decision queue
 *  - Schedules: durable cron schedule manager
 *  - Governed Executions: external subprocess audit view
 *
 * The existing DefinitionsTab behavior (including its test surface) is fully
 * preserved — PopulatedState/EmptyState/AccessDeniedState/ErrorState are
 * kept as-is inside the Definitions tab. The shell only adds navigation.
 */

import { useState } from "react";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
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
import { InstanceTimeline } from "./InstanceTimeline";
import { ApprovalQueue } from "./ApprovalQueue";
import { ScheduleManager } from "./ScheduleManager";
import { ExternalExecutionInspector } from "./ExternalExecutionInspector";

// ---------------------------------------------------------------------------
// Tab definition
// ---------------------------------------------------------------------------

type TabId = "definitions" | "instances" | "approvals" | "schedules" | "executions";

const TABS: { id: TabId; label: string }[] = [
  { id: "definitions", label: "Definitions" },
  { id: "instances", label: "Instances" },
  { id: "approvals", label: "Approvals" },
  { id: "schedules", label: "Schedules" },
  { id: "executions", label: "Governed Executions" },
];

// ---------------------------------------------------------------------------
// Main shell
// ---------------------------------------------------------------------------

const TAB_IDS: readonly TabId[] = ["definitions", "instances", "approvals", "schedules", "executions"];

function isTabId(value: string | null): value is TabId {
  return TAB_IDS.includes(value as TabId);
}

export function WorkflowApp() {
  const [searchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const [activeTab, setActiveTab] = useState<TabId>(isTabId(requestedTab) ? requestedTab : "definitions");

  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <nav
        role="tablist"
        aria-label="Workflow workspace tabs"
        className="flex gap-1 flex-wrap border-b border-border pb-1"
      >
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            id={`workflow-tab-${tab.id}`}
            aria-selected={activeTab === tab.id}
            aria-controls={`workflow-panel-${tab.id}`}
            onClick={() => setActiveTab(tab.id)}
            className={[
              "px-3 py-1.5 text-sm rounded-md transition-colors",
              activeTab === tab.id
                ? "bg-primary text-primary-foreground font-medium"
                : "text-muted-foreground hover:text-foreground hover:bg-muted",
            ].join(" ")}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {/* Tab panels */}
      <div
        role="tabpanel"
        id={`workflow-panel-${activeTab}`}
        aria-labelledby={`workflow-tab-${activeTab}`}
      >
        {activeTab === "definitions" && <DefinitionsTab />}
        {activeTab === "instances" && <InstanceTimeline />}
        {activeTab === "approvals" && <ApprovalQueue />}
        {activeTab === "schedules" && <ScheduleManager />}
        {activeTab === "executions" && <ExternalExecutionInspector />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Definitions Tab (existing behavior fully preserved, all tests continue to pass)
// ---------------------------------------------------------------------------

function DefinitionsTab() {
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

// ---------------------------------------------------------------------------
// Definition sub-components (unchanged from pre-M5.6)
// ---------------------------------------------------------------------------

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
