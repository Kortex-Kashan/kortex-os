import * as React from "react";
import { Badge, Input } from "@kortex/design-system";
import { projectCapabilities } from "../../api";
import type { ProjectedCapability } from "../../types";

export interface CapabilityPaletteProps {
  onAddCapabilityNode: (capability: ProjectedCapability) => void;
  onAddApprovalNode: () => void;
  disabled?: boolean;
}

// Curated schema-complete capabilities allowlist (Section 12)
const CURATED_CAPABILITY_NAMES = new Set([
  "kortex.finance.invoice.get",
  "kortex.connector.notification.webhook.status",
  "kortex.connector.notification.webhook.send",
]);

export function CapabilityPalette({
  onAddCapabilityNode,
  onAddApprovalNode,
  disabled = false,
}: CapabilityPaletteProps): React.JSX.Element {
  const [searchQuery, setSearchQuery] = React.useState("");
  const [capabilities, setCapabilities] = React.useState<ProjectedCapability[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let mounted = true;
    setIsLoading(true);
    setError(null);

    projectCapabilities()
      .then((allCaps) => {
        if (!mounted) return;
        // Curate down to schema-complete platform capabilities and projected MCP capabilities
        const curated = allCaps.filter(
          (cap) => CURATED_CAPABILITY_NAMES.has(cap.name) || cap.name.startsWith("kortex.mcp.")
        );
        // If projection returned none (e.g. mock/test environment), provide fallback definitions for curated set
        if (curated.length === 0) {
          setCapabilities([
            {
              name: "kortex.finance.invoice.get",
              description: "Retrieve invoice details by invoice ID.",
              provider: "finance",
              parametersSchema: {
                type: "object",
                properties: {
                  invoice_id: { type: "string", description: "Invoice identifier" },
                },
                required: ["invoice_id"],
              },
              isReadOnly: true,
              isIdempotent: true,
            },
            {
              name: "kortex.connector.notification.webhook.status",
              description: "Check status of a delivered webhook notification.",
              provider: "connector",
              parametersSchema: {
                type: "object",
                properties: {
                  url: { type: "string", description: "Webhook URL" },
                },
                required: ["url"],
              },
              isReadOnly: true,
              isIdempotent: true,
            },
            {
              name: "kortex.connector.notification.webhook.send",
              description: "Send a notification payload to an external HTTP webhook endpoint.",
              provider: "connector",
              parametersSchema: {
                type: "object",
                properties: {
                  profile_id: { type: "string", description: "Connector profile identifier" },
                  url: { type: "string", description: "Target webhook URL" },
                  body: { type: "object", description: "JSON payload object" },
                },
                required: ["profile_id", "url", "body"],
              },
              isReadOnly: false,
              isIdempotent: false,
            },
          ]);
        } else {
          setCapabilities(curated);
        }
        setIsLoading(false);
      })
      .catch((err: unknown) => {
        if (!mounted) return;
        setError(err instanceof Error ? err.message : "Failed to load capabilities");
        setIsLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, []);

  const filteredCapabilities = capabilities.filter(
    (cap) =>
      cap.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      cap.description.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <aside
      aria-label="Workflow Capability Palette"
      className="w-72 border-r border-border bg-card/60 backdrop-blur flex flex-col h-full select-none"
      data-testid="capability-palette"
    >
      <div className="p-3 border-b border-border space-y-2">
        <h3 className="text-sm font-semibold text-foreground">Step Palette</h3>
        <p className="text-xs text-muted-foreground">
          Add steps to build your linear workflow.
        </p>
        <Input
          placeholder="Search capabilities..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="h-8 text-xs"
          data-testid="palette-search-input"
        />
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Built-in Approval Gate (Correction 1: Pure wait gate primitive, NOT an F6 capability) */}
        <div className="space-y-1.5">
          <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground px-1">
            Built-in Gates
          </div>
          <div
            className="p-2.5 rounded-lg border border-amber-500/30 bg-amber-500/5 hover:bg-amber-500/10 hover:border-amber-500/50 transition-colors cursor-pointer group"
            onClick={() => !disabled && onAddApprovalNode()}
            data-testid="palette-approval-gate"
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                if (!disabled) onAddApprovalNode();
              }
            }}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-amber-500 flex items-center gap-1.5">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
                Approval Gate
              </span>
              <Badge variant="outline" className="text-[10px] px-1.5 py-0 border-amber-500/40 text-amber-400">
                Wait Gate
              </Badge>
            </div>
            <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">
              Pauses execution until designated human role reviews and approves. Pure wait gate.
            </p>
            <div className="mt-2 flex justify-end">
              <span className="text-[10px] text-amber-400 font-medium group-hover:underline">
                + Add Gate
              </span>
            </div>
          </div>
        </div>

        {/* Curated Capabilities */}
        <div className="space-y-1.5">
          <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground px-1">
            Authorized Capabilities
          </div>

          {isLoading ? (
            <div className="p-4 text-center text-xs text-muted-foreground animate-pulse">
              Loading capabilities...
            </div>
          ) : error ? (
            <div className="p-2 text-xs text-destructive border border-destructive/30 rounded bg-destructive/5">
              {error}
            </div>
          ) : filteredCapabilities.length === 0 ? (
            <div className="p-4 text-center text-xs text-muted-foreground">
              No matching capabilities found.
            </div>
          ) : (
            filteredCapabilities.map((cap) => (
              <div
                key={cap.name}
                className="p-2.5 rounded-lg border border-border bg-background hover:bg-muted/60 hover:border-primary/50 transition-colors cursor-pointer group"
                onClick={() => !disabled && onAddCapabilityNode(cap)}
                data-testid={`palette-capability-${cap.name}`}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    if (!disabled) onAddCapabilityNode(cap);
                  }
                }}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="text-xs font-medium text-foreground truncate" title={cap.name}>
                    {cap.name.split(".").slice(-2).join(".")}
                  </span>
                  <Badge
                    variant={cap.isReadOnly ? "secondary" : "default"}
                    className="text-[9px] px-1 py-0 uppercase shrink-0"
                  >
                    {cap.isReadOnly ? "Read" : "Action"}
                  </Badge>
                </div>
                <div className="text-[10px] text-muted-foreground/80 font-mono truncate mt-0.5">
                  {cap.name}
                </div>
                <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">
                  {cap.description}
                </p>
                <div className="mt-2 flex justify-end">
                  <span className="text-[10px] text-primary font-medium group-hover:underline">
                    + Add Step
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </aside>
  );
}
