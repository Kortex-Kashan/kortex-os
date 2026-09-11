import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WorkflowGraph } from "../../types";
import { WorkflowCanvas } from "./WorkflowCanvas";

describe("WorkflowCanvas", () => {
  const sampleGraph: WorkflowGraph = {
    entryNodeId: "node-1",
    nodes: [
      {
        nodeId: "node-1",
        nodeType: "capability",
        capabilityName: "invoice.get",
        config: {},
        metadata: {
          "kortex.workflow.step": { name: "Get Invoice" },
        },
      },
      {
        nodeId: "node-2",
        nodeType: "approval",
        capabilityName: null,
        config: { requiredRole: "MANAGER" },
        metadata: {
          "kortex.workflow.step": { name: "Manager Approval Gate" },
        },
      },
      {
        nodeId: "node-3",
        nodeType: "capability",
        capabilityName: "webhook.send",
        config: {},
        metadata: {
          "kortex.workflow.step": { name: "Send Notification" },
        },
      },
    ],
    edges: [
      {
        edgeId: "edge_node-1_node-2",
        sourceNodeId: "node-1",
        targetNodeId: "node-2",
      },
    ],
  };

  const defaultProps = {
    graph: sampleGraph,
    selectedNodeId: "node-1",
    zoom: 1,
    pan: { x: 0, y: 0 },
    nodePositions: {
      "node-1": { x: 100, y: 100 },
      "node-2": { x: 100, y: 250 },
      "node-3": { x: 100, y: 400 },
    },
    onPanChange: vi.fn(),
    onZoomChange: vi.fn(),
    onSelectNode: vi.fn(),
    onMoveNode: vi.fn(),
    onConnectNodes: vi.fn().mockReturnValue({ success: true }),
    onDisconnectEdge: vi.fn(),
    onDeleteNode: vi.fn(),
    onSetEntryNode: vi.fn(),
  };

  it("renders all nodes and labels", () => {
    render(<WorkflowCanvas {...defaultProps} />);

    expect(screen.getByText("Get Invoice")).toBeInTheDocument();
    expect(screen.getByText("Manager Approval Gate")).toBeInTheDocument();
    expect(screen.getByText("Send Notification")).toBeInTheDocument();

    // Badges
    expect(screen.getByText("START")).toBeInTheDocument();
    expect(screen.getByText("GATE")).toBeInTheDocument();
  });

  it("selects node when clicked", () => {
    render(<WorkflowCanvas {...defaultProps} />);

    const node2 = screen.getByTestId("canvas-node-node-2");
    fireEvent.mouseDown(node2);

    expect(defaultProps.onSelectNode).toHaveBeenCalledWith("node-2");
  });

  it("calls onDeleteNode when delete button is clicked", () => {
    render(<WorkflowCanvas {...defaultProps} />);

    const deleteBtn = screen.getByTestId("canvas-node-delete-node-3");
    fireEvent.click(deleteBtn);

    expect(defaultProps.onDeleteNode).toHaveBeenCalledWith("node-3");
  });

  it("calls onSetEntryNode when Make Start button is clicked", () => {
    render(<WorkflowCanvas {...defaultProps} />);

    const setEntryBtn = screen.getByTestId("canvas-node-set-entry-node-3");
    fireEvent.click(setEntryBtn);

    expect(defaultProps.onSetEntryNode).toHaveBeenCalledWith("node-3");
  });

  it("renders connecting edge with delete button and handles disconnection", () => {
    render(<WorkflowCanvas {...defaultProps} />);

    const edgeDeleteBtn = screen.getByTestId("canvas-edge-delete-edge_node-1_node-2");
    expect(edgeDeleteBtn).toBeInTheDocument();

    fireEvent.click(edgeDeleteBtn);
    expect(defaultProps.onDisconnectEdge).toHaveBeenCalledWith("edge_node-1_node-2");
  });

  it("connects two unconnected nodes linearly (node-2 -> node-3)", () => {
    render(<WorkflowCanvas {...defaultProps} />);

    // Click node-2 output port
    const outputPort = screen.getByTestId("canvas-node-output-node-2");
    fireEvent.click(outputPort);

    // Click node-3 input port
    const inputPort = screen.getByTestId("canvas-node-input-node-3");
    fireEvent.click(inputPort);

    expect(defaultProps.onConnectNodes).toHaveBeenCalledWith("node-2", "node-3");
  });

  it("enforces linearity by rejecting connection when target already has an incoming edge", () => {
    defaultProps.onConnectNodes.mockReturnValueOnce({
      success: false,
      error: "Linearity violation: Target node already has an incoming connection",
    });
    render(<WorkflowCanvas {...defaultProps} />);

    // Try to connect node-3 -> node-2 (node-2 already has input from node-1)
    const outputPort = screen.getByTestId("canvas-node-output-node-3");
    fireEvent.click(outputPort);

    const inputPort = screen.getByTestId("canvas-node-input-node-2");
    fireEvent.click(inputPort);

    expect(defaultProps.onConnectNodes).toHaveBeenCalledWith("node-3", "node-2");
    expect(screen.getByText(/Linearity violation: Target node already has an incoming connection/i)).toBeInTheDocument();
  });

  it("enforces linearity by rejecting connection when source already has an outgoing edge", () => {
    defaultProps.onConnectNodes.mockReturnValueOnce({
      success: false,
      error: "Linearity violation: Source node already has a successor",
    });
    render(<WorkflowCanvas {...defaultProps} />);

    // Try to connect node-1 -> node-3 (node-1 already connects to node-2)
    const outputPort = screen.getByTestId("canvas-node-output-node-1");
    fireEvent.click(outputPort);

    const inputPort = screen.getByTestId("canvas-node-input-node-3");
    fireEvent.click(inputPort);

    expect(defaultProps.onConnectNodes).toHaveBeenCalledWith("node-1", "node-3");
    expect(screen.getByText(/Linearity violation: Source node already has a successor/i)).toBeInTheDocument();
  });

  it("rejects self-connection", () => {
    render(<WorkflowCanvas {...defaultProps} />);

    const outputPort = screen.getByTestId("canvas-node-output-node-3");
    fireEvent.click(outputPort);

    const inputPort = screen.getByTestId("canvas-node-input-node-3");
    fireEvent.click(inputPort);

    expect(defaultProps.onConnectNodes).not.toHaveBeenCalledWith("node-3", "node-3");
    expect(screen.getByText(/Cannot connect a node to itself/i)).toBeInTheDocument();
  });

  it("renders empty state when graph has no nodes", () => {
    render(
      <WorkflowCanvas
        {...defaultProps}
        graph={{ entryNodeId: "", nodes: [], edges: [] }}
      />,
    );

    expect(screen.getByText("Canvas is empty")).toBeInTheDocument();
  });
});
