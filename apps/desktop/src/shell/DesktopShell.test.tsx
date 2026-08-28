import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

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

// AppSidebar (M2.3) reads useWorkspace(), so the shell needs the same
// WorkspaceProvider wrapping it gets in the real router (routes/index.tsx).
function renderShell() {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <AuthProvider>
            <WorkspaceProvider>
              <DesktopShell />
            </WorkspaceProvider>
          </AuthProvider>
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
