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
  Skeleton,
} from "@kortex/design-system";
import { ConnectorAccessDeniedError } from "../api";
import { useConnectors } from "../hooks/useConnectors";
import type { ConnectorDriver } from "../types";
import { ConnectionsTab } from "./ConnectionsTab";

type ConnectorTab = "drivers" | "connections";

const TAB_LABEL: Record<ConnectorTab, string> = {
  drivers: "Drivers",
  connections: "Connections",
};

/** The Connectors workspace: tabbed between the connector/driver registry
 * (read-only, unchanged since M5) and Connections (M7.3) — where a tenant
 * admin creates, lists, and deletes the tenant's own connector profiles and
 * their credentials. No install flow and no marketplace either tab — both
 * remain explicitly out of scope for this milestone. See
 * `ConnectorAccessDeniedError`'s own docstring for why a `PERMISSION_DENIED`
 * failure renders one unified "access denied" state rather than branching on
 * session-expired vs. forbidden — the M4.1 session boundary that would own
 * that distinction is not part of this branch yet, and the transport does
 * not carry the underlying HTTP status code today regardless. */
export function ConnectorsApp() {
  const [activeTab, setActiveTab] = useState<ConnectorTab>("drivers");

  return (
    <div className="space-y-4">
      <nav role="tablist" aria-label="Connectors tabs" className="flex gap-1 border-b border-border pb-1">
        {(["drivers", "connections"] as ConnectorTab[]).map((tab) => (
          <button
            key={tab}
            role="tab"
            id={`connector-tab-${tab}`}
            aria-selected={activeTab === tab}
            aria-controls={`connector-panel-${tab}`}
            onClick={() => setActiveTab(tab)}
            className={[
              "px-3 py-1.5 text-sm rounded-md transition-colors",
              activeTab === tab
                ? "bg-primary text-primary-foreground font-medium"
                : "text-muted-foreground hover:text-foreground hover:bg-muted",
            ].join(" ")}
          >
            {TAB_LABEL[tab]}
          </button>
        ))}
      </nav>

      <div role="tabpanel" id={`connector-panel-${activeTab}`} aria-labelledby={`connector-tab-${activeTab}`}>
        {activeTab === "drivers" && <DriverRegistry />}
        {activeTab === "connections" && <ConnectionsTab />}
      </div>
    </div>
  );
}

function DriverRegistry() {
  const { data, isPending, isError, error, refetch, isFetching } = useConnectors();

  if (isPending) {
    return <LoadingState />;
  }

  if (isError) {
    if (error instanceof ConnectorAccessDeniedError) {
      return <AccessDeniedState message={error.message} />;
    }
    return <ErrorState message={error.message} onRetry={() => void refetch()} />;
  }

  const drivers = data ?? [];

  if (drivers.length === 0) {
    return <EmptyState onRefresh={() => void refetch()} isRefreshing={isFetching} />;
  }

  return <PopulatedState drivers={drivers} onRefresh={() => void refetch()} isRefreshing={isFetching} />;
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
    <WorkspaceCard title="Connectors" description="Connector / driver registry.">
      <div className="space-y-3" role="status" aria-label="Loading connector registry">
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
      title="Connectors"
      description="Connector / driver registry."
      action={
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
          Refresh
        </Button>
      }
    >
      <p className="text-body text-muted-foreground">No connectors are currently registered.</p>
    </WorkspaceCard>
  );
}

function AccessDeniedState({ message }: { message: string }) {
  return (
    <WorkspaceCard title="Connectors" description="Connector / driver registry.">
      <div className="space-y-2">
        <Badge variant="destructive">Access denied</Badge>
        <p className="text-body text-muted-foreground">
          You do not have permission to view the connector registry.
        </p>
        <p className="text-caption text-muted-foreground">{message}</p>
      </div>
    </WorkspaceCard>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <WorkspaceCard title="Connectors" description="Connector / driver registry.">
      <div className="space-y-3">
        <p className="text-body text-muted-foreground">
          Something went wrong loading the connector registry.
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
  drivers,
  onRefresh,
  isRefreshing,
}: {
  drivers: ConnectorDriver[];
  onRefresh: () => void;
  isRefreshing: boolean;
}) {
  return (
    <WorkspaceCard
      title="Connectors"
      description={`Connector / driver registry — ${drivers.length} registered.`}
      action={
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
          Refresh
        </Button>
      }
    >
      <ul className="space-y-3">
        {drivers.map((driver) => (
          <li
            key={driver.driverId}
            className="rounded-md border border-border p-4"
            data-testid="connector-driver-card"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-body font-medium text-foreground">{driver.displayName}</span>
              <Badge variant="secondary">v{driver.version}</Badge>
            </div>
            <p className="text-caption text-muted-foreground">
              {driver.vendor} · {driver.driverId}
            </p>
            <p className="mt-2 text-body text-muted-foreground">{driver.description}</p>
          </li>
        ))}
      </ul>
    </WorkspaceCard>
  );
}
