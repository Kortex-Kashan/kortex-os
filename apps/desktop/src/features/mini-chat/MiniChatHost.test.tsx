import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
