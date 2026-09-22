import * as React from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  cn,
} from "@kortex/design-system";

import { useQuery } from "@tanstack/react-query";
import { useOptionalAuth } from "@/auth/AuthProvider";
import { useApplicationNavigation } from "@/navigation/navigationBridge";
import { useWorkspace } from "@/workspace/WorkspaceProvider";
import type { WorkspaceApplication } from "@/workspace/workspaceTypes";
import { listPendingApprovals, listWorkflowInstances } from "@/features/workflow/api";

import { engineCapabilityCount, isEngineHealthy, type EngineHealthReport, type SystemHealthReport } from "../api";
import { useSystemHealth } from "../hooks/useSystemHealth";
import {
  ActivityIcon,
  AlertTriangleIcon,
  ArrowRightIcon,
  BoltIcon,
  BoxIcon,
  BriefcaseIcon,
  ChartIcon,
  CheckCircleIcon,
  DatabaseIcon,
  LayersIcon,
  PlayIcon,
  RefreshIcon,
  ServerIcon,
  SparklesIcon,
  XCircleIcon,
} from "../icons";

const STATUS_COPY: Record<string, { label: string; badgeVariant: "default" | "outline" }> = {
  healthy: { label: "All systems operational", badgeVariant: "default" },
  degraded: { label: "System degraded", badgeVariant: "outline" },
};

function formatEngineName(name: string): string {
  return name.replace(/_/g, " ");
}

/**
 * Best-effort, generic summary of an engine's extra diagnostic fields
 * beyond `engine`/`status`/`healthy`/`error` — different engines report
 * different extra fields (see the individual engines under
 * `backend/src/kortex/engines`) and this deliberately does not hard-code
 * per-engine field names, so it stays honest as those reports evolve
 * rather than silently going stale. Nested objects/arrays are skipped
 * (e.g. Storage's `stores`, Boot's `boot_order`) — those don't summarize
 * legibly as inline text.
 */
