/**
 * InstanceTimeline interaction tests (M5-A7).
 *
 * Closes a real M5.6 testing gap: `handleCancel` previously bypassed the
 * shared mutation pattern, swallowed all errors, and had no loading-state
 * gating (M56-9) — nothing exercised it. This proves the corrected
 * confirm-then-mutate flow, including that a failure is actually shown to
 * the operator instead of disappearing silently.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listWorkflowInstancesMock, cancelWorkflowInstanceMock } = vi.hoisted(() => ({
  listWorkflowInstancesMock: vi.fn(),
  cancelWorkflowInstanceMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    listWorkflowInstances: listWorkflowInstancesMock,
    cancelWorkflowInstance: cancelWorkflowInstanceMock,
  };
});

import { InstanceTimeline } from "./InstanceTimeline";

const runningInstance = {
  id: "inst-1",
  definitionId: "wf-1",
  definitionVersion: "1.0.0",
  tenantId: "acme",
  currentStepIndex: 1,
  currentStepId: "step_b",
  state: "RUNNING" as const,
  status: "RUNNING" as const,
  traceId: "trace-1",
  version: 1,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:05:00Z",
};

function renderTimeline() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InstanceTimeline />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listWorkflowInstancesMock.mockReset();
  cancelWorkflowInstanceMock.mockReset();
});

describe("InstanceTimeline", () => {
  it("renders an instance card with real backend fields, no fabricated step timeline", async () => {
    listWorkflowInstancesMock.mockResolvedValue([runningInstance]);
    renderTimeline();
    await waitFor(() => expect(screen.getByText("wf-1")).toBeDefined());
    expect(screen.getByText(/#1 \(step_b\)/)).toBeDefined();
  });

  it("requires confirmation before cancelling a running instance", async () => {
    listWorkflowInstancesMock.mockResolvedValue([runningInstance]);
    cancelWorkflowInstanceMock.mockResolvedValue(undefined);
    renderTimeline();

    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel instance inst-1" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Cancel instance inst-1" }));

    expect(cancelWorkflowInstanceMock).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Confirm Cancel" }));

    await waitFor(() =>
      expect(cancelWorkflowInstanceMock).toHaveBeenCalledWith("inst-1", "Cancelled by operator"),
    );
  });

  it("surfaces a cancellation failure instead of failing silently", async () => {
    listWorkflowInstancesMock.mockResolvedValue([runningInstance]);
    cancelWorkflowInstanceMock.mockRejectedValue(new Error("Instance already completed"));
    renderTimeline();

    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel instance inst-1" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Cancel instance inst-1" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm Cancel" }));

    expect(await screen.findByText("Instance already completed")).toBeDefined();
  });

  it("does not show a cancel control for a terminal-state instance", async () => {
    listWorkflowInstancesMock.mockResolvedValue([{ ...runningInstance, state: "COMPLETED" as const }]);
    renderTimeline();
    await waitFor(() => expect(screen.getByText("wf-1")).toBeDefined());
    expect(screen.queryByRole("button", { name: "Cancel instance inst-1" })).toBeNull();
  });
});
