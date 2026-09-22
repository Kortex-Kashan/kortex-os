import * as React from "react";
import { Badge, Button, Skeleton } from "@kortex/design-system";
import { listConnectorProfiles } from "@/features/connectors/api";
import type { ConnectorProfile } from "@/features/connectors/types";
import { useApplicationNavigation } from "@/navigation/navigationBridge";

export function McpTab() {
  const [profiles, setProfiles] = React.useState<ConnectorProfile[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const { navigateToApplication } = useApplicationNavigation();

  const fetchProfiles = React.useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const data = await listConnectorProfiles();
      // Emphasize MCP profiles or all active connector integrations
      setProfiles(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load MCP integrations");
    } finally {
      setIsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void fetchProfiles();
  }, [fetchProfiles]);

  if (isLoading) {
    return (
      <div className="flex flex-1 flex-col gap-3 p-3" role="status" aria-label="Loading MCP servers">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-center">
        <p className="text-caption text-destructive">{error}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => void fetchProfiles()}>
          Retry
        </Button>
      </div>
    );
  }

  const mcpServers = profiles.filter(
    (p) => p.driverId === "connector-mcp" || p.driverId === "kortex.mcp" || p.driverId.includes("mcp"),
  );

  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-3">
      <div className="flex items-center justify-between pb-1">
        <span className="text-caption font-semibold uppercase tracking-wider text-muted-foreground">
          Connected MCP Servers ({mcpServers.length})
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-6 px-2 text-xs"
          onClick={() => navigateToApplication({ applicationId: "connector-engine" })}
        >
          Manage in Connectors
        </Button>
      </div>

      {mcpServers.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center p-6 text-center text-muted-foreground">
          <div className="mb-2 size-8 rounded-full border border-border/80 bg-background/50 p-1.5 text-purple">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <rect width="18" height="18" x="3" y="3" rx="2" />
              <path d="M7 7h10M7 12h10M7 17h10" />
            </svg>
          </div>
          <p className="text-body font-medium text-foreground">No MCP servers registered</p>
          <p className="text-caption max-w-xs mt-1">
            Connect Model Context Protocol (MCP) servers to allow KORTEX AI to securely access external tools, APIs, and databases.
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3 border-purple/40 text-purple hover:bg-purple/10"
            onClick={() => navigateToApplication({ applicationId: "connector-engine" })}
          >
            Configure MCP Server
          </Button>
        </div>
      ) : (
        mcpServers.map((server) => (
          <div
            key={server.profileId}
            className="flex flex-col gap-2 rounded-lg border border-border/80 bg-card/60 p-3 shadow-sm transition-colors hover:border-border"
          >
            <div className="flex items-start justify-between gap-2">
              <span className="text-body font-medium text-foreground">{server.name}</span>
              <Badge
                variant="outline"
                className={`text-caption ${
                  server.isActive
                    ? "border-success/40 bg-success/10 text-success"
                    : "border-muted bg-muted/40 text-muted-foreground"
                }`}
              >
                {server.isActive ? "Online" : "Inactive"}
              </Badge>
            </div>

            <div className="flex items-center justify-between text-caption text-muted-foreground">
              <span className="font-mono text-xs text-muted-foreground/80">{server.profileId}</span>
              <span>Rate limit: {server.rateLimitPerSec}/s</span>
            </div>
          </div>
        ))
      )}

      {profiles.length > mcpServers.length && (
        <div className="mt-2 border-t border-border/50 pt-2">
          <span className="text-caption font-semibold uppercase tracking-wider text-muted-foreground">
            Other Integrations ({profiles.length - mcpServers.length})
          </span>
          <div className="mt-1 flex flex-col gap-1">
            {profiles
              .filter((p) => !mcpServers.includes(p))
              .map((p) => (
                <div key={p.profileId} className="flex items-center justify-between rounded p-1.5 text-caption hover:bg-muted/30">
                  <span className="text-foreground">{p.name}</span>
                  <Badge variant="outline" className="text-[10px] px-1 py-0 border-border">
                    {p.driverId}
                  </Badge>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
