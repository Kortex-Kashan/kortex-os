import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  projectCapabilitiesMock,
  getWorkflowDefinitionDraftMock,
  createWorkflowDraftMock,
  updateWorkflowDraftMock,
  validateWorkflowDraftMock,
  publishWorkflowDraftMock,
} = vi.hoisted(() => ({
  projectCapabilitiesMock: vi.fn(),
  getWorkflowDefinitionDraftMock: vi.fn(),
  createWorkflowDraftMock: vi.fn(),
  updateWorkflowDraftMock: vi.fn(),
  validateWorkflowDraftMock: vi.fn(),
  publishWorkflowDraftMock: vi.fn(),
}));

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    projectCapabilities: projectCapabilitiesMock,
    getWorkflowDefinitionDraft: getWorkflowDefinitionDraftMock,
    createWorkflowDraft: createWorkflowDraftMock,
    updateWorkflowDraft: updateWorkflowDraftMock,
    validateWorkflowDraft: validateWorkflowDraftMock,
    publishWorkflowDraft: publishWorkflowDraftMock,
  };
});

import { WorkflowBuilderTab } from "./WorkflowBuilderTab";

describe("WorkflowBuilderTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    projectCapabilitiesMock.mockResolvedValue([
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
        name: "kortex.connector.notification.webhook.send",
        description: "Dispatch outgoing HTTP webhook notification.",
        provider: "connector",
        parametersSchema: {
          type: "object",
          properties: {
            url: { type: "string" },
          },
          required: ["url"],
        },
        isReadOnly: false,
        isIdempotent: false,
      },
    ]);

    getWorkflowDefinitionDraftMock.mockResolvedValue(null);

    validateWorkflowDraftMock.mockResolvedValue({
      isValid: true,
      errors: [],
      warnings: [],
    });
  });

  function renderBuilder(initialEntries: string[] = ["/workflows/builder"]) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={initialEntries}>
          <WorkflowBuilderTab />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("renders empty builder with palette, toolbar, and canvas empty state", async () => {
    renderBuilder();

    expect(screen.getByTestId("capability-palette")).toBeInTheDocument();
    expect(screen.getByTestId("builder-toolbar")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-canvas-viewport")).toBeInTheDocument();
    expect(screen.getByText("Canvas is empty")).toBeInTheDocument();
  });

  it("adds capability step and approval gate to canvas maintaining linear chain", async () => {
    renderBuilder();

    // Wait for capabilities to load
    await waitFor(() => {
      expect(screen.getByTestId("palette-capability-kortex.finance.invoice.get")).toBeInTheDocument();
    });

    // 1. Add capability step
    const invoiceCap = screen.getByTestId("palette-capability-kortex.finance.invoice.get");
    fireEvent.click(invoiceCap);

    // Node is added and empty state disappears
    await waitFor(() => {
      expect(screen.queryByText("Canvas is empty")).not.toBeInTheDocument();
      expect(screen.getByText("START")).toBeInTheDocument();
    });

    // Inspector opens for selected node
    expect(screen.getByTestId("node-inspector")).toBeInTheDocument();

    // 2. Add Approval Gate (Correction 1: Pure wait gate, capability_name = null)
    const approvalGateItem = screen.getByTestId("palette-approval-gate");
    fireEvent.click(approvalGateItem);

    await waitFor(() => {
      expect(screen.getByText("GATE")).toBeInTheDocument();
      expect(screen.getByText("Approval Gate 2")).toBeInTheDocument();
    });
  });

  it("saves new workflow draft and updates lock version", async () => {
    createWorkflowDraftMock.mockResolvedValueOnce({
      id: "wf_123",
      definitionId: "wf_123",
      tenantId: "acme",
      version: "0.1.0",
      status: "DRAFT",
      name: "Invoice Pipeline",
      description: "Processes incoming invoices",
      trigger: "MANUAL",
      priority: "NORMAL",
      timeoutSeconds: 3600,
      steps: [],
      graph: {
        entryNodeId: "node_1",
        nodes: [],
        edges: [],
      },
      lockVersion: 2,
    });

    renderBuilder();

    // Change title
    const nameInput = screen.getByTestId("builder-name-input");
    fireEvent.change(nameInput, { target: { value: "Invoice Pipeline" } });

    // Add node so dirty state triggers
    await waitFor(() => {
      expect(screen.getByTestId("palette-capability-kortex.finance.invoice.get")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("palette-capability-kortex.finance.invoice.get"));

    // Click Save Draft
    const saveBtn = screen.getByTestId("builder-save-draft-button");
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(createWorkflowDraftMock).toHaveBeenCalledTimes(1);
    });

    const callArg = createWorkflowDraftMock.mock.calls[0][0];
    expect(callArg.name).toBe("Invoice Pipeline");
    expect(callArg.graph.nodes.length).toBe(1);
  });

  it("handles optimistic locking conflict (409 Conflict) on draft update", async () => {
    getWorkflowDefinitionDraftMock.mockResolvedValueOnce({
      id: "wf_existing",
      definitionId: "wf_existing",
      tenantId: "acme",
      version: "0.1.0",
      status: "DRAFT",
      name: "Existing Flow",
      description: "",
      trigger: "MANUAL",
      priority: "NORMAL",
      timeoutSeconds: 3600,
      steps: [],
      graph: {
        entryNodeId: "node_1",
        nodes: [
          {
            nodeId: "node_1",
            nodeType: "capability",
            capabilityName: "kortex.finance.invoice.get",
            config: {},
            inputPorts: ["input"],
            outputPorts: ["output"],
            metadata: {
              "kortex.workflow.step": { name: "Step 1" },
            },
          },
        ],
        edges: [],
      },
      lockVersion: 1,
    });

    const conflictErr = new Error("Draft was modified concurrently by another session (optimistic lock error).");
    (conflictErr as any).status = 409;
    updateWorkflowDraftMock.mockRejectedValueOnce(conflictErr);

    renderBuilder(["/workflows/builder?definitionId=wf_existing"]);

    await waitFor(() => {
      expect(screen.getByDisplayValue("Existing Flow")).toBeInTheDocument();
    });

    // Make a change
    const nameInput = screen.getByTestId("builder-name-input");
    fireEvent.change(nameInput, { target: { value: "Conflicting Flow" } });

    // Try to save
    const saveBtn = screen.getByTestId("builder-save-draft-button");
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateWorkflowDraftMock).toHaveBeenCalledWith(
        expect.objectContaining({
          definitionId: "wf_existing",
          expectedLockVersion: 1,
        }),
      );
    });

    // Should display conflict alert
    await waitFor(() => {
      expect(screen.getByTestId("builder-conflict-banner")).toBeInTheDocument();
      expect(screen.getByText(/Version conflict: This draft was modified elsewhere/i)).toBeInTheDocument();
    });
  });

  it("supports undo and redo on canvas graph mutations", async () => {
    renderBuilder();

    await waitFor(() => {
      expect(screen.getByTestId("palette-capability-kortex.finance.invoice.get")).toBeInTheDocument();
    });

    // Add step 1
    fireEvent.click(screen.getByTestId("palette-capability-kortex.finance.invoice.get"));
    await waitFor(() => {
      expect(screen.queryByText("Canvas is empty")).not.toBeInTheDocument();
    });

    // Undo button should now be enabled
    const undoBtn = screen.getByTestId("builder-undo-button");
    expect(undoBtn).not.toBeDisabled();

    // Click Undo
    fireEvent.click(undoBtn);

    // Canvas returns to empty state
    await waitFor(() => {
      expect(screen.getByText("Canvas is empty")).toBeInTheDocument();
    });

    // Redo button is enabled
    const redoBtn = screen.getByTestId("builder-redo-button");
    expect(redoBtn).not.toBeDisabled();

    // Click Redo
    fireEvent.click(redoBtn);

    // Node is restored
    await waitFor(() => {
      expect(screen.queryByText("Canvas is empty")).not.toBeInTheDocument();
    });
  });

  it("opens review dialog and confirms publication with human authorization", async () => {
    getWorkflowDefinitionDraftMock.mockResolvedValueOnce({
      id: "wf_ready",
      definitionId: "wf_ready",
      tenantId: "acme",
      version: "0.1.0",
      status: "DRAFT",
      name: "Ready Workflow",
      description: "Ready to publish",
      trigger: "MANUAL",
      priority: "NORMAL",
      timeoutSeconds: 3600,
      steps: [],
      graph: {
        entryNodeId: "node_1",
        nodes: [
          {
            nodeId: "node_1",
            nodeType: "capability",
            capabilityName: "kortex.finance.invoice.get",
            config: {},
            inputPorts: ["input"],
            outputPorts: ["output"],
            metadata: {
              "kortex.workflow.step": { name: "Step 1" },
            },
          },
        ],
        edges: [],
      },
      lockVersion: 1,
    });

    publishWorkflowDraftMock.mockResolvedValueOnce({
      definitionId: "wf_ready",
      version: "1.0.0",
      status: "PUBLISHED",
      publishedAt: new Date().toISOString(),
    });

    renderBuilder(["/workflows/builder?definitionId=wf_ready"]);

    await waitFor(() => {
      expect(screen.getByDisplayValue("Ready Workflow")).toBeInTheDocument();
    });

    // Click Review & Publish
    const publishBtn = screen.getByTestId("builder-publish-button");
    fireEvent.click(publishBtn);

    // Review modal opens
    await waitFor(() => {
      expect(screen.getByTestId("publish-review-dialog")).toBeInTheDocument();
      expect(screen.getByText("Review & Publish Workflow")).toBeInTheDocument();
    });

    // Confirm publish
    const confirmBtn = screen.getByTestId("publish-confirm-button");
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(publishWorkflowDraftMock).toHaveBeenCalledWith("wf_ready", 1);
    });
  });
});
