import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listPythonActionsMock, executePythonActionMock } = vi.hoisted(() => ({
  listPythonActionsMock: vi.fn(),
  executePythonActionMock: vi.fn(),
}));

vi.mock("../python-automation/api", async () => {
  const actual = await vi.importActual<typeof import("../python-automation/api")>("../python-automation/api");
  return { ...actual, listPythonActions: listPythonActionsMock, executePythonAction: executePythonActionMock };
});

import { PythonAutomationAccessDeniedError } from "../python-automation/api";
import { PythonAutomationTab } from "./PythonAutomationTab";

beforeEach(() => {
  listPythonActionsMock.mockReset();
  executePythonActionMock.mockReset();
});

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PythonAutomationTab />
    </QueryClientProvider>,
  );
}

describe("PythonAutomationTab", () => {
  it("shows a loading state while the request is in flight", () => {
    listPythonActionsMock.mockReturnValueOnce(new Promise(() => {}));

    renderTab();

    expect(screen.getByRole("status", { name: /loading python actions/i })).toBeInTheDocument();
  });

  it("shows an explicit empty state when no actions are published", async () => {
    listPythonActionsMock.mockResolvedValueOnce([]);

    renderTab();

    expect(await screen.findByText("No Python Actions have been published yet.")).toBeInTheDocument();
  });

  it("shows an access-denied state on PERMISSION_DENIED", async () => {
    listPythonActionsMock.mockRejectedValueOnce(
      new PythonAutomationAccessDeniedError("Missing permission: python:read"),
    );

    renderTab();

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(screen.getByText("You do not have permission to view Python Actions.")).toBeInTheDocument();
  });

  it("lists published actions with their id and pinned latest version", async () => {
    listPythonActionsMock.mockResolvedValueOnce([
      { actionId: "sync-inventory", name: "Sync Inventory", description: "", latestVersion: 2, isActive: true },
    ]);

    renderTab();

    expect(await screen.findByText("Sync Inventory")).toBeInTheDocument();
    expect(screen.getByText("sync-inventory · v2")).toBeInTheDocument();
  });

  it("disables Run for an inactive action", async () => {
    listPythonActionsMock.mockResolvedValueOnce([
      { actionId: "old-action", name: "Old Action", description: "", latestVersion: 1, isActive: false },
    ]);

    renderTab();

    await screen.findByText("Old Action");
    expect(screen.getByText("Inactive")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it("triggers the governed execution path with the action's pinned latest version and an empty payload", async () => {
    listPythonActionsMock.mockResolvedValueOnce([
      { actionId: "sync-inventory", name: "Sync Inventory", description: "", latestVersion: 2, isActive: true },
    ]);
    executePythonActionMock.mockResolvedValueOnce({
      status: "SUCCEEDED",
      output: { ok: true },
      error: null,
      exitCode: 0,
      durationMs: 42,
    });

    renderTab();
    await screen.findByText("Sync Inventory");
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => expect(executePythonActionMock).toHaveBeenCalledWith("sync-inventory", 2));
    expect(await screen.findByText("SUCCEEDED in 42ms.")).toBeInTheDocument();
  });

  it("surfaces a failed execution's status and error inline, as an alert", async () => {
    listPythonActionsMock.mockResolvedValueOnce([
      { actionId: "flaky-action", name: "Flaky Action", description: "", latestVersion: 1, isActive: true },
    ]);
    executePythonActionMock.mockResolvedValueOnce({
      status: "FAILED",
      output: null,
      error: "division by zero",
      exitCode: 1,
      durationMs: 5,
    });

    renderTab();
    await screen.findByText("Flaky Action");
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    const feedback = await screen.findByTestId("python-run-feedback");
    expect(feedback).toHaveTextContent("FAILED: division by zero");
    expect(feedback).toHaveAttribute("role", "alert");
  });

  it("surfaces a rejected execution call as an inline error, not a crash", async () => {
    listPythonActionsMock.mockResolvedValueOnce([
      { actionId: "act-1", name: "Action One", description: "", latestVersion: 1, isActive: true },
    ]);
    executePythonActionMock.mockRejectedValueOnce(new Error("backend unreachable"));

    renderTab();
    await screen.findByText("Action One");
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    expect(await screen.findByText("backend unreachable")).toBeInTheDocument();
  });

  it("disables Run while an execution is in flight", async () => {
    listPythonActionsMock.mockResolvedValueOnce([
      { actionId: "act-1", name: "Action One", description: "", latestVersion: 1, isActive: true },
    ]);
    executePythonActionMock.mockReturnValueOnce(new Promise(() => {}));

    renderTab();
    await screen.findByText("Action One");
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled());
  });

  it("refreshes the list when Refresh is clicked", async () => {
    listPythonActionsMock.mockResolvedValueOnce([]);
    listPythonActionsMock.mockResolvedValueOnce([
      { actionId: "act-1", name: "New Action", description: "", latestVersion: 1, isActive: true },
    ]);

    renderTab();
    await screen.findByText("No Python Actions have been published yet.");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("New Action")).toBeInTheDocument();
    expect(listPythonActionsMock).toHaveBeenCalledTimes(2);
  });
});
