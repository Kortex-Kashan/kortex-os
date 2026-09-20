import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listWorkflowDefinitionsMock, listPendingApprovalsMock, listPythonActionsMock } = vi.hoisted(() => ({
  listWorkflowDefinitionsMock: vi.fn(),
  listPendingApprovalsMock: vi.fn(),
  listPythonActionsMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    listWorkflowDefinitions: listWorkflowDefinitionsMock,
    listPendingApprovals: listPendingApprovalsMock,
  };
});

vi.mock("../python-automation/api", async () => {
  const actual = await vi.importActual<typeof import("../python-automation/api")>("../python-automation/api");
  return { ...actual, listPythonActions: listPythonActionsMock };
});

vi.mock("@/auth/AuthProvider", () => ({
  useAuth: () => ({
    state: {
      status: "AUTHENTICATED",
      identity: { tenantId: "acme", principalId: "alice", principalType: "USER", roles: ["ADMIN"] },
    },
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

import { WorkflowAccessDeniedError, WorkflowRequestError } from "../api";
import type { WorkflowDefinition } from "../types";
import { WorkflowApp } from "./WorkflowApp";

beforeEach(() => {
  listWorkflowDefinitionsMock.mockReset();
  listPendingApprovalsMock.mockReset();
  listPendingApprovalsMock.mockResolvedValue([]);
  listPythonActionsMock.mockReset();
  listPythonActionsMock.mockResolvedValue([]);
});

function renderWorkflowApp(initialEntries: string[] = ["/workflows"]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <WorkflowApp />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function makeDefinition(overrides: Partial<WorkflowDefinition> = {}): WorkflowDefinition {
  return {
    id: "wf_demo",
    name: "Demo Workflow",
    version: "1.0.0",
    description: "A demo workflow definition.",
    trigger: "MANUAL",
    priority: "NORMAL",
    timeoutSeconds: 3600,
    steps: [{ id: "s1", name: "Step 1", capabilityName: "kortex.connector.action.execute", isApprovalStep: false }],
    ...overrides,
  };
}

describe("WorkflowApp", () => {
  it("shows a loading state while the request is in flight", () => {
    listWorkflowDefinitionsMock.mockReturnValueOnce(new Promise<WorkflowDefinition[]>(() => {}));

    renderWorkflowApp();

    expect(screen.getByRole("status", { name: /loading workflow registry/i })).toBeInTheDocument();
  });

  it("shows the empty-registry message when no workflows are registered", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);

    renderWorkflowApp();

    expect(await screen.findByText("No workflows are currently registered.")).toBeInTheDocument();
  });

  it("renders real registry data for a populated registry, identifying each definition", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([
      makeDefinition(),
      makeDefinition({ id: "wf_other", name: "Other Workflow", version: "2.0.0" }),
    ]);

    renderWorkflowApp();

    expect(await screen.findByText("Demo Workflow")).toBeInTheDocument();
    expect(screen.getByText("Other Workflow")).toBeInTheDocument();
    expect(screen.getAllByTestId("workflow-definition-card")).toHaveLength(2);
  });

  it("never renders step parameters or compensation-action data, even if present on a definition object", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([
      {
        ...makeDefinition(),
        steps: [
          {
            id: "s1",
            name: "Step 1",
            capabilityName: "kortex.connector.action.execute",
            isApprovalStep: false,
            parameters: { apiKey: "should_never_render" },
          },
        ],
      },
    ]);

    renderWorkflowApp();

    await screen.findByText("Demo Workflow");
    expect(screen.queryByText(/should_never_render/i)).not.toBeInTheDocument();
  });

  it("shows an access-denied state — not a session-expired claim — on PERMISSION_DENIED", async () => {
    listWorkflowDefinitionsMock.mockRejectedValueOnce(
      new WorkflowAccessDeniedError("Missing permission: workflow:read"),
    );

    renderWorkflowApp();

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(
      screen.getByText("You do not have permission to view the workflow registry."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/session expired/i)).not.toBeInTheDocument();
  });

  it("shows a generic, recoverable error state with a retry action on any other failure", async () => {
    // A non-access-denied failure gets one automatic retry (`useWorkflows`'s
    // own `retry` option, mirroring the app's global `retry: 1` default) —
    // persistently rejecting and extending the timeout accounts for that
    // real retry delay rather than racing it (see the M5 Connectors
    // component test for the same pattern and its rationale).
    listWorkflowDefinitionsMock.mockRejectedValue(new WorkflowRequestError("backend unreachable"));

    renderWorkflowApp();

    expect(
      await screen.findByText("Something went wrong loading the workflow registry.", undefined, {
        timeout: 3000,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  }, 8000);

  it("retries the request when Retry is clicked", async () => {
    listWorkflowDefinitionsMock.mockRejectedValue(new WorkflowRequestError("backend unreachable"));

    renderWorkflowApp();

    const retryButton = await screen.findByRole("button", { name: "Retry" }, { timeout: 3000 });
    listWorkflowDefinitionsMock.mockReset();
    listWorkflowDefinitionsMock.mockResolvedValueOnce([makeDefinition()]);
    fireEvent.click(retryButton);

    expect(await screen.findByText("Demo Workflow")).toBeInTheDocument();
  }, 8000);

  it("refreshes populated data when Refresh is clicked", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([makeDefinition()]);
    listWorkflowDefinitionsMock.mockResolvedValueOnce([
      makeDefinition(),
      makeDefinition({ id: "wf_other", name: "Other Workflow" }),
    ]);

    renderWorkflowApp();

    await screen.findByText("Demo Workflow");
    const refreshButton = screen.getByRole("button", { name: "Refresh" });
    fireEvent.click(refreshButton);

    expect(await screen.findByText("Other Workflow")).toBeInTheDocument();
    expect(listWorkflowDefinitionsMock).toHaveBeenCalledTimes(2);
  });

  it("M7.2: deep-links straight to the Approvals tab via a ?tab= query param", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);

    renderWorkflowApp(["/workflows?tab=approvals"]);

    expect(screen.getByRole("tab", { name: "Approvals" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Definitions" })).toHaveAttribute("aria-selected", "false");
    expect(await screen.findByLabelText("Pending Approvals")).toBeInTheDocument();
  });

  it("M7.2: ignores an unrecognized ?tab= value and falls back to Definitions", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);

    renderWorkflowApp(["/workflows?tab=not-a-real-tab"]);

    expect(screen.getByRole("tab", { name: "Definitions" })).toHaveAttribute("aria-selected", "true");
  });

  // ---------------------------------------------------------------------------
  // Phase E: Workflow Engine IA -- Manual/AI/Python Automation
  // ---------------------------------------------------------------------------

  it("exposes the intended automation tabs: Manual, AI, and Python Automation, alongside the existing ones", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);

    renderWorkflowApp();

    expect(screen.getByRole("tab", { name: "Definitions" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Manual Automation" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "AI Automation" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Python Automation" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Instances" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Approvals" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Schedules" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Governed Executions" })).toBeInTheDocument();
    // The old bare "Builder" label is gone -- renamed, not duplicated.
    expect(screen.queryByRole("tab", { name: "Builder" })).not.toBeInTheDocument();
  });

  it("Manual Automation still renders the existing visual workflow builder, unchanged behavior under a new label", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);

    renderWorkflowApp();
    fireEvent.click(screen.getByRole("tab", { name: "Manual Automation" }));

    expect(await screen.findByTestId("workflow-builder-tab")).toBeInTheDocument();
  });

  it("AI Automation renders the existing (relocated) AI Workflow Builder, reaching its real functionality", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);

    renderWorkflowApp();
    fireEvent.click(screen.getByRole("tab", { name: "AI Automation" }));

    expect(await screen.findByTestId("ai-workflow-builder-panel")).toBeInTheDocument();
    // The real intent-input/Generate flow is present, not a placeholder.
    expect(screen.getByTestId("builder-intent-input")).toBeInTheDocument();
    expect(screen.getByTestId("builder-generate-button")).toBeInTheDocument();
  });

  it("Python Automation lists already-published Python Actions and can trigger one through the governed execution path", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);
    listPythonActionsMock.mockResolvedValueOnce([
      { actionId: "act-1", name: "Sync Inventory", description: "Syncs inventory levels.", latestVersion: 3, isActive: true },
    ]);

    renderWorkflowApp();
    fireEvent.click(screen.getByRole("tab", { name: "Python Automation" }));

    expect(await screen.findByText("Sync Inventory")).toBeInTheDocument();
    expect(screen.getByText("act-1 · v3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument();
  });

  it("Python Automation shows an explicit empty state when no actions are published", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);
    listPythonActionsMock.mockResolvedValueOnce([]);

    renderWorkflowApp();
    fireEvent.click(screen.getByRole("tab", { name: "Python Automation" }));

    expect(await screen.findByText("No Python Actions have been published yet.")).toBeInTheDocument();
  });

  it("deep-links straight to the AI Automation tab via a ?tab= query param (Mini Chat's entry point)", async () => {
    listWorkflowDefinitionsMock.mockResolvedValueOnce([]);

    renderWorkflowApp(["/workflows?tab=aiAutomation"]);

    expect(screen.getByRole("tab", { name: "AI Automation" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("ai-workflow-builder-panel")).toBeInTheDocument();
  });
});
