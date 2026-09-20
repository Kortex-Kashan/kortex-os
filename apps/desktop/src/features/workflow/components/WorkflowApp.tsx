/**
 * Workflow workspace shell (M5.6) — tabbed navigation across:
 *  - Definitions: read-only workflow definition catalog (existing, preserved)
 *  - Manual Automation: the existing visual/manual workflow builder (was
 *    labeled "Builder" — AI Studio functional stabilization Phase E renamed
 *    the visible label only; the tab id and underlying `WorkflowBuilderTab`
 *    behavior are unchanged)
 *  - AI Automation: the natural-language AI Workflow Builder, moved here
 *    from AI Studio (Phase E) — `ai-automation/components/WorkflowBuilderPanel`,
 *    relocated wholesale, not rewritten
 *  - Python Automation: a new, minimal surface over the already-existing,
 *    already-governed `kortex.python.execute`/`kortex.python.action.list`
 *    capabilities (Phase E) — no new backend capability, no second Python
 *    execution path
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
import { WorkflowBuilderPanel } from "../ai-automation/components/WorkflowBuilderPanel";
import { InstanceTimeline } from "./InstanceTimeline";
import { ApprovalQueue } from "./ApprovalQueue";
import { PythonAutomationTab } from "./PythonAutomationTab";
import { ScheduleManager } from "./ScheduleManager";
import { ExternalExecutionInspector } from "./ExternalExecutionInspector";
import { WorkflowBuilderTab } from "./builder/WorkflowBuilderTab";

// ---------------------------------------------------------------------------
// Tab definition
// ---------------------------------------------------------------------------

type TabId =
  | "definitions"
  | "builder"
  | "aiAutomation"
  | "pythonAutomation"
  | "instances"
  | "approvals"
  | "schedules"
  | "executions";

const TABS: { id: TabId; label: string }[] = [
  { id: "definitions", label: "Definitions" },
  { id: "builder", label: "Manual Automation" },
  { id: "aiAutomation", label: "AI Automation" },
  { id: "pythonAutomation", label: "Python Automation" },
  { id: "instances", label: "Instances" },
  { id: "approvals", label: "Approvals" },
  { id: "schedules", label: "Schedules" },
  { id: "executions", label: "Governed Executions" },
];

// ---------------------------------------------------------------------------
// Main shell
// ---------------------------------------------------------------------------

const TAB_IDS: readonly TabId[] = [
  "definitions",
  "builder",
  "aiAutomation",
  "pythonAutomation",
  "instances",
  "approvals",
  "schedules",
  "executions",
];

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
        {activeTab === "definitions" && <DefinitionsTab onOpenBuilder={() => setActiveTab("builder")} />}
        {activeTab === "builder" && <WorkflowBuilderTab />}
        {activeTab === "aiAutomation" && <WorkflowBuilderPanel />}
        {activeTab === "pythonAutomation" && <PythonAutomationTab />}
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

function DefinitionsTab({ onOpenBuilder }: { onOpenBuilder?: () => void } = {}) {
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
    return <EmptyState onRefresh={() => void refetch()} isRefreshing={isFetching} onOpenBuilder={onOpenBuilder} />;
  }

  return (
    <PopulatedState
      definitions={definitions}
      onRefresh={() => void refetch()}
      isRefreshing={isFetching}
      onOpenBuilder={onOpenBuilder}
    />
  );
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

function EmptyState({
  onRefresh,
  isRefreshing,
  onOpenBuilder,
}: {
  onRefresh: () => void;
  isRefreshing: boolean;
  onOpenBuilder?: () => void;
}) {
  return (
    <WorkspaceCard
      title="Workflows"
      description="Workflow definition registry."
      action={
        <div className="flex gap-2">
          {onOpenBuilder && (
            <Button size="sm" onClick={onOpenBuilder} data-testid="definitions-new-workflow-button">
              New Workflow
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
            Refresh
          </Button>
        </div>
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
  onOpenBuilder,
}: {
  definitions: WorkflowDefinition[];
  onRefresh: () => void;
  isRefreshing: boolean;
  onOpenBuilder?: () => void;
}) {
  return (
    <WorkspaceCard
      title="Workflows"
      description={`Workflow definition registry — ${definitions.length} registered.`}
      action={
        <div className="flex gap-2">
          {onOpenBuilder && (
            <Button size="sm" onClick={onOpenBuilder} data-testid="definitions-new-workflow-button">
              New Workflow
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
            Refresh
          </Button>
        </div>
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
