import * as React from "react";
import { Badge, Button, Input, Skeleton } from "@kortex/design-system";
import { projectCapabilities } from "@/features/workflow/api";
import type { ProjectedCapability } from "@/features/workflow/types";

export function ToolsTab() {
  const [tools, setTools] = React.useState<ProjectedCapability[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [search, setSearch] = React.useState("");

  const fetchTools = React.useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const caps = await projectCapabilities();
      setTools(caps);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load tools");
    } finally {
      setIsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void fetchTools();
  }, [fetchTools]);

  const filteredTools = React.useMemo(() => {
    if (!search.trim()) return tools;
    const q = search.toLowerCase();
    return tools.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q) ||
        t.provider.toLowerCase().includes(q),
    );
  }, [tools, search]);

  if (isLoading) {
    return (
      <div className="flex flex-1 flex-col gap-3 p-3" role="status" aria-label="Loading tools">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-center">
        <p className="text-caption text-destructive">{error}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => void fetchTools()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-2 overflow-hidden p-3">
      <div className="flex items-center gap-2">
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter tools or capabilities..."
          className="h-8 text-caption"
        />
        {search && (
          <Button variant="ghost" size="sm" className="h-8 px-2 text-xs" onClick={() => setSearch("")}>
            Clear
          </Button>
        )}
      </div>

      <div className="flex items-center justify-between py-1">
        <span className="text-caption text-muted-foreground">
          {filteredTools.length} {filteredTools.length === 1 ? "tool" : "tools"} available
        </span>
        <Button variant="ghost" size="sm" className="h-6 px-1.5 text-xs text-muted-foreground" onClick={() => void fetchTools()}>
          Reload
        </Button>
      </div>

      <div className="flex flex-1 flex-col gap-2 overflow-y-auto pr-1">
        {filteredTools.length === 0 ? (
          <div className="py-8 text-center text-caption text-muted-foreground">
            No tools match &ldquo;{search}&rdquo;
          </div>
        ) : (
          filteredTools.map((tool) => {
            const isReadOnly = tool.isReadOnly;
            const isMcp = tool.name.startsWith("kortex.mcp.");
            return (
              <div
                key={tool.name}
                className="flex flex-col gap-1 rounded-lg border border-border/80 bg-card/60 p-2.5 transition-colors hover:border-border"
              >
                <div className="flex items-start justify-between gap-1.5">
                  <span className="font-mono text-caption font-semibold text-foreground truncate" title={tool.name}>
                    {tool.name}
                  </span>
                  <div className="flex shrink-0 gap-1">
                    {isMcp && (
                      <Badge variant="outline" className="border-purple/40 bg-purple/10 text-purple text-[10px] px-1 py-0">
                        MCP
                      </Badge>
                    )}
                    {isReadOnly ? (
                      <Badge variant="outline" className="border-cyan/40 bg-cyan/10 text-cyan text-[10px] px-1 py-0">
                        Read Only
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="border-warning/40 bg-warning/10 text-warning text-[10px] px-1 py-0">
                        Governed
                      </Badge>
                    )}
                  </div>
                </div>

                <p className="text-caption text-muted-foreground line-clamp-2">
                  {tool.description || "System capability"}
                </p>

                <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground/80">
                  <span>Provider: {tool.provider}</span>
                  {tool.requiredPermissions && tool.requiredPermissions.length > 0 && (
                    <span>Perm: {tool.requiredPermissions.join(", ")}</span>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
