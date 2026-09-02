import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import {
  AiChatAccessDeniedError,
  AiChatRequestError,
  getAgentStatus,
  getConversationHistory,
  sendAgentMessage,
} from "./chat-api";

beforeEach(() => {
  invokeCapabilityMock.mockReset();
});

function successEnvelope(result: unknown) {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "SUCCESS" as const,
    payload: { result },
    errors: [],
    warnings: [],
    executionDurationMs: 1,
  };
}

function failureEnvelope(category: string, message: string) {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "FAILURE" as const,
    payload: null,
    errors: [{ category, message, correlationId: "corr-1" }],
    warnings: [],
    executionDurationMs: 1,
  };
}

describe("sendAgentMessage", () => {
  const input = {
    taskId: "task-1",
    tenantId: "tenant-1",
    userId: "user-1",
    conversationId: "conv-1",
    goal: "Hello there",
  };

  it("calls kortex.ai.agent.orchestrate with a snake_case AgentTask", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({
        task_id: "task-1",
        tenant_id: "tenant-1",
        status: "COMPLETED",
        final_response: "Hi!",
      }),
    );

    await sendAgentMessage(input);

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.ai.agent.orchestrate",
        parameters: {
          task: {
            task_id: "task-1",
            tenant_id: "tenant-1",
            user_id: "user-1",
            conversation_id: "conv-1",
            goal: "Hello there",
          },
        },
      }),
    );
  });

  it("maps a COMPLETED result into a typed AgentTurnResult", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({
        task_id: "task-1",
        tenant_id: "tenant-1",
        status: "COMPLETED",
        final_response: "Hi!",
        total_steps: 1,
        error_message: null,
        pending_tool_calls: [],
        degraded: false,
      }),
    );

    const result = await sendAgentMessage(input);

    expect(result).toEqual({
      taskId: "task-1",
      tenantId: "tenant-1",
      status: "COMPLETED",
      finalResponse: "Hi!",
      totalSteps: 1,
      errorMessage: null,
      pendingToolCalls: [],
      degraded: false,
    });
  });

  it("maps a PAUSED_FOR_APPROVAL result's pending_tool_calls into camelCase", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({
        task_id: "task-1",
        tenant_id: "tenant-1",
        status: "PAUSED_FOR_APPROVAL",
        final_response: null,
        pending_tool_calls: [
          { call_id: "call-1", tool_name: "create_order", arguments: { item: "Laptop" } },
        ],
      }),
    );

    const result = await sendAgentMessage(input);

    expect(result.status).toBe("PAUSED_FOR_APPROVAL");
    expect(result.pendingToolCalls).toEqual([
      { callId: "call-1", toolName: "create_order", arguments: { item: "Laptop" } },
    ]);
  });

  it("throws AiChatAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(sendAgentMessage(input)).rejects.toBeInstanceOf(AiChatAccessDeniedError);
  });

  it("throws AiChatRequestError on any other failure category", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("SERVICE_UNAVAILABLE", "backend unreachable"));

    await expect(sendAgentMessage(input)).rejects.toBeInstanceOf(AiChatRequestError);
  });
});

describe("getAgentStatus", () => {
  it("calls kortex.ai.agent.status with task_id/tenant_id", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({
        task: { task_id: "task-1", tenant_id: "tenant-1", conversation_id: "conv-1" },
        status: "COMPLETED",
      }),
    );

    await getAgentStatus("task-1", "tenant-1");

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.ai.agent.status",
        parameters: { task_id: "task-1", tenant_id: "tenant-1" },
      }),
    );
  });

  it("maps a persisted task record into a typed AgentTaskSnapshot", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope({
        task: { task_id: "task-1", tenant_id: "tenant-1", conversation_id: "conv-1" },
        status: "PAUSED_FOR_APPROVAL",
      }),
    );

    const snapshot = await getAgentStatus("task-1", "tenant-1");

    expect(snapshot).toEqual({
      taskId: "task-1",
      tenantId: "tenant-1",
      status: "PAUSED_FOR_APPROVAL",
      conversationId: "conv-1",
    });
  });

  it("returns null when the backend returns no record", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(null));

    expect(await getAgentStatus("task-1", "tenant-1")).toBeNull();
  });
});

describe("getConversationHistory", () => {
  it("calls kortex.ai.conversation.history.get with tenant_id/conversation_id", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await getConversationHistory("tenant-1", "conv-1");

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({
        capabilityName: "kortex.ai.conversation.history.get",
        parameters: { tenant_id: "tenant-1", conversation_id: "conv-1" },
      }),
    );
  });

  it("maps raw ConversationTurn entries into camelCase, oldest first", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          sequence: 1,
          user_content: "Hello",
          assistant_content: "Hi there",
          request_id: "req-1",
          user_id: "user-1",
          created_at: "2026-01-01T00:00:00Z",
        },
      ]),
    );

    const turns = await getConversationHistory("tenant-1", "conv-1");

    expect(turns).toEqual([
      { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
    ]);
  });

  it("maps a missing/non-array result to an empty array", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope(null));

    expect(await getConversationHistory("tenant-1", "conv-1")).toEqual([]);
  });

  it("throws AiChatAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(getConversationHistory("tenant-1", "conv-1")).rejects.toBeInstanceOf(AiChatAccessDeniedError);
  });
});
