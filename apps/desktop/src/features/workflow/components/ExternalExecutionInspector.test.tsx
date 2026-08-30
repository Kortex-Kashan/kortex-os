/**
 * ExternalExecutionInspector interaction tests (M5-A7).
 *
 * Closes a real M5.6 testing gap and proves the new cancel control
 * (M56-7 — the backend always supported `kortex.workflow.external.cancel`;
 * no UI ever reached it) actually calls through with confirmation.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listExternalExecutionsMock, cancelExternalExecutionMock } = vi.hoisted(() => ({
  listExternalExecutionsMock: vi.fn(),
  cancelExternalExecutionMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    listExternalExecutions: listExternalExecutionsMock,
    cancelExternalExecution: cancelExternalExecutionMock,
  };
});

import { ExternalExecutionInspector } from "./ExternalExecutionInspector";

const runningExecution = {
  id: "exec-1",
  status: "RUNNING" as const,
  target: "kortex.some.capability",
  output: null,
  error: null,
  attempts: 1,
  executionTimeMs: 500,
  approvalRequestId: null,
  tenantId: "acme",
};

const completedExecution = { ...runningExecution, id: "exec-2", status: "COMPLETED" as const, output: { ok: true } };

function renderInspector() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ExternalExecutionInspector />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listExternalExecutionsMock.mockReset();
  cancelExternalExecutionMock.mockReset();
});

describe("ExternalExecutionInspector", () => {
  it("renders an execution card with real backend fields", async () => {
    listExternalExecutionsMock.mockResolvedValue([completedExecution]);
    renderInspector();
    await waitFor(() => expect(screen.getByText("kortex.some.capability")).toBeDefined());
    expect(screen.getByText(/"ok": true/)).toBeDefined();
  });

  it("shows a cancel control for a running execution and requires confirmation", async () => {
    listExternalExecutionsMock.mockResolvedValue([runningExecution]);
    cancelExternalExecutionMock.mockResolvedValue(undefined);
    renderInspector();

    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel execution exec-1" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Cancel execution exec-1" }));

    expect(cancelExternalExecutionMock).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Confirm Cancel" }));

    await waitFor(() => expect(cancelExternalExecutionMock).toHaveBeenCalledWith("exec-1"));
  });

  it("does not show a cancel control for a completed execution", async () => {
    listExternalExecutionsMock.mockResolvedValue([completedExecution]);
    renderInspector();
    await waitFor(() => expect(screen.getByText("kortex.some.capability")).toBeDefined());
    expect(screen.queryByRole("button", { name: "Cancel execution exec-2" })).toBeNull();
  });
});
