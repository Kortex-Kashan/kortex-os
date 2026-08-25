import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { WorkspaceProvider } from "@/workspace/WorkspaceProvider";

import { DesktopShell } from "./DesktopShell";
import { WorkspaceEmptyState } from "./Workspace";

// AppSidebar (M2.3) reads useWorkspace(), so the shell needs the same
// WorkspaceProvider wrapping it gets in the real router (routes/index.tsx).
function renderShell() {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <WorkspaceProvider>
            <DesktopShell />
          </WorkspaceProvider>
        ),
        children: [{ index: true, element: <WorkspaceEmptyState /> }],
      },
    ],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("DesktopShell", () => {
  it("renders the top bar, sidebar navigation, workspace, and status bar together", () => {
    renderShell();

    expect(screen.getByText("KORTEX OS")).toBeInTheDocument();
    expect(screen.getByText("Core")).toBeInTheDocument();
    expect(screen.getByText("Intelligence")).toBeInTheDocument();
    expect(screen.getByText("No application mounted")).toBeInTheDocument();
    expect(screen.getByText("Local-first")).toBeInTheDocument();
  });

  it("renders without error when the dark theme class is applied", () => {
    // jsdom doesn't load compiled CSS, so this can't assert computed
    // colors — it verifies the token-driven tree mounts cleanly under
    // the `.dark` class, the same mechanism useThemeSync toggles at
    // runtime. Full visual dark-mode verification happens in-browser.
    document.documentElement.classList.add("dark");
    try {
      renderShell();
      expect(screen.getByText("KORTEX OS")).toBeInTheDocument();
      expect(screen.getByText("No application mounted")).toBeInTheDocument();
    } finally {
      document.documentElement.classList.remove("dark");
    }
  });
});