function engineDetail(report: EngineHealthReport): string | null {
  if (typeof report.error === "string" && report.error) {
    return report.error;
  }
  const parts = Object.entries(report)
    .filter(([key]) => !["engine", "status", "healthy", "error"].includes(key))
    .filter(([, value]) => typeof value !== "object")
    .map(([key, value]) => `${formatEngineName(key)}: ${String(value)}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

function OverallStatusBanner({ report }: { report: SystemHealthReport }) {
  const status = report.system_health.status;
  const copy = STATUS_COPY[status] ?? { label: `Status: ${status}`, badgeVariant: "outline" as const };
  const Icon = status === "healthy" ? CheckCircleIcon : AlertTriangleIcon;

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-3 pt-6">
        <Icon
          className={status === "healthy" ? "size-6 text-primary" : "size-6 text-foreground"}
        />
        <div className="flex min-w-0 flex-col gap-1">
          <div role="status" className="flex flex-wrap items-center gap-2">
            <span className="text-heading">{copy.label}</span>
            <Badge variant={copy.badgeVariant}>{status}</Badge>
          </div>
          <p className="text-body text-muted-foreground">
            Kernel {report.kernel_state.toLowerCase()} · Database{" "}
            {report.db_connected ? `connected (${report.db_dialect})` : "disconnected"}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function StatTile({
  label,
  value,
  icon: Icon,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  icon?: React.ComponentType<React.SVGProps<SVGSVGElement>>;
  accent?: string;
}) {
  return (
    <Card>
      <CardHeader className="gap-1.5 pb-6">
        <div className="flex items-center justify-between">
          <CardDescription>{label}</CardDescription>
          {Icon && (
            <span
              className={cn(
                "grid size-7 shrink-0 place-items-center rounded-md bg-primary/10",
                accent ?? "text-primary",
              )}
            >
              <Icon className="size-4" aria-hidden="true" />
            </span>
          )}
        </div>
        <CardTitle className="font-display">{value}</CardTitle>
      </CardHeader>
    </Card>
  );
}

function StatTileSkeleton({ label }: { label: string }) {
  return (
    <Card>
      <CardHeader className="gap-1.5 pb-6">
        <CardDescription>{label}</CardDescription>
        <Skeleton className="h-7 w-16" />
      </CardHeader>
    </Card>
  );
}

function EngineHealthTable({ engines }: { engines: Record<string, EngineHealthReport> }) {
  const entries = Object.entries(engines);

  if (entries.length === 0) {
    return <p className="text-body text-muted-foreground">No engines are currently registered.</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Engine</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Details</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entries.map(([name, report]) => {
          const healthy = isEngineHealthy(report);
          const detail = engineDetail(report);
          return (
            <TableRow key={name}>
              <TableCell className="font-medium capitalize">{formatEngineName(name)}</TableCell>
              <TableCell>
                <div className="flex items-center gap-2">
                  {healthy ? (
                    <CheckCircleIcon className="size-4 shrink-0 text-primary" aria-hidden="true" />
                  ) : (
                    <XCircleIcon className="size-4 shrink-0 text-destructive" aria-hidden="true" />
                  )}
                  <Badge variant={healthy ? "default" : "destructive"}>
                    {report.status ?? (healthy ? "healthy" : "unhealthy")}
                  </Badge>
                </div>
              </TableCell>
              <TableCell className="text-caption text-muted-foreground">{detail ?? "—"}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function DashboardSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-6 p-6">
      <span className="sr-only">Loading system health…</span>
      <Skeleton className="h-24 w-full" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTileSkeleton label="Kernel" />
        <StatTileSkeleton label="Database" />
        <StatTileSkeleton label="Engines" />
        <StatTileSkeleton label="Capabilities" />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Engine Health</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}

function useLiveClock(): string {
  const [time, setTime] = React.useState("");
  React.useEffect(() => {
    const format = new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    const update = () => setTime(format.format(new Date()));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, []);
  return time;
}

const MODE_CHIPS = ["Manage", "Automate", "Communicate", "Analyze", "Grow"];

/**
 * Purely presentational — no `useSystemHealth`/`data` dependency — so it
 * renders identically across the loading/error/success branches below and
 * keeps the shell feeling alive even while the health check is in flight
 * or unreachable (Section 8/9 of the visual transformation brief: "the
 * shell should remain visually stable while users navigate").
 */
function Hero() {
  const time = useLiveClock();
  const dateLabel = React.useMemo(
    () => new Intl.DateTimeFormat("en-US", { weekday: "long", day: "numeric", month: "long" }).format(new Date()),
    [],
  );

  return (
    <section className="relative overflow-hidden rounded-lg border border-border/70 bg-gradient-to-br from-primary/15 via-panel to-background p-6 shadow-panel sm:p-8">
      <div
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_15%_20%,hsl(var(--primary)/0.28),transparent_45%)]"
        aria-hidden="true"
      />
      <div className="relative flex flex-wrap items-start justify-between gap-6">
        <div className="max-w-xl">
          <span className="inline-flex items-center gap-1.5 text-caption font-semibold uppercase tracking-[0.14em] text-cyan">
            <span className="size-1.5 rounded-full bg-cyan shadow-[0_0_8px_hsl(var(--cyan))]" aria-hidden="true" />
            Command center
          </span>
          <h1 className="mt-3 font-display text-3xl font-bold text-foreground sm:text-4xl">
            Your business. Your AI. Your Operating System.
          </h1>
          <div className="mt-4 flex flex-wrap gap-2">
            {MODE_CHIPS.map((mode) => (
              <span
                key={mode}
                className="rounded-full border border-border/70 bg-background/60 px-3 py-1 text-caption text-muted-foreground backdrop-blur-sm"
              >
                {mode}
              </span>
            ))}
          </div>
        </div>
        <div className="text-right" aria-hidden={time === ""}>
          <div className="font-display text-2xl font-semibold tabular-nums text-foreground">{time}</div>
          <div className="mt-1 text-caption text-muted-foreground">{dateLabel}</div>
        </div>
      </div>
    </section>
  );
}

const COMING_SOON_TILES: {
  label: string;
  detail: string;
  icon: React.ComponentType<React.SVGProps<SVGSVGElement>>;
}[] = [
  { label: "CRM", detail: "Leads, customers & pipeline", icon: BriefcaseIcon },
  { label: "ERP", detail: "Finance, inventory & people", icon: BoxIcon },
  { label: "Analytics", detail: "Real-time business intelligence", icon: ChartIcon },
];

/**
 * The real registered workspace applications (`WorkspaceRegistry`, via
 * `useWorkspace()`) as a tile grid, plus a small number of clearly-marked
 * "Coming soon" tiles for sections that don't exist yet — never fabricated
 * as working destinations (Section 20 of the brief: don't fill the
 * interface with empty pages pretending to be complete).
 */
function WorkspaceGrid({
  apps,
  onNavigate,
}: {
  apps: WorkspaceApplication[];
  onNavigate: (applicationId: string) => void;
}) {
  const tiles = apps.filter((app) => app.id !== "dashboard");

  return (
    <section aria-label="Workspace applications">
      <h2 className="mb-3 text-heading">Jump back in</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {tiles.map((app) => {
          const Icon = app.icon;
          return (
            <button
              key={app.id}
              type="button"
              onClick={() => onNavigate(app.id)}
              className="group flex items-center gap-3 rounded-lg border border-border/70 bg-card/80 p-3 text-left shadow-low backdrop-blur-md transition hover:-translate-y-0.5 hover:border-primary/50 hover:shadow-glow"
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-md bg-primary/10 text-primary">
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-body font-medium text-foreground">{app.name}</span>
                <span className="block truncate text-caption text-muted-foreground">{app.description}</span>
              </span>
              <ArrowRightIcon className="ml-auto size-4 shrink-0 text-muted-foreground opacity-0 transition group-hover:opacity-100" />
            </button>
          );
        })}
        {COMING_SOON_TILES.map((tile) => (
          <div
            key={tile.label}
            aria-disabled="true"
            className="flex items-center gap-3 rounded-lg border border-dashed border-border/60 bg-muted/30 p-3 opacity-70"
          >
            <span className="grid size-9 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
              <tile.icon className="size-4" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-body font-medium text-foreground">{tile.label}</span>
              <span className="block truncate text-caption text-muted-foreground">{tile.detail}</span>
            </span>
            <Badge variant="outline" className="ml-auto shrink-0 text-caption">
              Soon
            </Badge>
          </div>
        ))}
      </div>
    </section>
  );
}

function ClassificationBadge({ classification }: { classification: "REAL" | "DERIVED" | "UNAVAILABLE" }) {
  const styles = {
    REAL: "text-success border-success/30 bg-success/10",
    DERIVED: "text-cyan border-cyan/30 bg-cyan/10",
    UNAVAILABLE: "text-muted-foreground border-border/70 bg-muted/20",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold tracking-wider uppercase",
        styles[classification],
      )}
    >
      {classification}
    </span>
  );
}

function OperationsRow({ report, onNavigate }: { report: SystemHealthReport; onNavigate: (appId: string) => void }) {
  const auth = useOptionalAuth();
  const isAuthenticated = auth?.state.status === "AUTHENTICATED";

  const { data: approvals = [] } = useQuery({
    queryKey: ["dashboard", "pending-approvals"],
    queryFn: () => listPendingApprovals(),
    enabled: isAuthenticated,
    staleTime: 15_000,
  });

  const { data: instances = [] } = useQuery({
    queryKey: ["dashboard", "active-instances"],
    queryFn: () => listWorkflowInstances({ state: "RUNNING" }),
    enabled: isAuthenticated,
    staleTime: 15_000,
  });

  const degradedEngines = Object.entries(report.system_health.engines).filter(
    ([, engineReport]) => !isEngineHealthy(engineReport),
  );

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      {/* 1. Pending Approvals */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <CardTitle className="text-heading">Pending Approvals</CardTitle>
          <ClassificationBadge classification={isAuthenticated ? "REAL" : "UNAVAILABLE"} />
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {!isAuthenticated ? (
            <p className="text-caption text-muted-foreground">Sign in to view real pending approvals.</p>
          ) : approvals.length === 0 ? (
            <div className="flex items-center gap-2.5">
              <CheckCircleIcon className="size-4 shrink-0 text-success" aria-hidden="true" />
              <p className="text-caption text-muted-foreground">No approvals requiring operator review.</p>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {approvals.slice(0, 3).map((req) => (
                <div key={req.id} className="flex items-center justify-between gap-2.5 rounded border border-border/50 p-2 text-caption">
                  <div className="min-w-0 flex-1 truncate">
                    <span className="font-mono text-xs">{req.id.slice(0, 8)}...</span>
                    {req.stepId && <span className="ml-1 text-muted-foreground">({req.stepId})</span>}
                  </div>
                  <Badge variant="outline" className="text-warning text-[10px]">
                    {req.requiredRole}
                  </Badge>
                </div>
              ))}
              <Button
                variant="ghost"
                size="sm"
                className="mt-1 justify-start p-0 text-caption text-primary hover:underline"
                onClick={() => onNavigate("workflow-engine")}
              >
                Open Workflow Approvals &rarr;
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 2. Ongoing Automations */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <CardTitle className="text-heading">Active Automations</CardTitle>
          <ClassificationBadge classification={isAuthenticated ? "REAL" : "UNAVAILABLE"} />
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {!isAuthenticated ? (
            <p className="text-caption text-muted-foreground">Sign in to monitor workflow executions.</p>
          ) : instances.length === 0 ? (
            <div className="flex items-center gap-2.5">
              <PlayIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <p className="text-caption text-muted-foreground">No workflow instances currently executing.</p>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {instances.slice(0, 3).map((inst) => (
                <div key={inst.id} className="flex items-center justify-between gap-2.5 rounded border border-border/50 p-2 text-caption">
                  <div className="min-w-0 flex-1 truncate">
                    <span className="font-mono text-xs">{inst.id.slice(0, 8)}...</span>
                    <span className="ml-1 text-muted-foreground">step {inst.currentStepIndex}</span>
                  </div>
                  <Badge variant="secondary" className="text-[10px]">
                    {inst.state}
                  </Badge>
                </div>
              ))}
              <Button
                variant="ghost"
                size="sm"
                className="mt-1 justify-start p-0 text-caption text-primary hover:underline"
                onClick={() => onNavigate("workflow-engine")}
              >
                View all runs in Workflows &rarr;
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 3. System Attention / Sentinel */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <CardTitle className="text-heading">System Attention</CardTitle>
          <ClassificationBadge classification="DERIVED" />
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {degradedEngines.length === 0 ? (
            <div className="flex items-center gap-2.5">
              <CheckCircleIcon className="size-4 shrink-0 text-success" aria-hidden="true" />
              <p className="text-caption text-muted-foreground">All registered system engines operating normally.</p>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {degradedEngines.map(([name, rep]) => (
                <div key={name} className="flex items-center justify-between gap-2 text-caption text-destructive">
                  <span className="font-medium capitalize">{formatEngineName(name)} engine</span>
                  <Badge variant="destructive" className="text-[10px]">
                    {rep.status ?? "Degraded"}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function InsightPanel() {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="text-heading">AI Operational Insight</CardTitle>
        <ClassificationBadge classification="UNAVAILABLE" />
      </CardHeader>
      <CardContent className="flex items-start gap-4">
        <span className="grid size-11 shrink-0 place-items-center rounded-full border border-chart-4/40 text-chart-4 shadow-[0_0_20px_hsl(var(--chart-4)/0.25)]">
          <SparklesIcon className="size-5" aria-hidden="true" />
        </span>
        <div>
          <p className="text-body font-medium text-foreground">
            AI operational telemetry analysis is currently unavailable.
          </p>
          <p className="mt-1 text-caption text-muted-foreground">
            KORTEX Sentinel will generate continuous business insights once operational activity streams are connected.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

export function Dashboard() {
  const { data, error, isPending, isFetching, refetch } = useSystemHealth();
  const { applications } = useWorkspace();
  const { navigateToApplication } = useApplicationNavigation();
  const goTo = React.useCallback(
    (applicationId: string) => navigateToApplication({ applicationId }),
    [navigateToApplication],
  );

  if (isPending) {
    return <DashboardSkeleton />;
  }

  if (error || !data) {
    return (
      <div className="flex flex-col gap-6 p-6">
        <Hero />
        <WorkspaceGrid apps={applications} onNavigate={goTo} />

        <header>
          <h1 className="text-display">Dashboard</h1>
          <p className="text-body text-muted-foreground">System overview and health at a glance</p>
        </header>
        <Card role="alert">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0">
            <XCircleIcon className="mt-1 size-5 shrink-0 text-destructive" aria-hidden="true" />
            <div className="flex flex-col gap-1">
              <CardTitle>Unable to load system health</CardTitle>
              <CardDescription>
                {error instanceof Error ? error.message : "Unable to reach the KORTEX backend."}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              <RefreshIcon className={isFetching ? "animate-spin" : undefined} />
              Retry
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const engines = data.system_health.engines;
  const capabilityCount = engineCapabilityCount(engines);

  return (
    <div className="flex flex-col gap-6 p-6">
      <Hero />
      <WorkspaceGrid apps={applications} onNavigate={goTo} />

      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-display">Dashboard</h1>
          <p className="text-body text-muted-foreground">System overview and health at a glance</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshIcon className={isFetching ? "animate-spin" : undefined} />
          Refresh
        </Button>
      </header>

      <OverallStatusBanner report={data} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Kernel" value={data.kernel_state} icon={ServerIcon} accent="text-primary" />
        <StatTile
          label="Database"
          value={data.db_connected ? data.db_dialect : "Disconnected"}
          icon={DatabaseIcon}
          accent="text-cyan"
        />
        <StatTile label="Engines" value={Object.keys(engines).length} icon={LayersIcon} accent="text-chart-4" />
        <StatTile label="Capabilities" value={capabilityCount ?? "—"} icon={BoltIcon} accent="text-warning" />
      </div>

      <OperationsRow report={data} onNavigate={goTo} />
      <InsightPanel />

      <Card>
        <CardHeader>
          <CardTitle>Engine Health</CardTitle>
          <CardDescription>Status reported directly by each registered system engine.</CardDescription>
        </CardHeader>
        <CardContent>
          <EngineHealthTable engines={engines} />
        </CardContent>
      </Card>
    </div>
  );
}
