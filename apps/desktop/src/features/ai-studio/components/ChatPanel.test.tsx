import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  getConversationHistoryMock,
  sendAgentMessageMock,
  getAgentStatusMock,
  listConversationsMock,
  navigateToApplicationMock,
} = vi.hoisted(() => ({
  getConversationHistoryMock: vi.fn(),
  sendAgentMessageMock: vi.fn(),
  getAgentStatusMock: vi.fn(),
  listConversationsMock: vi.fn(),
  navigateToApplicationMock: vi.fn(),
}));

vi.mock("../chat-api", async () => {
  const actual = await vi.importActual<typeof import("../chat-api")>("../chat-api");
  return {
    ...actual,
    getConversationHistory: getConversationHistoryMock,
    sendAgentMessage: sendAgentMessageMock,
    getAgentStatus: getAgentStatusMock,
    listConversations: listConversationsMock,
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
  listConversationsMock.mockReset();
  listConversationsMock.mockResolvedValue([]);
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

  // ---------------------------------------------------------------------------
  // Phase B: fixed layout -- the transcript is the only scroll container,
  // the composer lives outside it.
  // ---------------------------------------------------------------------------

  it("keeps the message transcript as the scroll container, with the composer outside it", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([
      { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
    ]);

    renderChatPanel();
    await screen.findByText("Hello");

    const log = screen.getByRole("log", { name: "Chat transcript" });
    const textarea = screen.getByLabelText("Message");
    const sendButton = screen.getByRole("button", { name: "Send" });

    // The composer is not part of the scrolling transcript -- it must stay
    // reachable/visible regardless of how far the transcript itself scrolls.
    expect(log.contains(textarea)).toBe(false);
    expect(log.contains(sendButton)).toBe(false);
  });

  // ---------------------------------------------------------------------------
  // Phase B: composer responsiveness -- typing stays available while a
  // response is generating; only Send is blocked until it resolves.
  // ---------------------------------------------------------------------------

  it("keeps the textarea usable while a response is generating, and re-enables Send once it resolves", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);
    let resolveSend!: (value: unknown) => void;
    sendAgentMessageMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveSend = resolve;
        }),
    );

    renderChatPanel();
    await screen.findByText("No messages yet. Say hello to get started.");

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // The request is now in flight (never resolved yet) -- the previous
    // behavior disabled the textarea itself for this entire window.
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeDisabled());
    const textarea = screen.getByLabelText("Message");
    expect(textarea).not.toBeDisabled();

    fireEvent.change(textarea, { target: { value: "my next question, queued while I wait" } });
    expect(textarea).toHaveValue("my next question, queued while I wait");

    resolveSend({
      taskId: "task-1",
      tenantId: "tenant-1",
      status: "COMPLETED",
      finalResponse: "Hi! How can I help?",
      totalSteps: 1,
      errorMessage: null,
      pendingToolCalls: [],
      degraded: false,
    });

    expect(await screen.findByText("Hi! How can I help?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    // What was typed while the first request was in flight was never lost.
    expect(screen.getByLabelText("Message")).toHaveValue("my next question, queued while I wait");
  });

  // ---------------------------------------------------------------------------
  // Phase C: durable "Recent Conversations" + New Chat
  // ---------------------------------------------------------------------------

  describe("Recent Conversations", () => {
    it("renders an empty state when the tenant/user has no conversations yet", async () => {
      getConversationHistoryMock.mockResolvedValueOnce([]);
      listConversationsMock.mockResolvedValueOnce([]);

      renderChatPanel();

      expect(await screen.findByText("No conversations yet. Say hello to get started.")).toBeInTheDocument();
    });

    it("shows an explicit error state, with retry, when the list fails to load", async () => {
      getConversationHistoryMock.mockResolvedValueOnce([]);
      listConversationsMock.mockRejectedValueOnce(new Error("conversations unavailable"));

      renderChatPanel();

      expect(await screen.findByText("Could not load Recent Conversations.")).toBeInTheDocument();
      expect(screen.getByText("conversations unavailable")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    });

    it("renders conversations grouped and highlights the active one", async () => {
      getConversationHistoryMock.mockResolvedValueOnce([]);
      const now = new Date();
      const today = now.toISOString();
      // Noon on the calendar day before today, not a fixed 25h offset from
      // "now": subtracting a flat 25 hours lands TWO calendar days back
      // whenever the test happens to run within the first hour after
      // midnight, which is exactly the kind of time-of-day-dependent flake
      // this must not have.
      const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1, 12, 0, 0).toISOString();
      listConversationsMock.mockResolvedValueOnce([
        { conversationId: "active-conv", title: "Gemini discussion", firstActivityAt: today, lastActivityAt: today },
        {
          conversationId: "conv-yesterday",
          title: "Security architecture",
          firstActivityAt: yesterday,
          lastActivityAt: yesterday,
        },
      ]);
      window.localStorage.setItem("kortex.ai-studio.chat.conversation-id:tenant-1:user-1", "active-conv");

      renderChatPanel();

      expect(await screen.findByText("Today")).toBeInTheDocument();
      expect(screen.getByText("Yesterday")).toBeInTheDocument();
      const activeButton = screen.getByRole("button", { name: "Gemini discussion" });
      expect(activeButton).toHaveAttribute("aria-current", "true");
      expect(screen.getByRole("button", { name: "Security architecture" })).not.toHaveAttribute("aria-current");
    });

    it("selecting a past conversation loads its own durable transcript", async () => {
      getConversationHistoryMock.mockResolvedValueOnce([]); // initial active conversation
      listConversationsMock.mockResolvedValueOnce([
        {
          conversationId: "conv-past",
          title: "an older question",
          firstActivityAt: "2026-01-01T00:00:00Z",
          lastActivityAt: "2026-01-01T00:00:00Z",
        },
      ]);

      renderChatPanel();
      await screen.findByText("an older question");

      getConversationHistoryMock.mockResolvedValueOnce([
        {
          sequence: 1,
          userContent: "an older question",
          assistantContent: "an older answer",
          createdAt: "2026-01-01T00:00:00Z",
        },
      ]);

      fireEvent.click(screen.getByRole("button", { name: "an older question" }));

      expect(await screen.findByText("an older answer")).toBeInTheDocument();
    });

    it("New Chat clears the transcript and starts a genuinely new conversation", async () => {
      getConversationHistoryMock.mockResolvedValueOnce([
        { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
      ]);
      listConversationsMock.mockResolvedValue([]);

      renderChatPanel();
      await screen.findByText("Hello");

      fireEvent.click(screen.getByRole("button", { name: "New Chat" }));

      expect(await screen.findByText("No messages yet. Say hello to get started.")).toBeInTheDocument();
      expect(screen.queryByText("Hello")).not.toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Phase C: model switching must not disturb the active conversation
  // ---------------------------------------------------------------------------

  it("re-mounting with the same tenant/user (e.g. after a model/provider change elsewhere) preserves the conversation", async () => {
    getConversationHistoryMock.mockResolvedValue([
      { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
    ]);
    listConversationsMock.mockResolvedValue([]);

    const { unmount } = renderChatPanel();
    await screen.findByText("Hello");
    // ChatPanel/useConversation carry no coupling to provider/model state at
    // all (they are a different tab/component subtree entirely — see
    // AiStudioApp.tsx) — unmounting and remounting this component is the
    // closest a unit test can come to simulating "the user switched to a
    // different AI Studio tab and back" without a model change actually
    // being able to reach into this component's props or state.
    unmount();

    renderChatPanel();

    expect(await screen.findByText("Hello")).toBeInTheDocument();
    expect(screen.getByText("Hi there")).toBeInTheDocument();
    // The exact same conversation id is resumed, not a new one.
    expect(getConversationHistoryMock).toHaveBeenLastCalledWith(
      "tenant-1",
      window.localStorage.getItem("kortex.ai-studio.chat.conversation-id:tenant-1:user-1"),
    );
  });
});
