import * as React from "react";
import { useSearchParams } from "react-router-dom";
import {
  createWorkflowDraft,
  getWorkflowDefinitionDraft,
  publishWorkflowDraft,
  updateWorkflowDraft,
  validateWorkflowDraft,
} from "../../api";
import type {
  ProjectedCapability,
  WorkflowGraph,
  WorkflowGraphEdge,
  WorkflowGraphNode,
  WorkflowValidationReport,
} from "../../types";
import { CapabilityPalette } from "./CapabilityPalette";
import { WorkflowCanvas, type NodePosition } from "./WorkflowCanvas";
import { NodeInspector } from "./NodeInspector";
import { BuilderToolbar } from "./BuilderToolbar";
import { ValidationPanel, type ValidationIssue } from "./ValidationPanel";
import { PublishReviewDialog } from "./PublishReviewDialog";

const STEP_METADATA_KEY = "kortex.workflow.step";

export interface WorkflowBuilderTabProps {
  initialDefinitionId?: string;
}

export function WorkflowBuilderTab({
  initialDefinitionId,
}: WorkflowBuilderTabProps): React.JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const definitionIdFromParam = searchParams.get("definitionId") ?? initialDefinitionId ?? null;

  // Persistent Draft State (Milestone F4)
  const [definitionId, setDefinitionId] = React.useState<string | null>(definitionIdFromParam);
  const [lockVersion, setLockVersion] = React.useState<number>(1);
  const [workflowName, setWorkflowName] = React.useState<string>("Untitled Workflow");
  const [workflowDescription, setWorkflowDescription] = React.useState<string>("");
  const [status, setStatus] = React.useState<string>("DRAFT");

  // Canonical Workflow Graph (Milestone F2 & F3)
  const [graph, setGraph] = React.useState<WorkflowGraph>({
    schemaVersion: "1.0.0",
    entryNodeId: "",
    nodes: [],
    edges: [],
  });

  // Transient Editor UI State (Correction 2: NOT persisted as workflow semantics)
  const [nodePositions, setNodePositions] = React.useState<Record<string, NodePosition>>({});
  const [selectedNodeId, setSelectedNodeId] = React.useState<string | null>(null);
  const [zoom, setZoom] = React.useState<number>(1.0);
  const [pan, setPan] = React.useState<{ x: number; y: number }>({ x: 40, y: 40 });
  const [isDirty, setIsDirty] = React.useState<boolean>(false);

  // Undo / Redo Local History (Transient)
  const [history, setHistory] = React.useState<WorkflowGraph[]>([]);
  const [future, setFuture] = React.useState<WorkflowGraph[]>([]);

  // Async States
  const [isLoadingDraft, setIsLoadingDraft] = React.useState<boolean>(false);
  const [isSaving, setIsSaving] = React.useState<boolean>(false);
  const [isValidating, setIsValidating] = React.useState<boolean>(false);
  const [isPublishing, setIsPublishing] = React.useState<boolean>(false);
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const [conflictError, setConflictError] = React.useState<string | null>(null);

  // Dialog & Validation States
  const [validationReport, setValidationReport] = React.useState<WorkflowValidationReport | null>(null);
  const [isReviewOpen, setIsReviewOpen] = React.useState<boolean>(false);

  // Load existing draft if definitionId is set
  React.useEffect(() => {
    if (!definitionId) return;

    let isMounted = true;
    setIsLoadingDraft(true);
    setSaveError(null);
    setConflictError(null);

    getWorkflowDefinitionDraft(definitionId)
      .then((draft) => {
        if (!isMounted || !draft) return;
        setWorkflowName(draft.name || "Untitled Workflow");
        setWorkflowDescription(draft.description || "");
        setStatus(draft.status || "DRAFT");
        setLockVersion(draft.lockVersion);

        if (draft.graph) {
          setGraph(draft.graph);
          // Calculate initial positions top-down
          const initialPos: Record<string, NodePosition> = {};
          draft.graph.nodes.forEach((n, i) => {
            initialPos[n.nodeId] = { x: 180, y: 60 + i * 140 };
          });
          setNodePositions(initialPos);
        } else if (draft.steps && draft.steps.length > 0) {
          // Backward compatibility: project flat steps into linear graph
          const nodes: WorkflowGraphNode[] = draft.steps.map((s) => ({
            nodeId: s.id,
            nodeType: s.isApprovalStep ? "approval" : "capability",
            capabilityName: s.capabilityName,
            config: {},
            inputPorts: ["input"],
            outputPorts: ["output"],
            metadata: {
              [STEP_METADATA_KEY]: {
                name: s.name,
                is_approval_step: s.isApprovalStep,
              },
            },
          }));
          const edges: WorkflowGraphEdge[] = [];
          for (let i = 0; i < nodes.length - 1; i++) {
            edges.push({
              edgeId: `edge_${nodes[i].nodeId}_${nodes[i + 1].nodeId}`,
              sourceNodeId: nodes[i].nodeId,
              targetNodeId: nodes[i + 1].nodeId,
              sourcePort: "output",
              targetPort: "input",
            });
          }
          const generatedGraph: WorkflowGraph = {
            schemaVersion: "1.0.0",
            entryNodeId: nodes[0].nodeId,
            nodes,
            edges,
          };
          setGraph(generatedGraph);
          const initialPos: Record<string, NodePosition> = {};
          nodes.forEach((n, i) => {
            initialPos[n.nodeId] = { x: 180, y: 60 + i * 140 };
          });
          setNodePositions(initialPos);
        }
        setIsDirty(false);
        setIsLoadingDraft(false);
      })
      .catch((err: unknown) => {
        if (!isMounted) return;
        setSaveError(err instanceof Error ? err.message : "Failed to load workflow draft.");
        setIsLoadingDraft(false);
      });

    return () => {
      isMounted = false;
    };
  }, [definitionId]);

  // Helper to commit graph changes with undo history
  const commitGraph = (newGraph: WorkflowGraph) => {
    setHistory((prev) => [...prev.slice(-30), graph]);
    setFuture([]);
    setGraph(newGraph);
    setIsDirty(true);
  };

  const handleUndo = () => {
    if (history.length === 0) return;
    const prev = history[history.length - 1];
    setHistory((h) => h.slice(0, -1));
    setFuture((f) => [graph, ...f]);
    setGraph(prev);
    setIsDirty(true);
  };

  const handleRedo = () => {
    if (future.length === 0) return;
    const next = future[0];
    setFuture((f) => f.slice(1));
    setHistory((h) => [...h, graph]);
    setGraph(next);
    setIsDirty(true);
  };

  // Add capability node
  const handleAddCapabilityNode = (cap: ProjectedCapability) => {
    const stepCount = graph.nodes.length + 1;
    const nodeId = `step_${cap.name.split(".").pop()}_${stepCount}`;
    const newNode: WorkflowGraphNode = {
      nodeId,
      nodeType: "capability",
      capabilityName: cap.name,
      config: {},
      inputPorts: ["input"],
      outputPorts: ["output"],
      metadata: {
        [STEP_METADATA_KEY]: {
          name: `${cap.name.split(".").slice(-2).join(".")} Step`,
          is_approval_step: false,
        },
      },
    };

    // Auto connect to current terminal node if available to maintain linearity
    const newNodes = [...graph.nodes, newNode];
    const newEdges = [...graph.edges];
    let newEntry = graph.entryNodeId;

    if (graph.nodes.length === 0) {
      newEntry = nodeId;
    } else {
      // Find current terminal node (node with out-degree 0)
      const outgoing = new Set(graph.edges.map((e) => e.sourceNodeId));
      const terminal = graph.nodes.find((n) => !outgoing.has(n.nodeId));
      if (terminal) {
        newEdges.push({
          edgeId: `edge_${terminal.nodeId}_${nodeId}`,
          sourceNodeId: terminal.nodeId,
          targetNodeId: nodeId,
          sourcePort: "output",
          targetPort: "input",
        });
      }
    }

    setNodePositions((pos) => ({
      ...pos,
      [nodeId]: { x: 180, y: 60 + graph.nodes.length * 140 },
    }));

    commitGraph({
      ...graph,
      entryNodeId: newEntry,
      nodes: newNodes,
      edges: newEdges,
    });
    setSelectedNodeId(nodeId);
  };

  // Add Approval Gate (Correction 1: Pure wait gate, capability_name = null)
  const handleAddApprovalNode = () => {
    const stepCount = graph.nodes.length + 1;
    const nodeId = `approval_gate_${stepCount}`;
    const newNode: WorkflowGraphNode = {
      nodeId,
      nodeType: "approval",
      capabilityName: null,
      config: {},
      inputPorts: ["input"],
      outputPorts: ["output"],
      metadata: {
        [STEP_METADATA_KEY]: {
          name: `Approval Gate ${stepCount}`,
          is_approval_step: true,
          required_approval_role: "operator",
        },
      },
    };

    const newNodes = [...graph.nodes, newNode];
    const newEdges = [...graph.edges];
    let newEntry = graph.entryNodeId;

    if (graph.nodes.length === 0) {
      newEntry = nodeId;
    } else {
      const outgoing = new Set(graph.edges.map((e) => e.sourceNodeId));
      const terminal = graph.nodes.find((n) => !outgoing.has(n.nodeId));
      if (terminal) {
        newEdges.push({
          edgeId: `edge_${terminal.nodeId}_${nodeId}`,
          sourceNodeId: terminal.nodeId,
          targetNodeId: nodeId,
          sourcePort: "output",
          targetPort: "input",
        });
      }
    }

    setNodePositions((pos) => ({
      ...pos,
      [nodeId]: { x: 180, y: 60 + graph.nodes.length * 140 },
    }));

    commitGraph({
      ...graph,
      entryNodeId: newEntry,
      nodes: newNodes,
      edges: newEdges,
    });
    setSelectedNodeId(nodeId);
  };

  // Update Node
  const handleUpdateNode = (updatedNode: WorkflowGraphNode) => {
    const newNodes = graph.nodes.map((n) => (n.nodeId === updatedNode.nodeId ? updatedNode : n));
    commitGraph({ ...graph, nodes: newNodes });
  };

  // Delete Node
  const handleDeleteNode = (nodeId: string) => {
    const newNodes = graph.nodes.filter((n) => n.nodeId !== nodeId);
    const newEdges = graph.edges.filter(
      (e) => e.sourceNodeId !== nodeId && e.targetNodeId !== nodeId
    );
    let newEntry = graph.entryNodeId;
    if (newEntry === nodeId) {
      newEntry = newNodes.length > 0 ? newNodes[0].nodeId : "";
    }

    if (selectedNodeId === nodeId) {
      setSelectedNodeId(null);
    }

    commitGraph({
      ...graph,
      entryNodeId: newEntry,
      nodes: newNodes,
      edges: newEdges,
    });
  };

  // Connect Nodes with Strict Linearity V1 (Correction 5)
  const handleConnectNodes = (
    sourceNodeId: string,
    targetNodeId: string
  ): { success: boolean; error?: string } => {
    if (sourceNodeId === targetNodeId) {
      return { success: false, error: "Self-loop rejected: a step cannot connect to itself." };
    }

    // Single successor check (no fan-out)
    const existingOut = graph.edges.find((e) => e.sourceNodeId === sourceNodeId);
    if (existingOut) {
      return {
        success: false,
        error: "Linearity violation: source step already has an outgoing connection (fan-out is forbidden in v1).",
      };
    }

    // Single predecessor check (no joins)
    const existingIn = graph.edges.find((e) => e.targetNodeId === targetNodeId);
    if (existingIn) {
      return {
        success: false,
        error: "Linearity violation: target step already has an incoming connection (joins are forbidden in v1).",
      };
    }

    // Cycle check: verify targetNodeId cannot reach sourceNodeId
    const outgoingMap = new Map<string, string>();
    for (const e of graph.edges) {
      outgoingMap.set(e.sourceNodeId, e.targetNodeId);
    }
    outgoingMap.set(sourceNodeId, targetNodeId);

    // Trace from target to see if we reach source
    let curr: string | undefined = targetNodeId;
    const visited = new Set<string>();
    while (curr && outgoingMap.has(curr)) {
      if (visited.has(curr)) break;
      visited.add(curr);
      curr = outgoingMap.get(curr);
      if (curr === sourceNodeId) {
        return { success: false, error: "Cycle rejected: this connection would create a closed loop." };
      }
    }

    const edgeId = `edge_${sourceNodeId}_${targetNodeId}`;
    const newEdges = [
      ...graph.edges,
      {
        edgeId,
        sourceNodeId,
        targetNodeId,
        sourcePort: "output",
        targetPort: "input",
      },
    ];

    // Determine entry node if not set or if target was previously entry
    let newEntry = graph.entryNodeId;
    if (!newEntry || newEntry === targetNodeId) {
      newEntry = sourceNodeId;
    }

    commitGraph({
      ...graph,
      entryNodeId: newEntry,
      edges: newEdges,
    });
    return { success: true };
  };

  // Disconnect Edge
  const handleDisconnectEdge = (edgeId: string) => {
    const newEdges = graph.edges.filter((e) => e.edgeId !== edgeId);
    commitGraph({ ...graph, edges: newEdges });
  };

  // Move Node (Transient UI state)
  const handleMoveNode = (nodeId: string, pos: NodePosition) => {
    setNodePositions((prev) => ({ ...prev, [nodeId]: pos }));
  };

  // Auto-layout nodes in clean top-down sequence
  const handleAutoLayout = () => {
    const outgoing = new Map(graph.edges.map((e) => [e.sourceNodeId, e.targetNodeId]));
    const newPos: Record<string, NodePosition> = {};
    let curr: string | undefined = graph.entryNodeId;
    let idx = 0;
    const visited = new Set<string>();

    while (curr && !visited.has(curr)) {
      visited.add(curr);
      newPos[curr] = { x: 180, y: 60 + idx * 140 };
      idx++;
      curr = outgoing.get(curr);
    }

    // Place any remaining disconnected nodes
    for (const node of graph.nodes) {
      if (!visited.has(node.nodeId)) {
        newPos[node.nodeId] = { x: 180, y: 60 + idx * 140 };
        idx++;
      }
    }

    setNodePositions(newPos);
  };

  // Compute live validation issues
  const validationIssues = React.useMemo<ValidationIssue[]>(() => {
    const issues: ValidationIssue[] = [];

    if (graph.nodes.length === 0) {
      issues.push({ type: "warning", message: "Workflow contains no steps." });
      return issues;
    }

    // Check entry node
    if (!graph.entryNodeId || !graph.nodes.some((n) => n.nodeId === graph.entryNodeId)) {
      issues.push({ type: "error", message: "Workflow has no valid start entry step." });
    }

    // Check approval invariant (Correction 1)
    for (const node of graph.nodes) {
      const stepMeta = (node.metadata?.[STEP_METADATA_KEY] ?? {}) as Record<string, unknown>;
      const isApproval =
        node.nodeType === "approval" ||
        Boolean(stepMeta.is_approval_step);
      if (isApproval && node.capabilityName !== null) {
        issues.push({
          type: "error",
          nodeId: node.nodeId,
          message: `Approval step '${node.nodeId}' cannot declare a capability (approval steps are pure wait gates).`,
        });
      }
    }

    // Linearity & Reachability
    const inDegree: Record<string, number> = {};
    const outDegree: Record<string, number> = {};
    for (const n of graph.nodes) {
      inDegree[n.nodeId] = 0;
      outDegree[n.nodeId] = 0;
    }
    for (const e of graph.edges) {
      outDegree[e.sourceNodeId] = (outDegree[e.sourceNodeId] || 0) + 1;
      inDegree[e.targetNodeId] = (inDegree[e.targetNodeId] || 0) + 1;
    }

    for (const [id, count] of Object.entries(outDegree)) {
      if (count > 1) {
        issues.push({
          type: "error",
          nodeId: id,
          message: `Step '${id}' branches to multiple steps (branching is not allowed in linear v1).`,
        });
      }
    }
    for (const [id, count] of Object.entries(inDegree)) {
      if (count > 1) {
        issues.push({
          type: "error",
          nodeId: id,
          message: `Step '${id}' has multiple incoming connections (joins are not allowed in linear v1).`,
        });
      }
    }

    // Disconnected nodes
    if (graph.nodes.length > 1 && graph.entryNodeId) {
      const visited = new Set<string>();
      let curr: string | undefined = graph.entryNodeId;
      const outgoingMap = new Map(graph.edges.map((e) => [e.sourceNodeId, e.targetNodeId]));
      while (curr && !visited.has(curr)) {
        visited.add(curr);
        curr = outgoingMap.get(curr);
      }
      for (const node of graph.nodes) {
        if (!visited.has(node.nodeId)) {
          issues.push({
            type: "error",
            nodeId: node.nodeId,
            message: `Step '${node.nodeId}' is disconnected from the main linear execution chain.`,
          });
        }
      }
    }

    return issues;
  }, [graph]);

  const hasErrors = validationIssues.some((i) => i.type === "error");

  // Save Draft (Section 20 & 21: Explicit draft save, optimistic locking)
  const handleSaveDraft = async () => {
    setIsSaving(true);
    setSaveError(null);
    setConflictError(null);

    try {
      if (!definitionId) {
        // Create new draft
        const created = await createWorkflowDraft({
          name: workflowName,
          description: workflowDescription,
          graph,
        });
        setDefinitionId(created.definitionId);
        setLockVersion(created.lockVersion);
        setStatus(created.status);
        setIsDirty(false);
        setSearchParams({ tab: "builder", definitionId: created.definitionId });
      } else {
        // Update existing draft with optimistic lock
        const updated = await updateWorkflowDraft({
          definitionId,
          expectedLockVersion: lockVersion,
          name: workflowName,
          description: workflowDescription,
          graph,
        });
        setLockVersion(updated.lockVersion);
        setStatus(updated.status);
        setIsDirty(false);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to save workflow draft.";
      if (msg.includes("modified concurrently") || msg.includes("lock_version")) {
        setConflictError(
          "Version conflict: This draft was modified elsewhere. Please refresh to load the latest changes."
        );
      } else {
        setSaveError(msg);
      }
    } finally {
      setIsSaving(false);
    }
  };

  // Run Authoritative Backend Validation
  const handleValidate = async () => {
    if (!definitionId || isDirty) {
      // Save draft first if dirty or unsaved
      await handleSaveDraft();
    }
    if (!definitionId) return;

    setIsValidating(true);
    try {
      const report = await validateWorkflowDraft(definitionId);
      setValidationReport(report);
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : "Validation failed.");
    } finally {
      setIsValidating(false);
    }
  };

  // Open Review & Publish Dialog
  const handleReviewAndPublish = async () => {
    if (isDirty || !definitionId) {
      await handleSaveDraft();
    }
    if (!definitionId) return;

    // Run fresh validation
    setIsValidating(true);
    try {
      const report = await validateWorkflowDraft(definitionId);
      setValidationReport(report);
      setIsReviewOpen(true);
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : "Pre-publish validation failed.");
    } finally {
      setIsValidating(false);
    }
  };

  // Confirm Publication (Human-only authority, Section 22)
  const handleConfirmPublish = async () => {
    if (!definitionId) return;

    setIsPublishing(true);
    try {
      const published = await publishWorkflowDraft(definitionId, lockVersion);
      setStatus("PUBLISHED");
      setLockVersion(published.lockVersion);
      setIsReviewOpen(false);
      setIsDirty(false);
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : "Publication failed.");
    } finally {
      setIsPublishing(false);
    }
  };

  // Compute topological ancestors for selected node
  const selectedNode = graph.nodes.find((n) => n.nodeId === selectedNodeId) || null;
  const ancestors = React.useMemo(() => {
    if (!selectedNodeId) return [];
    // Trace back through incoming edges
    const incoming = new Map(graph.edges.map((e) => [e.targetNodeId, e.sourceNodeId]));
    const result: { nodeId: string; name: string }[] = [];
    let curr: string | undefined = incoming.get(selectedNodeId);
    const visited = new Set<string>();

    while (curr && !visited.has(curr)) {
      visited.add(curr);
      const node = graph.nodes.find((n) => n.nodeId === curr);
      if (node) {
        const stepMeta = (node.metadata?.[STEP_METADATA_KEY] ?? {}) as Record<string, unknown>;
        result.push({
          nodeId: node.nodeId,
          name: String(stepMeta.name ?? node.nodeId),
        });
      }
      curr = incoming.get(curr);
    }
    return result;
  }, [graph, selectedNodeId]);

  return (
    <div
      className="flex flex-col h-[calc(100vh-8.5rem)] border border-border rounded-lg overflow-hidden bg-background"
      data-testid="workflow-builder-tab"
    >
      {/* Top Toolbar */}
      <BuilderToolbar
        name={workflowName}
        description={workflowDescription}
        status={status}
        isDirty={isDirty}
        isSaving={isSaving}
        isValidating={isValidating}
        canPublish={!hasErrors && graph.nodes.length > 0}
        canUndo={history.length > 0}
        canRedo={future.length > 0}
        zoom={zoom}
        onNameChange={(n) => {
          setWorkflowName(n);
          setIsDirty(true);
        }}
        onDescriptionChange={(d) => {
          setWorkflowDescription(d);
          setIsDirty(true);
        }}
        onSaveDraft={handleSaveDraft}
        onValidate={handleValidate}
        onReviewAndPublish={handleReviewAndPublish}
        onZoomIn={() => setZoom((z) => Math.min(Math.round((z + 0.1) * 10) / 10, 2.0))}
        onZoomOut={() => setZoom((z) => Math.max(Math.round((z - 0.1) * 10) / 10, 0.4))}
        onResetZoom={() => {
          setZoom(1.0);
          setPan({ x: 40, y: 40 });
        }}
        onAutoLayout={handleAutoLayout}
        onUndo={handleUndo}
        onRedo={handleRedo}
      />

      {/* Error & Conflict Banners */}
      {conflictError && (
        <div
          className="p-3 bg-destructive/15 border-b border-destructive/30 text-xs text-destructive flex items-center justify-between"
          data-testid="builder-conflict-banner"
        >
          <span>{conflictError}</span>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="underline font-semibold ml-2 hover:opacity-80"
          >
            Reload
          </button>
        </div>
      )}
      {saveError && (
        <div
          className="p-3 bg-destructive/10 border-b border-destructive/30 text-xs text-destructive flex items-center justify-between"
          data-testid="builder-save-error-banner"
        >
          <span>{saveError}</span>
          <button
            type="button"
            onClick={() => setSaveError(null)}
            className="text-xs hover:opacity-80"
          >
            ✕
          </button>
        </div>
      )}

      {/* Loading Overlay */}
      {isLoadingDraft && (
        <div
          className="p-2 bg-muted/60 text-xs text-muted-foreground border-b border-border animate-pulse text-center"
          data-testid="builder-loading-state"
        >
          Loading workflow draft...
        </div>
      )}

      {/* Middle: Palette + Canvas + Inspector */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left Palette */}
        <CapabilityPalette
          onAddCapabilityNode={handleAddCapabilityNode}
          onAddApprovalNode={handleAddApprovalNode}
          disabled={isSaving || isPublishing}
        />

        {/* Center Canvas */}
        <WorkflowCanvas
          graph={graph}
          selectedNodeId={selectedNodeId}
          zoom={zoom}
          pan={pan}
          nodePositions={nodePositions}
          onPanChange={setPan}
          onZoomChange={setZoom}
          onSelectNode={setSelectedNodeId}
          onMoveNode={handleMoveNode}
          onConnectNodes={handleConnectNodes}
          onDisconnectEdge={handleDisconnectEdge}
          onDeleteNode={handleDeleteNode}
          onSetEntryNode={(nodeId) => commitGraph({ ...graph, entryNodeId: nodeId })}
        />

        {/* Right Node Inspector */}
        <NodeInspector
          node={selectedNode}
          capability={
            selectedNode?.capabilityName
              ? {
                  name: selectedNode.capabilityName,
                  description: "",
                  provider: "",
                  parametersSchema: {},
                }
              : null
          }
          ancestors={ancestors}
          onUpdateNode={handleUpdateNode}
          onDeleteNode={handleDeleteNode}
          onClose={() => setSelectedNodeId(null)}
        />
      </div>

      {/* Bottom Live Validation Bar */}
      <ValidationPanel
        issues={validationIssues}
        onSelectNode={setSelectedNodeId}
      />

      {/* Human Publish Review Dialog */}
      <PublishReviewDialog
        isOpen={isReviewOpen}
        name={workflowName}
        description={workflowDescription}
        graph={graph}
        validationReport={validationReport}
        isPublishing={isPublishing}
        onConfirmPublish={handleConfirmPublish}
        onClose={() => setIsReviewOpen(false)}
      />
    </div>
  );
}
