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

  // ---------------------------------------------------------------------------
  // Phase C: multi-conversation support -- New Chat / Recent Conversations
  // ---------------------------------------------------------------------------

  describe("switchConversation / startNewConversation", () => {
    it("startNewConversation generates a fresh id and clears the transcript without hydrating stale history", async () => {
      getConversationHistoryMock.mockResolvedValue([]);

      const { result } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), { wrapper });
      await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));

      act(() => result.current.sendMessage("something in the first conversation"));
      expect(result.current.messages).toHaveLength(1);
      const firstConversationId = result.current.conversationId;

      act(() => result.current.startNewConversation());

      expect(result.current.conversationId).not.toBe(firstConversationId);
      expect(result.current.messages).toHaveLength(0);
      expect(result.current.pendingTaskId).toBeNull();
    });

    it("switchConversation loads the selected conversation's own durable transcript", async () => {
      getConversationHistoryMock.mockResolvedValueOnce([]); // initial mount hydration
      sendAgentMessageMock.mockResolvedValueOnce({
        taskId: "task-original",
        tenantId: "tenant-1",
        status: "COMPLETED",
        finalResponse: "reply in the original conversation",
        totalSteps: 1,
        errorMessage: null,
        pendingToolCalls: [],
        degraded: false,
      });

      const { result } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), { wrapper });
      await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));

      act(() => result.current.sendMessage("message in the original conversation"));
      // Let the send settle fully before switching, so its async
      // resolution can never land a message into the conversation this
      // test is about to switch away from.
      await waitFor(() => expect(result.current.messages).toHaveLength(2));

      getConversationHistoryMock.mockResolvedValueOnce([
        {
          sequence: 1,
          userContent: "an older question",
          assistantContent: "an older answer",
          createdAt: "2026-01-01T00:00:00Z",
        },
      ]);

      act(() => result.current.switchConversation("conv-from-recent-list"));

      expect(result.current.conversationId).toBe("conv-from-recent-list");
      // The original conversation's optimistic message must not leak into
      // the newly selected one.
      expect(result.current.messages.some((m) => m.content === "message in the original conversation")).toBe(false);

      await waitFor(() => expect(result.current.messages).toHaveLength(2));
      expect(result.current.messages[0]).toMatchObject({ role: "user", content: "an older question" });
      expect(result.current.messages[1]).toMatchObject({ role: "assistant", content: "an older answer" });
    });

    it("a reply that resolves AFTER the user has already switched away is never appended to the new conversation's transcript", async () => {
      // Closeout code-review fix regression test: unlike the test above
      // (which waits for the send to settle before switching), this one
      // switches conversations WHILE the send is still in flight, using a
      // manually-controlled promise so the race is deterministic rather
      // than timing-dependent.
      getConversationHistoryMock.mockResolvedValueOnce([]); // initial mount hydration
      let resolveSend: (value: unknown) => void = () => {};
      sendAgentMessageMock.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSend = resolve;
          }),
      );

      const { result } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), { wrapper });
      await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));

      act(() => result.current.sendMessage("message sent just before switching away"));
      expect(result.current.messages).toHaveLength(1); // optimistic user message only -- send still pending

      getConversationHistoryMock.mockResolvedValueOnce([]); // the newly-switched-to conversation's own (empty) history
      act(() => result.current.switchConversation("conv-switched-to-mid-flight"));
      expect(result.current.messages).toHaveLength(0);

      // The original send now resolves -- its reply must land nowhere,
      // since the transcript on screen belongs to a different conversation.
      await act(async () => {
        resolveSend({
          taskId: "task-original",
          tenantId: "tenant-1",
          status: "COMPLETED",
          finalResponse: "a reply for the conversation the user already left",
          totalSteps: 1,
          errorMessage: null,
          pendingToolCalls: [],
          degraded: false,
        });
        await Promise.resolve();
      });

      expect(result.current.messages).toHaveLength(0);
      expect(
        result.current.messages.some((m) => m.content === "a reply for the conversation the user already left"),
      ).toBe(false);
    });

    it("switching to the already-active conversation id is a no-op", async () => {
      getConversationHistoryMock.mockResolvedValue([]);

      const { result } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), { wrapper });
      await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));

      act(() => result.current.sendMessage("keep me"));
      expect(result.current.messages).toHaveLength(1);
      const activeId = result.current.conversationId;

      act(() => result.current.switchConversation(activeId));

      expect(result.current.conversationId).toBe(activeId);
      expect(result.current.messages).toHaveLength(1);
    });

    it("persists the switched-to conversation as active across a remount", async () => {
      getConversationHistoryMock.mockResolvedValue([]);

      const { result: first } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), {
        wrapper,
      });
      await waitFor(() => expect(first.current.isLoadingHistory).toBe(false));

      act(() => first.current.switchConversation("conv-selected-from-recent"));
      expect(first.current.conversationId).toBe("conv-selected-from-recent");

      const { result: second } = renderHook(() => useConversation({ tenantId: "tenant-1", userId: "user-1" }), {
        wrapper,
      });
      await waitFor(() => expect(second.current.isLoadingHistory).toBe(false));

      expect(second.current.conversationId).toBe("conv-selected-from-recent");
    });
  });
});
