import * as React from "react";
import { Badge, Button } from "@kortex/design-system";

export interface ValidationIssue {
  type: "error" | "warning";
  message: string;
  nodeId?: string;
}

export interface ValidationPanelProps {
  issues: ValidationIssue[];
  onSelectNode?: (nodeId: string) => void;
}

export function ValidationPanel({
  issues,
  onSelectNode,
}: ValidationPanelProps): React.JSX.Element {
  const [isOpen, setIsOpen] = React.useState(true);

  const errors = issues.filter((i) => i.type === "error");
  const warnings = issues.filter((i) => i.type === "warning");
  const isValid = errors.length === 0;

  return (
    <div
      className="border-t border-border bg-card/80 backdrop-blur transition-all duration-200"
      data-testid="validation-panel"
    >
      <div className="px-4 py-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Badge
            variant={isValid ? "default" : "destructive"}
            className="text-[10px] uppercase font-bold tracking-wider"
            data-testid="validation-status-badge"
          >
            {isValid ? "Valid Linear Pipeline" : `${errors.length} Error${errors.length > 1 ? "s" : ""}`}
          </Badge>
          {warnings.length > 0 && (
            <Badge variant="outline" className="text-[10px] text-amber-500 border-amber-500/40">
              {warnings.length} Warning{warnings.length > 1 ? "s" : ""}
            </Badge>
          )}
          <span className="text-xs text-muted-foreground hidden sm:inline">
            {isValid
              ? "Graph structure and step configurations are valid for publication."
              : "Resolve validation issues before publishing."}
          </span>
        </div>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setIsOpen(!isOpen)}
          className="h-6 text-xs text-muted-foreground hover:text-foreground"
          data-testid="validation-toggle-button"
        >
          {isOpen ? "Hide Details ▲" : "Show Details ▼"}
        </Button>
      </div>

      {isOpen && issues.length > 0 && (
        <div
          className="max-h-36 overflow-y-auto px-4 pb-3 space-y-1.5"
          data-testid="validation-issues-list"
        >
          {issues.map((issue, idx) => (
            <div
              key={idx}
              className={`text-xs p-2 rounded flex items-start justify-between gap-2 ${
                issue.type === "error"
                  ? "bg-destructive/10 text-destructive border border-destructive/20"
                  : "bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20"
              }`}
            >
              <div className="flex items-start gap-2">
                <span className="font-bold uppercase text-[10px] mt-0.5">
                  {issue.type}:
                </span>
                <span>{issue.message}</span>
              </div>
              {issue.nodeId && onSelectNode && (
                <button
                  type="button"
                  onClick={() => onSelectNode(issue.nodeId!)}
                  className="text-[11px] font-medium underline shrink-0 hover:opacity-80"
                  data-testid={`validation-focus-${issue.nodeId}`}
                >
                  View Step
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
