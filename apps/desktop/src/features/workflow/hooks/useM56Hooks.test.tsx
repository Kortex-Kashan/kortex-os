/**
 * M5.6 — Approval, Schedule, and External Execution hook tests.
 */

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const {
  listPendingApprovalsMock,
  submitApprovalDecisionMock,
  listSchedulesMock,
  listExternalExecutionsMock,
} = vi.hoisted(() => ({
  listPendingApprovalsMock: vi.fn(),
  submitApprovalDecisionMock: vi.fn(),
  listSchedulesMock: vi.fn(),
  listExternalExecutionsMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    listPendingApprovals: listPendingApprovalsMock,
    submitApprovalDecision: submitApprovalDecisionMock,
    listSchedules: listSchedulesMock,
    listExternalExecutions: listExternalExecutionsMock,
  };
});

import { WorkflowAccessDeniedError } from "../api";
import { usePendingApprovals, useSubmitApprovalDecision } from "./useApprovals";
import { useSchedules } from "./useSchedules";
import { useExternalExecutions } from "./useExternalExecutions";

// ---------------------------------------------------------------------------
// Test wrapper
// ---------------------------------------------------------------------------

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  listPendingApprovalsMock.mockReset();
  submitApprovalDecisionMock.mockReset();
  listSchedulesMock.mockReset();
  listExternalExecutionsMock.mockReset();
});

// ---------------------------------------------------------------------------
// usePendingApprovals
// ---------------------------------------------------------------------------

describe("usePendingApprovals", () => {
  it("starts pending, resolves to approval list on success", async () => {
    const approval = { requestId: "req-1", workflowName: "Demo", status: "PENDING" };
    listPendingApprovalsMock.mockResolvedValueOnce([approval]);

    const { result } = renderHook(() => usePendingApprovals(), { wrapper });
    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("surfaces WorkflowAccessDeniedError without retrying", async () => {
    listPendingApprovalsMock.mockRejectedValueOnce(new WorkflowAccessDeniedError("denied"));

    const { result } = renderHook(() => usePendingApprovals(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(WorkflowAccessDeniedError);
    expect(listPendingApprovalsMock).toHaveBeenCalledTimes(1);
  });

  it("resolves to empty array when no approvals pending", async () => {
    listPendingApprovalsMock.mockResolvedValueOnce([]);
    const { result } = renderHook(() => usePendingApprovals(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// useSubmitApprovalDecision
// ---------------------------------------------------------------------------

describe("useSubmitApprovalDecision", () => {
  it("calls submitApprovalDecision with provided payload", async () => {
    submitApprovalDecisionMock.mockResolvedValueOnce(undefined);
    listPendingApprovalsMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useSubmitApprovalDecision(), { wrapper });
    await result.current.mutateAsync({ requestId: "req-1", decision: "APPROVED", rationale: "OK" });

    expect(submitApprovalDecisionMock).toHaveBeenCalledWith({
      requestId: "req-1",
      decision: "APPROVED",
      rationale: "OK",
    });
  });
});

// ---------------------------------------------------------------------------
// useSchedules
// ---------------------------------------------------------------------------

describe("useSchedules", () => {
  it("starts pending, resolves to schedule list on success", async () => {
    const sched = { scheduleId: "sched-1", cronExpression: "0 9 * * *", status: "ACTIVE" };
    listSchedulesMock.mockResolvedValueOnce([sched]);

    const { result } = renderHook(() => useSchedules(), { wrapper });
    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("surfaces WorkflowAccessDeniedError without retrying", async () => {
    listSchedulesMock.mockRejectedValueOnce(new WorkflowAccessDeniedError("denied"));

    const { result } = renderHook(() => useSchedules(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(WorkflowAccessDeniedError);
    expect(listSchedulesMock).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// useExternalExecutions
// ---------------------------------------------------------------------------

describe("useExternalExecutions", () => {
  it("starts pending, resolves to execution list on success", async () => {
    const exec = { executionId: "exec-1", status: "COMPLETED" };
    listExternalExecutionsMock.mockResolvedValueOnce([exec]);

    const { result } = renderHook(() => useExternalExecutions(), { wrapper });
    expect(result.current.isPending).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it("surfaces WorkflowAccessDeniedError without retrying", async () => {
    listExternalExecutionsMock.mockRejectedValueOnce(new WorkflowAccessDeniedError("denied"));

    const { result } = renderHook(() => useExternalExecutions(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(WorkflowAccessDeniedError);
    expect(listExternalExecutionsMock).toHaveBeenCalledTimes(1);
  });
});
