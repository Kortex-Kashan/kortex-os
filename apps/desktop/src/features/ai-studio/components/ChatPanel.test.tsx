import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { getConversationHistoryMock, sendAgentMessageMock, getAgentStatusMock, navigateToApplicationMock } =
  vi.hoisted(() => ({
    getConversationHistoryMock: vi.fn(),
    sendAgentMessageMock: vi.fn(),
    getAgentStatusMock: vi.fn(),
    navigateToApplicationMock: vi.fn(),
  }));

vi.mock("../chat-api", async () => {
  const actual = await vi.importActual<typeof import("../chat-api")>("../chat-api");
  return {
    ...actual,
    getConversationHistory: getConversationHistoryMock,
    sendAgentMessage: sendAgentMessageMock,
    getAgentStatus: getAgentStatusMock,
  };
});

vi.mock("@/navigation/navigationBridge", () => ({
  useApplicationNavigation: () => ({
    state: { applicationId: "ai-studio", route: "/ai-studio" },
    navigateToApplication: navigateToApplicationMock,
  }),
}));

import { ChatPanel } from "./ChatPanel";

beforeEach(() => {
  getConversationHistoryMock.mockReset();
  sendAgentMessageMock.mockReset();
  getAgentStatusMock.mockReset();
  navigateToApplicationMock.mockReset();
  window.localStorage.clear();
});

function renderChatPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel tenantId="tenant-1" userId="user-1" />
    </QueryClientProvider>,
  );
}

describe("ChatPanel", () => {
  it("shows a loading state, then an empty-transcript message once history resolves", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);

    renderChatPanel();

    expect(screen.getByLabelText("Loading conversation")).toBeInTheDocument();
    expect(await screen.findByText("No messages yet. Say hello to get started.")).toBeInTheDocument();
  });

  it("hydrates the transcript from durable conversation history", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([
      { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
    ]);

    renderChatPanel();

    expect(await screen.findByText("Hello")).toBeInTheDocument();
    expect(screen.getByText("Hi there")).toBeInTheDocument();
  });

  it("sends a message on submit and renders the COMPLETED reply", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);
    sendAgentMessageMock.mockImplementationOnce((input: { taskId: string; tenantId: string }) =>
      Promise.resolve({
        taskId: input.taskId,
        tenantId: input.tenantId,
        status: "COMPLETED",
        finalResponse: "Hi! How can I help?",
        totalSteps: 1,
        errorMessage: null,
        pendingToolCalls: [],
        degraded: false,
      }),
    );

    renderChatPanel();
    await screen.findByText("No messages yet. Say hello to get started.");

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Hello")).toBeInTheDocument();
    expect(await screen.findByText("Hi! How can I help?")).toBeInTheDocument();
  });

  it("renders a pending-approval card on PAUSED_FOR_APPROVAL and disables the composer", async () => {
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
    getAgentStatusMock.mockResolvedValue({
      taskId: "irrelevant",
      tenantId: "tenant-1",
      status: "PAUSED_FOR_APPROVAL",
      conversationId: "conv-1",
    });

    renderChatPanel();
    await screen.findByText("No messages yet. Say hello to get started.");

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Order a laptop" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // "Order a laptop" appears twice: the optimistic user bubble, and the
    // pending-approval card's own title (it shows the task's goal).
    await waitFor(() => expect(screen.getAllByText("Order a laptop")).toHaveLength(2));
    expect(screen.getByRole("button", { name: "Review & Decide" })).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("navigates to the Workflow approvals tab when Review & Decide is clicked", async () => {
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
    getAgentStatusMock.mockResolvedValue({
      taskId: "irrelevant",
      tenantId: "tenant-1",
      status: "PAUSED_FOR_APPROVAL",
      conversationId: "conv-1",
    });

    renderChatPanel();
    await screen.findByText("No messages yet. Say hello to get started.");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Order a laptop" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const reviewButton = await screen.findByRole("button", { name: "Review & Decide" });
    fireEvent.click(reviewButton);

    expect(navigateToApplicationMock).toHaveBeenCalledWith({
      applicationId: "workflow-engine",
      search: "?tab=approvals",
    });
  });

  it("resolves a pending approval card once status polling observes COMPLETED, using the latest durable turn", async () => {
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
    getAgentStatusMock.mockResolvedValue({
      taskId: "irrelevant",
      tenantId: "tenant-1",
      status: "COMPLETED",
      conversationId: "conv-1",
    });
    getConversationHistoryMock.mockResolvedValueOnce([
      {
        sequence: 1,
        userContent: "Order a laptop",
        assistantContent: "Order created successfully.",
        createdAt: "2026-01-01T00:00:00Z",
      },
    ]);

    renderChatPanel();
    await screen.findByText("No messages yet. Say hello to get started.");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Order a laptop" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByText("Order created successfully.")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Review & Decide" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Message")).not.toBeDisabled();
  });

  it("appends a system notice, not a crash, when sendAgentMessage rejects", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);
    sendAgentMessageMock.mockRejectedValueOnce(new Error("backend unreachable"));

    renderChatPanel();
    await screen.findByText("No messages yet. Say hello to get started.");

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("backend unreachable")).toBeInTheDocument();
  });
});
