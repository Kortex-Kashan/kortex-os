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
} from "@kortex/design-system";

import { engineCapabilityCount, isEngineHealthy, type EngineHealthReport, type SystemHealthReport } from "../api";
import { useSystemHealth } from "../hooks/useSystemHealth";
import { AlertTriangleIcon, CheckCircleIcon, RefreshIcon, XCircleIcon } from "../icons";

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

function StatTile({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Card>
      <CardHeader className="gap-1.5 pb-6">
        <CardDescription>{label}</CardDescription>
        <CardTitle>{value}</CardTitle>
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

export function Dashboard() {
  const { data, error, isPending, isFetching, refetch } = useSystemHealth();

  if (isPending) {
    return <DashboardSkeleton />;
  }

  if (error || !data) {
    return (
      <div className="flex flex-col gap-6 p-6">
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
        <StatTile label="Kernel" value={data.kernel_state} />
        <StatTile label="Database" value={data.db_connected ? data.db_dialect : "Disconnected"} />
        <StatTile label="Engines" value={Object.keys(engines).length} />
        <StatTile label="Capabilities" value={capabilityCount ?? "—"} />
      </div>

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
