import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/auth/AuthProvider";
import { WorkspaceProvider } from "@/workspace/WorkspaceProvider";

import { DesktopShell } from "./DesktopShell";
import { WorkspaceEmptyState } from "./Workspace";

// TopBar reads useAuth() (M4.1), so the shell needs an AuthProvider
// ancestor too — its startup check calls the real `@tauri-apps/api/core`
// `invoke` (`has_session`), which reaches into `window.__TAURI_INTERNALS__`
// (absent outside a real Tauri webview), so it's mocked here the same way
// `app/App.test.tsx` mocks it.
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn().mockResolvedValue(false) }));

// Mini Chat (mounted inside DesktopShell) fetches durable conversation
// history via `useConversation` on mount -- mocked the same way
// `ChatPanel.test.tsx`/`MiniChatHost.test.tsx` already mock this exact,
// real, existing module, so DesktopShell's own tests never depend on an
// unmocked Tauri IPC round trip.
const { getConversationHistoryMock } = vi.hoisted(() => ({ getConversationHistoryMock: vi.fn() }));
vi.mock("@/features/ai-studio/chat-api", async () => {
  const actual = await vi.importActual<typeof import("@/features/ai-studio/chat-api")>(
    "@/features/ai-studio/chat-api",
  );
  return { ...actual, getConversationHistory: getConversationHistoryMock };
});

beforeEach(() => {
  getConversationHistoryMock.mockReset();
  getConversationHistoryMock.mockResolvedValue([]);
});

// AppSidebar (M2.3) reads useWorkspace(), so the shell needs the same
// WorkspaceProvider wrapping it gets in the real router (routes/index.tsx).
// QueryClientProvider mirrors `app/App.tsx`'s real root position (above the
// router) -- DesktopShell itself never needed one until Mini Chat, mounted
// inside it, started using `useConversation`'s `useQuery`.
function renderShell() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <QueryClientProvider client={client}>
            <AuthProvider>
              <WorkspaceProvider>
                <DesktopShell />
              </WorkspaceProvider>
            </AuthProvider>
          </QueryClientProvider>
        ),
        children: [{ index: true, element: <WorkspaceEmptyState /> }],
      },
    ],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("DesktopShell", () => {
  it("renders the top bar, sidebar navigation, workspace, and status bar together", async () => {
    renderShell();

    expect(await screen.findByText("KORTEX OS")).toBeInTheDocument();
    expect(screen.getByText("Core")).toBeInTheDocument();
    expect(screen.getByText("Intelligence")).toBeInTheDocument();
    expect(screen.getByText("No application mounted")).toBeInTheDocument();
    expect(screen.getByText("Local-first")).toBeInTheDocument();
  });

  it("renders without error when the dark theme class is applied", async () => {
    // jsdom doesn't load compiled CSS, so this can't assert computed
    // colors — it verifies the token-driven tree mounts cleanly under
    // the `.dark` class, the same mechanism useThemeSync toggles at
    // runtime. Full visual dark-mode verification happens in-browser.
    document.documentElement.classList.add("dark");
    try {
      renderShell();
      expect(await screen.findByText("KORTEX OS")).toBeInTheDocument();
      expect(screen.getByText("No application mounted")).toBeInTheDocument();
    } finally {
      document.documentElement.classList.remove("dark");
    }
  });
});
