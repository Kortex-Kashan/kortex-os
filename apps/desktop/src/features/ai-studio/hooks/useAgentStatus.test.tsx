import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { getAgentStatusMock } = vi.hoisted(() => ({ getAgentStatusMock: vi.fn() }));

vi.mock("../chat-api", async () => {
  const actual = await vi.importActual<typeof import("../chat-api")>("../chat-api");
  return { ...actual, getAgentStatus: getAgentStatusMock };
});

import { useAgentStatus } from "./useAgentStatus";

beforeEach(() => {
  getAgentStatusMock.mockReset();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useAgentStatus", () => {
  it("does not query while disabled", async () => {
    const { result } = renderHook(() => useAgentStatus("task-1", "tenant-1", false), { wrapper });

    expect(result.current.fetchStatus).toBe("idle");
    expect(getAgentStatusMock).not.toHaveBeenCalled();
  });

  it("queries kortex.ai.agent.status when enabled", async () => {
    getAgentStatusMock.mockResolvedValue({
      taskId: "task-1",
      tenantId: "tenant-1",
      status: "PAUSED_FOR_APPROVAL",
      conversationId: "conv-1",
    });

    const { result } = renderHook(() => useAgentStatus("task-1", "tenant-1", true), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getAgentStatusMock).toHaveBeenCalledWith("task-1", "tenant-1");
    expect(result.current.data?.status).toBe("PAUSED_FOR_APPROVAL");
  });

  it("stops polling once a terminal status is observed", async () => {
    getAgentStatusMock.mockResolvedValue({
      taskId: "task-1",
      tenantId: "tenant-1",
      status: "COMPLETED",
      conversationId: "conv-1",
    });

    const { result } = renderHook(() => useAgentStatus("task-1", "tenant-1", true), { wrapper });

    await waitFor(() => expect(result.current.data?.status).toBe("COMPLETED"));

    const callsAtCompletion = getAgentStatusMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 2_500));
    expect(getAgentStatusMock.mock.calls.length).toBe(callsAtCompletion);
  });
});
