import * as React from "react";
import { Badge } from "@kortex/design-system";
import type { WorkflowGraph } from "../../types";

export interface NodePosition {
  x: number;
  y: number;
}

export interface WorkflowCanvasProps {
  graph: WorkflowGraph;
  selectedNodeId: string | null;
  zoom: number;
  pan: { x: number; y: number };
  nodePositions: Record<string, NodePosition>;
  onPanChange: (newPan: { x: number; y: number }) => void;
  onZoomChange: (newZoom: number) => void;
  onSelectNode: (nodeId: string | null) => void;
  onMoveNode: (nodeId: string, pos: NodePosition) => void;
  onConnectNodes: (sourceNodeId: string, targetNodeId: string) => { success: boolean; error?: string };
  onDisconnectEdge: (edgeId: string) => void;
  onDeleteNode: (nodeId: string) => void;
  onSetEntryNode: (nodeId: string) => void;
}

const STEP_METADATA_KEY = "kortex.workflow.step";
const NODE_WIDTH = 240;
const NODE_HEIGHT = 80;

export function WorkflowCanvas({
  graph,
  selectedNodeId,
  zoom,
  pan,
  nodePositions,
  onPanChange,
  onZoomChange,
  onSelectNode,
  onMoveNode,
  onConnectNodes,
  onDisconnectEdge,
  onDeleteNode,
  onSetEntryNode,
}: WorkflowCanvasProps): React.JSX.Element {
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Transient drag state (Correction 2)
  const [isPanning, setIsPanning] = React.useState(false);
  const [panStart, setPanStart] = React.useState({ x: 0, y: 0 });

  const [draggingNodeId, setDraggingNodeId] = React.useState<string | null>(null);
  const [dragOffset, setDragOffset] = React.useState({ x: 0, y: 0 });

  // Transient edge drawing state: from output port of source node
  const [connectingSourceId, setConnectingSourceId] = React.useState<string | null>(null);
  const [mousePos, setMousePos] = React.useState<{ x: number; y: number } | null>(null);
  const [connectionError, setConnectionError] = React.useState<string | null>(null);

  // Clear connection error after a delay
  React.useEffect(() => {
    if (!connectionError) return;
    const timer = setTimeout(() => setConnectionError(null), 3500);
    return () => clearTimeout(timer);
  }, [connectionError]);

  // Transform canvas coordinates from screen coordinates
  const screenToCanvas = (screenX: number, screenY: number) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: (screenX - rect.left - pan.x) / zoom,
      y: (screenY - rect.top - pan.y) / zoom,
    };
  };

  // Pan handlers
  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === containerRef.current || (e.target as HTMLElement).tagName === "svg") {
      setIsPanning(true);
      setPanStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
      onSelectNode(null);
      if (connectingSourceId) {
        setConnectingSourceId(null);
        setMousePos(null);
      }
    }
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (isPanning) {
      onPanChange({
        x: e.clientX - panStart.x,
        y: e.clientY - panStart.y,
      });
    } else if (draggingNodeId) {
      const canvasCoords = screenToCanvas(e.clientX, e.clientY);
      onMoveNode(draggingNodeId, {
        x: Math.round(canvasCoords.x - dragOffset.x),
        y: Math.round(canvasCoords.y - dragOffset.y),
      });
    }

    if (connectingSourceId) {
      setMousePos(screenToCanvas(e.clientX, e.clientY));
    }
  };

  const handleMouseUp = () => {
    setIsPanning(false);
    setDraggingNodeId(null);
  };

  // Wheel zoom
  const handleWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const delta = e.deltaY < 0 ? 0.1 : -0.1;
      const newZoom = Math.min(Math.max(zoom + delta, 0.4), 2.0);
      onZoomChange(Math.round(newZoom * 10) / 10);
    }
  };

  // Node Dragging Start
  const handleNodeMouseDown = (e: React.MouseEvent, nodeId: string) => {
    e.stopPropagation();
    onSelectNode(nodeId);
    const canvasCoords = screenToCanvas(e.clientX, e.clientY);
    const currentPos = nodePositions[nodeId] || { x: 100, y: 100 };
    setDraggingNodeId(nodeId);
    setDragOffset({
      x: canvasCoords.x - currentPos.x,
      y: canvasCoords.y - currentPos.y,
    });
  };

  // Start connection from output port
  const handleStartConnection = (e: React.MouseEvent, nodeId: string) => {
    e.stopPropagation();
    setConnectingSourceId(nodeId);
    setMousePos(screenToCanvas(e.clientX, e.clientY));
    setConnectionError(null);
  };

  // Complete connection to input port of target node
  const handleCompleteConnection = (e: React.MouseEvent, targetNodeId: string) => {
    e.stopPropagation();
    if (!connectingSourceId) return;

    if (connectingSourceId === targetNodeId) {
      setConnectionError("Cannot connect a node to itself.");
      setConnectingSourceId(null);
      setMousePos(null);
      return;
    }

    const res = onConnectNodes(connectingSourceId, targetNodeId);
    if (res && !res.success) {
      setConnectionError(res.error || "Could not connect steps.");
    }
    setConnectingSourceId(null);
    setMousePos(null);
  };

  // Global keyboard shortcuts (Delete / Backspace)
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        (e.key === "Delete" || e.key === "Backspace") &&
        selectedNodeId &&
        document.activeElement?.tagName !== "INPUT" &&
        document.activeElement?.tagName !== "TEXTAREA"
      ) {
        onDeleteNode(selectedNodeId);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [selectedNodeId, onDeleteNode]);

  return (
    <div
      ref={containerRef}
      role="region"
      aria-label="Interactive Visual Workflow Canvas"
      className="flex-1 h-full relative overflow-hidden bg-background select-none cursor-default"
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onWheel={handleWheel}
      data-testid="workflow-canvas-viewport"
    >
      {/* Error banner for rejected connection */}
      {connectionError && (
        <div
          className="absolute top-3 left-1/2 -translate-x-1/2 z-30 px-3 py-1.5 rounded-md border border-destructive/50 bg-destructive text-destructive-foreground text-xs font-medium shadow-lg animate-in fade-in slide-in-from-top-2 duration-150 flex items-center gap-2"
          data-testid="canvas-connection-error-banner"
        >
          <span>⚠</span>
          <span>{connectionError}</span>
          <button
            type="button"
            onClick={() => setConnectionError(null)}
            className="ml-2 text-white/80 hover:text-white"
          >
            ✕
          </button>
        </div>
      )}

      {/* SVG Canvas Layer (Edges + Grid) */}
      <svg
        className="w-full h-full absolute inset-0 pointer-events-none"
        data-testid="canvas-svg-layer"
      >
        <defs>
          <pattern
            id="canvas-grid"
            width={24 * zoom}
            height={24 * zoom}
            patternUnits="userSpaceOnUse"
            patternTransform={`translate(${pan.x}, ${pan.y})`}
          >
            <circle cx="1" cy="1" r={1 * zoom} className="fill-border/60" />
          </pattern>
          <marker
            id="arrowhead"
            viewBox="0 0 10 10"
            refX="6"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M 0 1 L 8 5 L 0 9 z" className="fill-primary" />
          </marker>
        </defs>

        {/* Background Grid Pattern */}
        <rect width="100%" height="100%" fill="url(#canvas-grid)" />

        {/* Transformed Content Group */}
        <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
          {/* Render Persistent Edges */}
          {graph.edges.map((edge) => {
            const srcPos = nodePositions[edge.sourceNodeId] || { x: 100, y: 100 };
            const tgtPos = nodePositions[edge.targetNodeId] || { x: 100, y: 250 };

            const x1 = srcPos.x + NODE_WIDTH / 2;
            const y1 = srcPos.y + NODE_HEIGHT;
            const x2 = tgtPos.x + NODE_WIDTH / 2;
            const y2 = tgtPos.y;

            // Smooth cubic bezier curve
            const dy = Math.max(Math.abs(y2 - y1) / 2, 40);
            const pathData = `M ${x1} ${y1} C ${x1} ${y1 + dy}, ${x2} ${y2 - dy}, ${x2} ${y2}`;

            return (
              <g key={edge.edgeId} className="pointer-events-auto group">
                <path
                  d={pathData}
                  fill="none"
                  strokeWidth="3"
                  className="stroke-primary/80 group-hover:stroke-destructive transition-colors cursor-pointer"
                  markerEnd="url(#arrowhead)"
                  onClick={() => onDisconnectEdge(edge.edgeId)}
                  data-testid={`canvas-edge-${edge.edgeId}`}
                />
                {/* Edge Disconnect Button at midpoint */}
                <circle
                  cx={(x1 + x2) / 2}
                  cy={(y1 + y2) / 2}
                  r="7"
                  className="fill-background stroke-border group-hover:fill-destructive group-hover:stroke-destructive cursor-pointer transition-colors"
                  onClick={() => onDisconnectEdge(edge.edgeId)}
                  data-testid={`canvas-edge-delete-${edge.edgeId}`}
                />
              </g>
            );
          })}

          {/* Active drawing connection curve */}
          {connectingSourceId && mousePos && (
            (() => {
              const srcPos = nodePositions[connectingSourceId] || { x: 100, y: 100 };
              const x1 = srcPos.x + NODE_WIDTH / 2;
              const y1 = srcPos.y + NODE_HEIGHT;
              const x2 = mousePos.x;
              const y2 = mousePos.y;
              const dy = Math.max(Math.abs(y2 - y1) / 2, 40);
              const pathData = `M ${x1} ${y1} C ${x1} ${y1 + dy}, ${x2} ${y2 - dy}, ${x2} ${y2}`;
              return (
                <path
                  d={pathData}
                  fill="none"
                  strokeWidth="2.5"
                  strokeDasharray="5,5"
                  className="stroke-primary animate-pulse"
                />
              );
            })()
          )}
        </g>
      </svg>

      {/* HTML Node Layer */}
      <div
        className="absolute inset-0 pointer-events-none origin-top-left"
        style={{
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
        }}
      >
        {graph.nodes.map((node, index) => {
          const pos = nodePositions[node.nodeId] || { x: 100, y: 100 + index * 140 };
          const isSelected = selectedNodeId === node.nodeId;
          const isEntry = graph.entryNodeId === node.nodeId;
          const isApproval = node.nodeType === "approval";
          const stepMeta = (node.metadata?.[STEP_METADATA_KEY] ?? {}) as Record<string, unknown>;
          const label = String(stepMeta.name ?? node.nodeId);

          return (
            <div
              key={node.nodeId}
              id={`node-${node.nodeId}`}
              style={{
                transform: `translate(${pos.x}px, ${pos.y}px)`,
                width: `${NODE_WIDTH}px`,
                height: `${NODE_HEIGHT}px`,
              }}
              onMouseDown={(e) => handleNodeMouseDown(e, node.nodeId)}
              className={`absolute pointer-events-auto rounded-xl border bg-card/95 shadow-md flex flex-col justify-between p-2.5 transition-shadow cursor-grab active:cursor-grabbing ${
                isSelected
                  ? "border-primary ring-2 ring-primary/30 shadow-lg"
                  : isApproval
                    ? "border-amber-500/40 hover:border-amber-500/80"
                    : "border-border hover:border-foreground/40"
              }`}
              data-testid={`canvas-node-${node.nodeId}`}
            >
              {/* Input Port (Top Handle) */}
              <div
                className="absolute -top-2.5 left-1/2 -translate-x-1/2 w-5 h-5 rounded-full border-2 border-background bg-muted-foreground/60 hover:bg-primary hover:scale-125 transition-all cursor-crosshair flex items-center justify-center"
                title="Input Connection Port"
                onClick={(e) => handleCompleteConnection(e, node.nodeId)}
                data-testid={`canvas-node-input-${node.nodeId}`}
              >
                <div className="w-1.5 h-1.5 rounded-full bg-background" />
              </div>

              {/* Node Header */}
              <div className="flex items-center justify-between gap-1.5">
                <div className="flex items-center gap-1.5 min-w-0">
                  {isEntry && (
                    <Badge variant="default" className="text-[9px] px-1 py-0 bg-emerald-600 hover:bg-emerald-600 shrink-0">
                      START
                    </Badge>
                  )}
                  {isApproval && (
                    <Badge variant="outline" className="text-[9px] px-1 py-0 border-amber-500/40 text-amber-500 shrink-0">
                      GATE
                    </Badge>
                  )}
                  <span className="text-xs font-semibold text-foreground truncate" title={label}>
                    {label}
                  </span>
                </div>
                <div className="flex items-center gap-1">
                  {!isEntry && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onSetEntryNode(node.nodeId);
                      }}
                      className="text-[9px] text-muted-foreground hover:text-foreground px-1 py-0.5 rounded border border-border/50 hover:bg-muted/50 transition-colors"
                      title="Set as entry step"
                      data-testid={`canvas-node-set-entry-${node.nodeId}`}
                    >
                      Make Start
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteNode(node.nodeId);
                    }}
                    className="text-muted-foreground hover:text-destructive text-xs h-5 w-5 rounded flex items-center justify-center transition-colors"
                    title="Delete Step"
                    data-testid={`canvas-node-delete-${node.nodeId}`}
                  >
                    ✕
                  </button>
                </div>
              </div>

              {/* Node Body / Capability Info */}
              <div className="text-[10px] text-muted-foreground font-mono truncate">
                {isApproval ? "Human Decision Wait Gate" : node.capabilityName ?? "(no capability)"}
              </div>

              {/* Output Port (Bottom Handle) */}
              <div
                className={`absolute -bottom-2.5 left-1/2 -translate-x-1/2 w-5 h-5 rounded-full border-2 border-background flex items-center justify-center cursor-crosshair transition-all ${
                  connectingSourceId === node.nodeId
                    ? "bg-primary scale-125 ring-2 ring-primary/40"
                    : "bg-primary/80 hover:bg-primary hover:scale-125"
                }`}
                title="Connect next step (Single output)"
                onClick={(e) => handleStartConnection(e, node.nodeId)}
                data-testid={`canvas-node-output-${node.nodeId}`}
              >
                <div className="w-1.5 h-1.5 rounded-full bg-background" />
              </div>
            </div>
          );
        })}
      </div>

      {/* Empty State Banner */}
      {graph.nodes.length === 0 && (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-center p-6 pointer-events-none">
          <div className="w-12 h-12 rounded-full border border-border bg-muted/30 flex items-center justify-center text-muted-foreground mb-3">
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 4v16m8-8H4" />
            </svg>
          </div>
          <h4 className="text-sm font-semibold text-foreground">Canvas is empty</h4>
          <p className="text-xs text-muted-foreground mt-1 max-w-sm">
            Select a capability or Approval Gate from the left palette to add your first workflow step.
          </p>
        </div>
      )}
    </div>
  );
}
