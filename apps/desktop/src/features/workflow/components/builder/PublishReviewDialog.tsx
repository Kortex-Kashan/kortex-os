import * as React from "react";
import { Badge, Button } from "@kortex/design-system";
import type { WorkflowGraph, WorkflowValidationReport } from "../../types";

export interface PublishReviewDialogProps {
  isOpen: boolean;
  name: string;
  description: string;
  graph: WorkflowGraph;
  validationReport: WorkflowValidationReport | null;
  isPublishing: boolean;
  onConfirmPublish: () => void;
  onClose: () => void;
}

const STEP_METADATA_KEY = "kortex.workflow.step";

export function PublishReviewDialog({
  isOpen,
  name,
  description,
  graph,
  validationReport,
  isPublishing,
  onConfirmPublish,
  onClose,
}: PublishReviewDialogProps): React.JSX.Element | null {
  if (!isOpen) return null;

  // Build ordered chain from graph
  const nodesById = new Map(graph.nodes.map((n) => [n.nodeId, n]));
  const outgoing = new Map(graph.edges.map((e) => [e.sourceNodeId, e.targetNodeId]));

  const orderedNodes = [];
  let curr: string | undefined = graph.entryNodeId;
  const visited = new Set<string>();
  while (curr && nodesById.has(curr) && !visited.has(curr)) {
    visited.add(curr);
    orderedNodes.push(nodesById.get(curr)!);
    curr = outgoing.get(curr);
  }
  // Include any remaining nodes if graph wasn't fully chained
  for (const node of graph.nodes) {
    if (!visited.has(node.nodeId)) {
      orderedNodes.push(node);
    }
  }

  const errors = validationReport?.errors ?? [];
  const warnings = validationReport?.warnings ?? [];
  const hasErrors = errors.length > 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      data-testid="publish-review-dialog"
    >
      <div
        className="w-full max-w-lg rounded-xl border border-border bg-card shadow-2xl overflow-hidden flex flex-col max-h-[85vh] animate-in fade-in zoom-in-95 duration-150"
        role="dialog"
        aria-modal="true"
        aria-labelledby="publish-dialog-title"
      >
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div>
            <h2 id="publish-dialog-title" className="text-base font-semibold text-foreground">
              Review &amp; Publish Workflow
            </h2>
            <p className="text-xs text-muted-foreground">
              Publishing generates an immutable version that can be triggered in production.
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            disabled={isPublishing}
            className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
          >
            ✕
          </Button>
        </div>

        <div className="p-4 overflow-y-auto space-y-4 flex-1">
          {/* Summary Details */}
          <div className="rounded-lg border border-border bg-muted/30 p-3 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-foreground">{name || "(Untitled)"}</span>
              <Badge variant="outline" className="text-[10px] uppercase">
                {orderedNodes.length} Step{orderedNodes.length > 1 ? "s" : ""}
              </Badge>
            </div>
            {description && (
              <p className="text-xs text-muted-foreground">{description}</p>
            )}
          </div>

          {/* Validation Status */}
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-foreground">Validation Check</span>
            {hasErrors ? (
              <div className="p-3 rounded-lg border border-destructive/40 bg-destructive/10 text-xs text-destructive space-y-1">
                <div className="font-semibold flex items-center gap-1.5">
                  <span>⚠</span> Cannot publish: resolve the following errors first
                </div>
                <ul className="list-disc list-inside space-y-0.5 text-[11px]">
                  {errors.map((err, i) => (
                    <li key={i}>{err}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="p-2.5 rounded-lg border border-green-500/30 bg-green-500/10 text-xs text-green-700 dark:text-green-400 flex items-center gap-2">
                <span>✓</span> Workflow passed structural and capability validation.
              </div>
            )}

            {warnings.length > 0 && (
              <div className="p-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 text-xs text-amber-600 dark:text-amber-400 space-y-1">
                <div className="font-semibold">Warnings:</div>
                <ul className="list-disc list-inside space-y-0.5 text-[11px]">
                  {warnings.map((warn, i) => (
                    <li key={i}>{warn}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {/* Step Sequence */}
          <div className="space-y-2">
            <span className="text-xs font-medium text-foreground">Execution Steps Order</span>
            <ol className="space-y-2">
              {orderedNodes.map((node, index) => {
                const isApproval = node.nodeType === "approval";
                const stepMeta = (node.metadata?.[STEP_METADATA_KEY] ?? {}) as Record<string, unknown>;
                const label = String(stepMeta.name ?? node.nodeId);

                return (
                  <li
                    key={node.nodeId}
                    className="flex items-center justify-between p-2.5 rounded border border-border bg-background text-xs"
                    data-testid="publish-review-step-row"
                  >
                    <div className="flex items-center gap-2.5">
                      <span className="w-5 h-5 rounded-full bg-muted flex items-center justify-center font-bold text-[10px] text-muted-foreground">
                        {index + 1}
                      </span>
                      <div>
                        <div className="font-medium text-foreground">{label}</div>
                        <div className="text-[10px] text-muted-foreground font-mono">
                          {isApproval ? "Human Approval Gate" : node.capabilityName}
                        </div>
                      </div>
                    </div>
                    <Badge
                      variant={isApproval ? "outline" : "secondary"}
                      className={`text-[9px] px-1.5 py-0 ${isApproval ? "border-amber-500/40 text-amber-500" : ""}`}
                    >
                      {isApproval ? "Wait Gate" : "Automated"}
                    </Badge>
                  </li>
                );
              })}
            </ol>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="p-3 border-t border-border bg-muted/20 flex items-center justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onClose}
            disabled={isPublishing}
            className="text-xs h-8"
          >
            Cancel
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={onConfirmPublish}
            disabled={hasErrors || isPublishing || orderedNodes.length === 0}
            className="text-xs h-8 bg-primary text-primary-foreground font-medium"
            data-testid="publish-confirm-button"
          >
            {isPublishing ? "Publishing..." : "Confirm & Publish"}
          </Button>
        </div>
      </div>
    </div>
  );
}
