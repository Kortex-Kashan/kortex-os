import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Same jsdom scroll-geometry mocking technique as `MessageList.test.tsx`
 * (see that file's own comment for the full rationale) -- needed here too
 * because Phase D's actual claim is that `MessageList`'s scroll state
 * survives a collapse/reopen of the HOST around it, which can only be
 * proven by giving its scroll container real (mocked) geometry to move
 * within.
 */
let nextScrollDefaults = { scrollTop: 0, scrollHeight: 0, clientHeight: 0 };
const scrollState = new WeakMap<Element, { scrollTop: number; scrollHeight: number; clientHeight: number }>();

function scrollStateFor(el: Element) {
  let state = scrollState.get(el);
  if (!state) {
    state = { ...nextScrollDefaults };
    scrollState.set(el, state);
  }
  return state;
}

function setScrollMetrics(
  el: Element,
  patch: Partial<{ scrollTop: number; scrollHeight: number; clientHeight: number }>,
) {
  Object.assign(scrollStateFor(el), patch);
}

let originalScrollDescriptors: Record<string, PropertyDescriptor | undefined>;

beforeAll(() => {
  originalScrollDescriptors = {
    scrollTop: Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollTop"),
    scrollHeight: Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight"),
    clientHeight: Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight"),
  };
  Object.defineProperty(HTMLElement.prototype, "scrollTop", {
    configurable: true,
    get(this: Element) {
      return scrollStateFor(this).scrollTop;
    },
    set(this: Element, value: number) {
      scrollStateFor(this).scrollTop = value;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get(this: Element) {
      return scrollStateFor(this).scrollHeight;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get(this: Element) {
      return scrollStateFor(this).clientHeight;
    },
  });
});

afterAll(() => {
  for (const [prop, descriptor] of Object.entries(originalScrollDescriptors)) {
    if (descriptor) {
      Object.defineProperty(HTMLElement.prototype, prop, descriptor);
    }
  }
});

const {
  getConversationHistoryMock,
  sendAgentMessageMock,
  getAgentStatusMock,
  navigateToApplicationMock,
  useAuthMock,
} = vi.hoisted(() => ({
  getConversationHistoryMock: vi.fn(),
  sendAgentMessageMock: vi.fn(),
  getAgentStatusMock: vi.fn(),
  navigateToApplicationMock: vi.fn(),
  useAuthMock: vi.fn(),
}));

// Same boundary `ChatPanel.test.tsx` already mocks at -- the real, existing
// `chat-api.ts` module Mini Chat imports directly. No new/fictional API is
// introduced for these tests.
vi.mock("@/features/ai-studio/chat-api", async () => {
  const actual = await vi.importActual<typeof import("@/features/ai-studio/chat-api")>(
    "@/features/ai-studio/chat-api",
  );
  return {
    ...actual,
    getConversationHistory: getConversationHistoryMock,
    sendAgentMessage: sendAgentMessageMock,
    getAgentStatus: getAgentStatusMock,
  };
});

// `MessageBubble`'s pending-approval card navigates via this hook -- same
// mock `ChatPanel.test.tsx` uses for the identical reason.
vi.mock("@/navigation/navigationBridge", () => ({
  useApplicationNavigation: () => ({
    state: { applicationId: "mini-chat-test", route: "/" },
    navigateToApplication: navigateToApplicationMock,
  }),
}));

vi.mock("@/auth/AuthProvider", async () => {
  const actual = await vi.importActual<typeof import("@/auth/AuthProvider")>("@/auth/AuthProvider");
  return { ...actual, useAuth: useAuthMock };
});

import { AuthGate } from "@/auth/AuthGate";
import type { AuthIdentity } from "@/auth/authTypes";
import { MiniChatHost } from "./MiniChatHost";

function mockAuthenticated(identity: AuthIdentity) {
  useAuthMock.mockReturnValue({
    state: { status: "AUTHENTICATED", identity },
    login: vi.fn(),
    logout: vi.fn(),
    bootstrap: vi.fn(),
    retryConnection: vi.fn(),
    reportIpcResult: vi.fn(),
  });
}

function mockUnauthenticated() {
  useAuthMock.mockReturnValue({
    state: { status: "UNAUTHENTICATED" },
    login: vi.fn(),
    logout: vi.fn(),
    bootstrap: vi.fn(),
    retryConnection: vi.fn(),
    reportIpcResult: vi.fn(),
  });
}

const USER_A: AuthIdentity = { tenantId: "tenant-a", principalId: "user-a", principalType: "USER", roles: [] };
const USER_B: AuthIdentity = { tenantId: "tenant-b", principalId: "user-b", principalType: "USER", roles: [] };

beforeEach(() => {
  getConversationHistoryMock.mockReset();
  sendAgentMessageMock.mockReset();
  getAgentStatusMock.mockReset();
  navigateToApplicationMock.mockReset();
  useAuthMock.mockReset();
  window.localStorage.clear();
  nextScrollDefaults = { scrollTop: 0, scrollHeight: 0, clientHeight: 0 };
});

function renderMiniChat() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MiniChatHost />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// A. Shell mounting / C. Open-collapse
// ---------------------------------------------------------------------------

describe("MiniChatHost", () => {
  beforeEach(() => {
    mockAuthenticated(USER_A);
  });

  it("renders as a collapsed launcher by default", () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);

    renderMiniChat();

    expect(screen.getByRole("button", { name: "Open KORTEX AI assistant" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "KORTEX AI assistant" })).not.toBeInTheDocument();
  });

  it("opens the panel when the launcher is clicked, and collapses it back on the collapse control", async () => {
    getConversationHistoryMock.mockResolvedValue([]);

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));

    expect(await screen.findByRole("region", { name: "KORTEX AI assistant" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open KORTEX AI assistant" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse KORTEX AI assistant" }));

    expect(await screen.findByRole("button", { name: "Open KORTEX AI assistant" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "KORTEX AI assistant" })).not.toBeInTheDocument();
  });

  it("makes the collapsed panel's (and, symmetrically, the open launcher's) descendants unreachable via [inert], not merely aria-hidden", async () => {
    // Closeout code-review fix: aria-hidden + pointer-events:none alone do
    // not remove a subtree from keyboard tab order, so a keyboard user
    // could previously Tab into the invisible Composer while the panel
    // was collapsed. `inert` is the actual fix; this proves it is applied
    // to the right side at every state, not just that content is visually
    // hidden (already covered by the aria-hidden-driven role queries in
    // the tests around this one).
    getConversationHistoryMock.mockResolvedValue([]);

    renderMiniChat();
    const collapsedRegion = screen.getByRole("region", { name: "KORTEX AI assistant", hidden: true });
    expect(collapsedRegion.closest("[inert]")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
    const openRegion = await screen.findByRole("region", { name: "KORTEX AI assistant" });
    expect(openRegion.closest("[inert]")).toBeNull();
    const hiddenLauncher = screen.getByRole("button", { name: "Open KORTEX AI assistant", hidden: true });
    expect(hiddenLauncher.closest("[inert]")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Collapse KORTEX AI assistant" }));
    const reopenedLauncher = await screen.findByRole("button", { name: "Open KORTEX AI assistant" });
    expect(reopenedLauncher.closest("[inert]")).toBeNull();
  });

  it("fetches conversation history exactly once regardless of open/collapse toggling", async () => {
    getConversationHistoryMock.mockResolvedValue([]);

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
    await screen.findByRole("region", { name: "KORTEX AI assistant" });
    fireEvent.click(screen.getByRole("button", { name: "Collapse KORTEX AI assistant" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open KORTEX AI assistant" }));
    await screen.findByRole("region", { name: "KORTEX AI assistant" });

    // History is fetched once, at mount -- not once per time the panel is
    // opened. `useConversation`/`useAgentStatus` are called unconditionally
    // at the top of `MiniChatHost`, independent of `open`.
    expect(getConversationHistoryMock).toHaveBeenCalledTimes(1);
  });

  // ---------------------------------------------------------------------------
  // Phase D: the panel (and MessageList's scroll state inside it) is never
  // torn down by collapsing -- proven directly, not just inferred from the
  // history-fetch-count test above.
  // ---------------------------------------------------------------------------

  describe("scroll/state persistence across collapse and reopen", () => {
    function getLog(): HTMLElement {
      return screen.getByRole("log", { name: "Chat transcript" });
    }

    it("keeps the conversation transcript intact across a collapse and reopen", async () => {
      getConversationHistoryMock.mockResolvedValueOnce([
        { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
      ]);

      renderMiniChat();
      fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
      await screen.findByText("Hello");

      fireEvent.click(screen.getByRole("button", { name: "Collapse KORTEX AI assistant" }));
      fireEvent.click(await screen.findByRole("button", { name: "Open KORTEX AI assistant" }));

      expect(await screen.findByText("Hello")).toBeInTheDocument();
      expect(screen.getByText("Hi there")).toBeInTheDocument();
    });

    it("preserves scroll position across a collapse and reopen when the user had scrolled away from the bottom", async () => {
      getConversationHistoryMock.mockResolvedValueOnce([
        { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
      ]);

      renderMiniChat();
      fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
      await screen.findByText("Hello");

      const log = getLog();
      setScrollMetrics(log, { scrollTop: 150, scrollHeight: 2000, clientHeight: 300 });
      fireEvent.scroll(log);
      expect(screen.getByRole("button", { name: "Scroll to latest" })).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Collapse KORTEX AI assistant" }));
      fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));

      // The same log element (never unmounted) still reports the scroll
      // position the user left it at, and "Scroll to latest" is still
      // showing -- reopening must not silently snap back to the bottom.
      expect(getLog()).toBe(log);
      expect(getLog().scrollTop).toBe(150);
      expect(screen.getByRole("button", { name: "Scroll to latest" })).toBeInTheDocument();
    });

    it("stays at the latest message across a collapse and reopen when the user was following", async () => {
      getConversationHistoryMock.mockResolvedValueOnce([
        { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
      ]);
      nextScrollDefaults = { scrollTop: 0, scrollHeight: 300, clientHeight: 300 }; // fits fully -- "at the bottom"

      renderMiniChat();
      fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
      await screen.findByText("Hello");

      expect(screen.queryByRole("button", { name: "Scroll to latest" })).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Collapse KORTEX AI assistant" }));
      fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));

      // Still following -- no "Scroll to latest" affordance appears just
      // because the panel was collapsed and reopened.
      expect(screen.queryByRole("button", { name: "Scroll to latest" })).not.toBeInTheDocument();
    });

    it('"Scroll to latest" still restores follow mode after a collapse/reopen cycle', async () => {
      getConversationHistoryMock.mockResolvedValueOnce([
        { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
      ]);

      renderMiniChat();
      fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
      await screen.findByText("Hello");

      const log = getLog();
      setScrollMetrics(log, { scrollTop: 0, scrollHeight: 2000, clientHeight: 300 });
      fireEvent.scroll(log);
      fireEvent.click(screen.getByRole("button", { name: "Collapse KORTEX AI assistant" }));
      fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
      expect(screen.getByRole("button", { name: "Scroll to latest" })).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Scroll to latest" }));

      expect(getLog().scrollTop).toBe(2000);
      expect(screen.queryByRole("button", { name: "Scroll to latest" })).not.toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // D/E/F. Reuses the existing conversation hook/API for history and sending
  // ---------------------------------------------------------------------------

  it("rehydrates existing durable conversation history through the existing capability", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([
      { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
    ]);

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));

    expect(await screen.findByText("Hello")).toBeInTheDocument();
    expect(screen.getByText("Hi there")).toBeInTheDocument();
    expect(getConversationHistoryMock).toHaveBeenCalledWith("tenant-a", expect.any(String));
  });

  it("sends a message through the existing agent.orchestrate path and renders the reply", async () => {
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

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
    await screen.findByText("No messages yet. Say hello to get started.");

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Hello")).toBeInTheDocument();
    expect(await screen.findByText("Hi! How can I help?")).toBeInTheDocument();
    expect(sendAgentMessageMock).toHaveBeenCalledWith(
      expect.objectContaining({ tenantId: "tenant-a", userId: "user-a", goal: "Hello" }),
    );
  });

  // ---------------------------------------------------------------------------
  // G/H. Existing tool-call rendering and approval-pause behavior are reused
  // ---------------------------------------------------------------------------

  it("renders a pending-approval card via the existing ToolCallCard/MessageBubble machinery and disables the composer", async () => {
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
      tenantId: "tenant-a",
      status: "PAUSED_FOR_APPROVAL",
      conversationId: "conv-1",
    });

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
    await screen.findByText("No messages yet. Say hello to get started.");

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Order a laptop" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getAllByText("Order a laptop")).toHaveLength(2));
    expect(screen.getByRole("button", { name: "Review & Decide" })).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeDisabled();
  });

  // ---------------------------------------------------------------------------
  // I. Existing error handling is reused
  // ---------------------------------------------------------------------------

  it("appends a system notice, not a crash, when sending fails", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);
    sendAgentMessageMock.mockRejectedValueOnce(new Error("backend unreachable"));

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
    await screen.findByText("No messages yet. Say hello to get started.");

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("backend unreachable")).toBeInTheDocument();
  });

  it("surfaces a history-load failure inline without blocking the composer", async () => {
    getConversationHistoryMock.mockRejectedValueOnce(new Error("history unavailable"));

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));

    expect(await screen.findByText(/Could not load prior conversation history/)).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).not.toBeDisabled();
  });

  // ---------------------------------------------------------------------------
  // Phase C: Mini Chat resumes the SHARED active-conversation pointer, not a
  // fixed id of its own -- proving it was updated to the multi-conversation
  // model rather than left assuming there is only ever one conversation.
  // ---------------------------------------------------------------------------

  it("resumes whichever conversation is active in localStorage, e.g. one selected from AI Studio's Recent Conversations", async () => {
    // Simulates the user having previously selected a non-default
    // conversation in the AI Studio Chat tab's Recent Conversations panel
    // (`chat-conversation-id.ts::setActiveConversationId`) -- Mini Chat
    // must resume that SAME conversation, not silently fall back to
    // generating/using a different one of its own.
    window.localStorage.setItem(
      "kortex.ai-studio.chat.conversation-id:tenant-a:user-a",
      "conv-selected-in-ai-studio",
    );
    getConversationHistoryMock.mockResolvedValueOnce([
      {
        sequence: 1,
        userContent: "a question asked from the AI Studio tab",
        assistantContent: "its answer",
        createdAt: "2026-01-01T00:00:00Z",
      },
    ]);

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));

    expect(await screen.findByText("a question asked from the AI Studio tab")).toBeInTheDocument();
    expect(screen.getByText("its answer")).toBeInTheDocument();
    expect(getConversationHistoryMock).toHaveBeenCalledWith("tenant-a", "conv-selected-in-ai-studio");
  });

  // ---------------------------------------------------------------------------
  // Phase B regression coverage: Mini Chat reuses the exact same MessageList
  // (scroll container + auto-scroll) and Composer (typing-vs-sending split)
  // components ChatPanel.tsx exercises directly -- no separate
  // implementation to verify or keep in sync here.
  // ---------------------------------------------------------------------------

  it("keeps the transcript as the scroll container, with the composer outside it", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([
      { sequence: 1, userContent: "Hello", assistantContent: "Hi there", createdAt: "2026-01-01T00:00:00Z" },
    ]);

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
    await screen.findByText("Hello");

    const log = screen.getByRole("log", { name: "Chat transcript" });
    const textarea = screen.getByLabelText("Message");
    const sendButton = screen.getByRole("button", { name: "Send" });

    expect(log.contains(textarea)).toBe(false);
    expect(log.contains(sendButton)).toBe(false);
  });

  it("keeps the textarea usable while a response is generating, same as the AI Studio Chat tab", async () => {
    getConversationHistoryMock.mockResolvedValueOnce([]);
    let resolveSend!: (value: unknown) => void;
    sendAgentMessageMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveSend = resolve;
        }),
    );

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
    await screen.findByText("No messages yet. Say hello to get started.");

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeDisabled());
    const textarea = screen.getByLabelText("Message");
    expect(textarea).not.toBeDisabled();

    fireEvent.change(textarea, { target: { value: "queued follow-up" } });
    expect(textarea).toHaveValue("queued follow-up");

    resolveSend({
      taskId: "task-1",
      tenantId: "tenant-a",
      status: "COMPLETED",
      finalResponse: "Hi! How can I help?",
      totalSteps: 1,
      errorMessage: null,
      pendingToolCalls: [],
      degraded: false,
    });

    expect(await screen.findByText("Hi! How can I help?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });
});

