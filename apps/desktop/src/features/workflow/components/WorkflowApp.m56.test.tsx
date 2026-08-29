/**
 * M5.6 — WorkflowApp tab navigation tests.
 *
 * Verifies that the tabbed workflow shell renders correctly and that
 * all five tabs are navigable without crashing. The Definitions tab
 * behavior is separately tested in WorkflowApp.test.tsx.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const {
  listWorkflowDefinitionsMock,
  listWorkflowInstancesMock,
  listPendingApprovalsMock,
  listSchedulesMock,
  listExternalExecutionsMock,
} = vi.hoisted(() => ({
  listWorkflowDefinitionsMock: vi.fn(),
  listWorkflowInstancesMock: vi.fn(),
  listPendingApprovalsMock: vi.fn(),
  listSchedulesMock: vi.fn(),
  listExternalExecutionsMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    listWorkflowDefinitions: listWorkflowDefinitionsMock,
    listWorkflowInstances: listWorkflowInstancesMock,
    listPendingApprovals: listPendingApprovalsMock,
    listSchedules: listSchedulesMock,
    listExternalExecutions: listExternalExecutionsMock,
  };
});

import { WorkflowApp } from "./WorkflowApp";

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <WorkflowApp />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listWorkflowDefinitionsMock.mockResolvedValue([]);
  listWorkflowInstancesMock.mockResolvedValue([]);
  listPendingApprovalsMock.mockResolvedValue([]);
  listSchedulesMock.mockResolvedValue([]);
  listExternalExecutionsMock.mockResolvedValue([]);
});

describe("WorkflowApp tab navigation", () => {
  it("renders all five tab buttons", () => {
    renderApp();
    expect(screen.getByRole("tab", { name: "Definitions" })).toBeDefined();
    expect(screen.getByRole("tab", { name: "Instances" })).toBeDefined();
    expect(screen.getByRole("tab", { name: "Approvals" })).toBeDefined();
    expect(screen.getByRole("tab", { name: "Schedules" })).toBeDefined();
    expect(screen.getByRole("tab", { name: "Governed Executions" })).toBeDefined();
  });

  it("defaults to Definitions tab with aria-selected=true", () => {
    renderApp();
    const defTab = screen.getByRole("tab", { name: "Definitions" });
    expect(defTab.getAttribute("aria-selected")).toBe("true");
    const instanceTab = screen.getByRole("tab", { name: "Instances" });
    expect(instanceTab.getAttribute("aria-selected")).toBe("false");
  });

  it("switches to Instances tab on click", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: "Instances" }));

    const instTab = screen.getByRole("tab", { name: "Instances" });
    expect(instTab.getAttribute("aria-selected")).toBe("true");

    // Definitions tab loses selection
    const defTab = screen.getByRole("tab", { name: "Definitions" });
    expect(defTab.getAttribute("aria-selected")).toBe("false");
  });

  it("switches to Approvals tab and shows empty state", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: "Approvals" }));

    await waitFor(() => {
      expect(screen.getByText("No pending approvals.")).toBeDefined();
    });
  });

  it("switches to Schedules tab and shows empty state", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: "Schedules" }));

    await waitFor(() => {
      expect(screen.getByText("No schedules configured.")).toBeDefined();
    });
  });

  it("switches to Governed Executions tab and shows empty state", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: "Governed Executions" }));

    await waitFor(() => {
      expect(screen.getByText("No external executions recorded.")).toBeDefined();
    });
  });

  it("shows pending approval badge count when approvals exist", async () => {
    listPendingApprovalsMock.mockResolvedValue([
      {
        requestId: "req-1",
        workflowName: "Demo",
        status: "PENDING",
        requiredRole: "admin",
        requesterPrincipalId: "alice",
        instanceId: "inst-1",
        stepId: "s1",
        tenantId: "acme",
        context: {},
        expiresAt: null,
        createdAt: "2026-01-01T00:00:00Z",
        decidedAt: null,
        deciderPrincipalId: null,
        decisionRationale: null,
      },
    ]);

    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: "Approvals" }));

    await waitFor(() => {
      expect(screen.getByLabelText("1 pending approvals")).toBeDefined();
    });
  });
});
