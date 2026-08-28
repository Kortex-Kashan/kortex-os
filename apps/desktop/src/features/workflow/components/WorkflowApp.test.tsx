import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listWorkflowDefinitionsMock } = vi.hoisted(() => ({ listWorkflowDefinitionsMock: vi.fn() }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listWorkflowDefinitions: listWorkflowDefinitionsMock };
});

import { WorkflowAccessDeniedError, WorkflowRequestError } from "../api";
import type { WorkflowDefinition } from "../types";
import { WorkflowApp } from "./WorkflowApp";

beforeEach(() => {
  listWorkflowDefinitionsMock.mockReset();
});

function renderWorkflowApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkflowApp />
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
});
