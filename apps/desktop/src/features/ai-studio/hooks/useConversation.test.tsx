import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { getConversationHistoryMock, sendAgentMessageMock } = vi.hoisted(() => ({
  getConversationHistoryMock: vi.fn(),
  sendAgentMessageMock: vi.fn(),
}));

vi.mock("../chat-api", async () => {
  const actual = await vi.importActual<typeof import("../chat-api")>("../chat-api");
  return { ...actual, getConversationHistory: getConversationHistoryMock, sendAgentMessage: sendAgentMessageMock };
});

import { AiChatRequestError } from "../chat-api";
import { useConversation } from "./useConversation";

beforeEach(() => {
  getConversationHistoryMock.mockReset();
  sendAgentMessageMock.mockReset();
  window.localStorage.clear();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useConversation", () => {
  it("hydrates messages from durable history on mount", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([
      { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
    ]);

    const { result } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), { wrapper });

    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.messages[0]).toMatchObject({ role: "user", content: "Hello" });
    expect(result.current.messages[1]).toMatchObject({ role: "assistant", content: "Hi there" });
  });

  it("re-uses the same conversationId across remounts for the same tenant/user", async () => {
    getConversationHistoryMock.mockResolvedValue([]);

    const { result: first } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), {
      wrapper,
    });
    await waitFor(() => expect(first.current.isLoadingHistory).toBe(false));

    const { result: second } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), {
      wrapper,
    });
    await waitFor(() => expect(second.current.isLoadingHistory).toBe(false));

    expect(second.current.conversationId).toBe(first.current.conversationId);
  });

  it("appends an optimistic user message immediately, then the assistant reply on COMPLETED", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);
    sendAgentMessageMock.mockResolvedValueOnce({
      taskId: "task-1",
      tenantId: "tenant-1",
      status: "COMPLETED",
      finalResponse: "Hi!",
      totalSteps: 1,
      errorMessage: null,
      pendingToolCalls: [],
      degraded: false,
    });

    const { result } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), { wrapper });
    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));

    act(() => result.current.sendMessage("Hello"));
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]).toMatchObject({ role: "user", content: "Hello" });

    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.messages[1]).toMatchObject({ role: "assistant", content: "Hi!" });
    expect(result.current.pendingTaskId).toBeNull();
  });

  it("renders a PAUSED_FOR_APPROVAL result as a pending-approval message and sets pendingTaskId", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);
    sendAgentMessageMock.mockImplementationOnce((input: { taskId: string; tenantId: string }) =>
      Promise.resolve({
        taskId: input.taskId,
        tenantId: input.tenantId,
        status: "PAUSED_FOR_APPROVAL",
        finalResponse: null,
        totalSteps: 1,
        errorMessage: null,
        pendingToolCalls: [{ callId: "call-1", toolName: "create_order", arguments: { item: "Laptop" } }],
        degraded: false,
      }),
    );

    const { result } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), { wrapper });
    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));

    act(() => result.current.sendMessage("Order a laptop"));

    await waitFor(() => expect(result.current.pendingTaskId).not.toBeNull());
    const taskId = result.current.pendingTaskId as string;
    const pending = result.current.messages.find((m) => m.pendingApproval?.taskId === taskId);
    expect(pending?.pendingApproval).toEqual({
      taskId,
      goal: "Order a laptop",
      pendingToolCalls: [{ callId: "call-1", toolName: "create_order", arguments: { item: "Laptop" } }],
    });
  });

  it("appends a system message when sendAgentMessage rejects", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);
    sendAgentMessageMock.mockRejectedValueOnce(new AiChatRequestError("backend unreachable"));

    const { result } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), { wrapper });
    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));

    act(() => result.current.sendMessage("Hello"));

    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.messages[1]).toMatchObject({ role: "system", content: "backend unreachable" });
  });

  it("resolvePendingApproval(COMPLETED) replaces the pending message with the latest durable turn", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);
    sendAgentMessageMock.mockImplementationOnce((input: { taskId: string; tenantId: string }) =>
      Promise.resolve({
        taskId: input.taskId,
        tenantId: input.tenantId,
        status: "PAUSED_FOR_APPROVAL",
        finalResponse: null,
        totalSteps: 1,
        errorMessage: null,
        pendingToolCalls: [],
        degraded: false,
      }),
    );

    const { result } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), { wrapper });
    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));
    act(() => result.current.sendMessage("Order a laptop"));
    await waitFor(() => expect(result.current.pendingTaskId).not.toBeNull());
    const taskId = result.current.pendingTaskId as string;

    getConversationHistoryMock.mockResolvedValueOnce([
      {
        sequence: 1,
        userContent: "Order a laptop",
        assistantContent: "Order created successfully.",
        createdAt: "2026-01-01T00:00:00Z",
      },
    ]);

    await act(async () => {
      await result.current.resolvePendingApproval(taskId, "COMPLETED");
    });

    expect(result.current.pendingTaskId).toBeNull();
    const resolved = result.current.messages.find((m) => m.id === taskId);
    expect(resolved).toMatchObject({ role: "assistant", content: "Order created successfully." });
    expect(resolved?.pendingApproval).toBeUndefined();
  });

  it("resolvePendingApproval(CANCELLED) replaces the pending message with a system notice", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);
    sendAgentMessageMock.mockImplementationOnce((input: { taskId: string; tenantId: string }) =>
      Promise.resolve({
        taskId: input.taskId,
        tenantId: input.tenantId,
        status: "PAUSED_FOR_APPROVAL",
        finalResponse: null,
        totalSteps: 1,
        errorMessage: null,
        pendingToolCalls: [],
        degraded: false,
      }),
    );

    const { result } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), { wrapper });
    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));
    act(() => result.current.sendMessage("Order a laptop"));
    await waitFor(() => expect(result.current.pendingTaskId).not.toBeNull());
    const taskId = result.current.pendingTaskId as string;

    await act(async () => {
      await result.current.resolvePendingApproval(taskId, "CANCELLED");
    });

    expect(result.current.pendingTaskId).toBeNull();
    expect(getConversationHistoryMock).toHaveBeenCalledTimes(1); // only the initial hydration, never for CANCELLED
    const resolved = result.current.messages.find((m) => m.id === taskId);
    expect(resolved).toMatchObject({
      role: "system",
      content: "This request was rejected and will not be carried out.",
    });
  });
});