// ---------------------------------------------------------------------------
// B. Route persistence -- Mini Chat survives Workspace route navigation
// ---------------------------------------------------------------------------

describe("MiniChatHost route persistence", () => {
  beforeEach(() => {
    mockAuthenticated(USER_A);
  });

  it("stays mounted (open state preserved) across a Workspace route change", async () => {
    getConversationHistoryMock.mockResolvedValue([]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    // Mirrors DesktopShell's real shape: a layout route renders MiniChatHost
    // as a persistent sibling of `<Outlet/>`, so only the outlet's content
    // changes as the child route changes -- MiniChatHost itself is never
    // inside the part of the tree react-router swaps out.
    function Layout() {
      return (
        <QueryClientProvider client={client}>
          <div>
            <div id="workspace-outlet">
              <Outlet />
            </div>
            <MiniChatHost />
          </div>
        </QueryClientProvider>
      );
    }

    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <Layout />,
          children: [
            { path: "a", element: <div>Current route: /a</div> },
            { path: "b", element: <div>Current route: /b</div> },
          ],
        },
      ],
      { initialEntries: ["/a"] },
    );

    render(<RouterProvider router={router} />);
    await screen.findByText("Current route: /a");

    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
    expect(await screen.findByRole("region", { name: "KORTEX AI assistant" })).toBeInTheDocument();
    // History must not be re-fetched merely because the route changed.
    expect(getConversationHistoryMock).toHaveBeenCalledTimes(1);

    router.navigate("/b");
    await screen.findByText("Current route: /b");

    // Still open: if MiniChatHost had unmounted/remounted, its local
    // `open` state would have reset to its `false` default.
    expect(screen.getByRole("region", { name: "KORTEX AI assistant" })).toBeInTheDocument();
    expect(getConversationHistoryMock).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// K. AI Workflow Builder entry point (AI Workflow Builder milestone;
// retargeted by AI Studio functional stabilization Phase E)
//
// Mini Chat renders no Workflow Builder UI of its own and calls no builder
// API -- it is a pure navigation trigger onto the Workflow Engine app's "AI
// Automation" tab (the same `WorkflowBuilderPanel`/`useWorkflowBuilder`,
// relocated there from AI Studio in Phase E). These tests prove exactly
// that: the button exists, is reachable once the panel is open, and its
// only effect is one call to the existing `navigateToApplication` bridge
// with the deep-link `WorkflowApp.tsx`'s own `?tab=` reader consumes --
// never a second builder implementation, never a draft/publish call of its
// own.
// ---------------------------------------------------------------------------

describe("MiniChatHost Workflow Builder entry point", () => {
  beforeEach(() => {
    mockAuthenticated(USER_A);
  });

  it("renders a Workflow Builder entry point once the panel is open", async () => {
    getConversationHistoryMock.mockResolvedValue([]);

    renderMiniChat();
    expect(screen.queryByRole("button", { name: "Open AI Workflow Builder" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));

    expect(await screen.findByRole("button", { name: "Open AI Workflow Builder" })).toBeInTheDocument();
  });

  it("activating the entry point deep-links to the Workflow Engine app's AI Automation tab, and nothing else", async () => {
    getConversationHistoryMock.mockResolvedValue([]);

    renderMiniChat();
    fireEvent.click(screen.getByRole("button", { name: "Open KORTEX AI assistant" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open AI Workflow Builder" }));

    expect(navigateToApplicationMock).toHaveBeenCalledTimes(1);
    expect(navigateToApplicationMock).toHaveBeenCalledWith({
      applicationId: "workflow-engine",
      search: "?tab=aiAutomation",
    });
    // Reuse, not duplication: Mini Chat never renders builder content of its own
    // (no node list, no "Create Draft"/"Approve & Publish" controls here).
    expect(screen.queryByTestId("ai-workflow-builder-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("builder-create-draft-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("builder-approve-publish-button")).not.toBeInTheDocument();
  });

  it("MiniChatHost.tsx source never imports the builder hook/API/panel -- navigation only, never a second implementation", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const source = fs.readFileSync(path.resolve(__dirname, "MiniChatHost.tsx"), "utf-8");

    for (const forbidden of [
      "ai-automation/hooks/useWorkflowBuilder",
      "ai-automation/api",
      "ai-automation/components/WorkflowBuilderPanel",
      "generateWorkflowProposal",
      "createWorkflowDraft",
      "publishWorkflowDraft",
    ]) {
      expect(source).not.toContain(forbidden);
    }
    // The only Workflow-Builder-adjacent thing this file may reference is the
    // navigation deep-link itself.
    expect(source).toContain("navigateToApplication");
  });
});

// ---------------------------------------------------------------------------
// J. Identity transition -- the security-critical regression
// ---------------------------------------------------------------------------
//
// Renders through the REAL AuthGate component (only `useAuth` and the
// non-shell screens are mocked, exactly as `AuthGate.test.tsx` already
// does) -- proving the actual lifecycle abstraction the application ships,
// not a simulated prop change on an already-mounted component.

vi.mock("@/auth/LoginScreen", () => ({ LoginScreen: () => <div>LOGIN SCREEN MARKER</div> }));
vi.mock("@/auth/BootstrapScreen", () => ({ BootstrapScreen: () => <div>BOOTSTRAP SCREEN MARKER</div> }));
vi.mock("@/auth/BackendUnavailableScreen", () => ({
  BackendUnavailableScreen: () => <div>BACKEND UNAVAILABLE SCREEN MARKER</div>,
}));

describe("MiniChatHost identity lifecycle", () => {
  it("destroys user A's in-memory transcript on logout, and user B never sees it after logging in", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    mockAuthenticated(USER_A);
    getConversationHistoryMock.mockResolvedValueOnce([]);
    sendAgentMessageMock.mockImplementationOnce((input: { taskId: string; tenantId: string }) =>
      Promise.resolve({
        taskId: input.taskId,
        tenantId: input.tenantId,
        status: "COMPLETED",
        finalResponse: "Reply for A",
        totalSteps: 1,
        errorMessage: null,
        pendingToolCalls: [],
        degraded: false,
      }),
    );

    const { rerender } = render(
      <QueryClientProvider client={client}>
        <AuthGate>
          <MiniChatHost />
        </AuthGate>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Open KORTEX AI assistant" }));
    await screen.findByText("No messages yet. Say hello to get started.");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Secret A message" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Secret A message")).toBeInTheDocument();
    expect(await screen.findByText("Reply for A")).toBeInTheDocument();

    // Logout: AuthGate's own, real conditional now renders the login
    // screen instead of `children` -- Mini Chat and everything in it is
    // unmounted, not merely hidden.
    mockUnauthenticated();
    rerender(
      <QueryClientProvider client={client}>
        <AuthGate>
          <MiniChatHost />
        </AuthGate>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("LOGIN SCREEN MARKER")).toBeInTheDocument();
    expect(screen.queryByText("Secret A message")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open KORTEX AI assistant" })).not.toBeInTheDocument();

    // User B logs in: a fresh Mini Chat instance mounts for a different
    // identity. Its own (empty) history is fetched independently.
    mockAuthenticated(USER_B);
    getConversationHistoryMock.mockResolvedValueOnce([]);
    rerender(
      <QueryClientProvider client={client}>
        <AuthGate>
          <MiniChatHost />
        </AuthGate>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Open KORTEX AI assistant" }));

    expect(await screen.findByText("No messages yet. Say hello to get started.")).toBeInTheDocument();
    expect(screen.queryByText("Secret A message")).not.toBeInTheDocument();
    expect(screen.queryByText("Reply for A")).not.toBeInTheDocument();
    // The fresh instance requests its OWN tenant's history, not tenant A's.
    expect(getConversationHistoryMock).toHaveBeenLastCalledWith("tenant-b", expect.any(String));
  });
});
