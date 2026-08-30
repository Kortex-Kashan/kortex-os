/**
 * ApprovalQueue interaction tests (M5-A7).
 *
 * Closes a real M5.6 testing gap: this component previously had zero
 * dedicated tests, which is exactly why the render-crashing field mismatch
 * (M56-2) and the discarded-rationale payload bug (M56-3) shipped
 * undetected — `WorkflowApp.m56.test.tsx` only ever asserted the badge
 * count, never opened the Decide dialog or submitted a decision.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listPendingApprovalsMock, submitApprovalDecisionMock, delegateApprovalMock } = vi.hoisted(() => ({
  listPendingApprovalsMock: vi.fn(),
  submitApprovalDecisionMock: vi.fn(),
  delegateApprovalMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    listPendingApprovals: listPendingApprovalsMock,
    submitApprovalDecision: submitApprovalDecisionMock,
    delegateApproval: delegateApprovalMock,
  };
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

import { ApprovalQueue } from "./ApprovalQueue";

const pendingApproval = {
  id: "req-1",
  tenantId: "acme",
  instanceId: "inst-1",
  stepId: "s1",
  requiredRole: "admin",
  state: "PENDING" as const,
  timeoutAt: null,
  signatureRequired: false,
  requesterPrincipalId: null,
  requesterPrincipalType: null,
  correlationId: null,
};

function renderQueue() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ApprovalQueue />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listPendingApprovalsMock.mockReset();
  submitApprovalDecisionMock.mockReset();
  delegateApprovalMock.mockReset();
});

describe("ApprovalQueue", () => {
  it("renders a pending approval card with real backend fields", async () => {
    listPendingApprovalsMock.mockResolvedValue([pendingApproval]);
    renderQueue();

    await waitFor(() => expect(screen.getByText(/Required Role: admin/)).toBeDefined());
    expect(screen.getByText(/req-1/)).toBeDefined();
  });

  it("submits an approval decision as the current session's own principal ID", async () => {
    listPendingApprovalsMock.mockResolvedValue([pendingApproval]);
    submitApprovalDecisionMock.mockResolvedValue(undefined);
    renderQueue();

    await waitFor(() => expect(screen.getByRole("button", { name: "Decide request req-1" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Decide request req-1" }));

    const reasonInput = await screen.findByLabelText("Reason *");
    fireEvent.change(reasonInput, { target: { value: "Looks correct" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve this approval request" }));

    await waitFor(() =>
      expect(submitApprovalDecisionMock).toHaveBeenCalledWith({
        requestId: "req-1",
        decision: "APPROVED",
        approverId: "alice",
        reason: "Looks correct",
      }),
    );
  });

  it("requires a reason before submitting a decision", async () => {
    listPendingApprovalsMock.mockResolvedValue([pendingApproval]);
    renderQueue();

    await waitFor(() => expect(screen.getByRole("button", { name: "Decide request req-1" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Decide request req-1" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve this approval request" }));

    expect(await screen.findByText("A reason is required.")).toBeDefined();
    expect(submitApprovalDecisionMock).not.toHaveBeenCalled();
  });

  it("surfaces a mutation failure inline instead of failing silently", async () => {
    listPendingApprovalsMock.mockResolvedValue([pendingApproval]);
    submitApprovalDecisionMock.mockRejectedValue(new Error("Backend rejected the decision"));
    renderQueue();

    await waitFor(() => expect(screen.getByRole("button", { name: "Decide request req-1" })).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Decide request req-1" }));
    fireEvent.change(await screen.findByLabelText("Reason *"), { target: { value: "reason" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve this approval request" }));

    expect(await screen.findByText("Backend rejected the decision")).toBeDefined();
  });

  it("delegates the ticket's required role with a time-bounded window", async () => {
    listPendingApprovalsMock.mockResolvedValue([pendingApproval]);
    delegateApprovalMock.mockResolvedValue(undefined);
    renderQueue();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Delegate role for request req-1" })).toBeDefined(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delegate role for request req-1" }));

    fireEvent.change(await screen.findByLabelText("Delegate to Principal ID *"), {
      target: { value: "bob" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Delegate" }));

    await waitFor(() => expect(delegateApprovalMock).toHaveBeenCalledTimes(1));
    const call = delegateApprovalMock.mock.calls[0][0];
    expect(call.delegatorId).toBe("alice");
    expect(call.delegateeId).toBe("bob");
    expect(call.role).toBe("admin");
    expect(new Date(call.validUntil).getTime()).toBeGreaterThan(new Date(call.validFrom).getTime());
  });
});
