import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@kortex/design-system";

import { AuthProvider } from "@/auth/AuthProvider";

import { TopBar } from "./TopBar";

// TopBar now reads `useAuth()` (M4.1) for the identity label/sign-out
// action, which requires an AuthProvider ancestor. AuthProvider's startup
// check calls the real `@tauri-apps/api/core` `invoke` (`has_session`),
// which reaches into `window.__TAURI_INTERNALS__` — absent outside a real
// Tauri webview — so it is mocked here the same way `app/App.test.tsx`
// mocks it for `useKortexEventStream`.
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn().mockResolvedValue(false) }));

// TopBar's search trigger uses Tooltip, which requires a TooltipProvider
// ancestor — in the real app DesktopShell provides one once for the whole
// shell; standalone tests need to supply the same context.
function renderTopBar() {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <AuthProvider>
            <TooltipProvider>
              <TopBar />
            </TooltipProvider>
          </AuthProvider>
        ),
      },
    ],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("TopBar", () => {
  it("opens the command palette from the search trigger", async () => {
    renderTopBar();

    expect(screen.queryByPlaceholderText("Type a command or search...")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /search/i }));

    expect(await screen.findByPlaceholderText("Type a command or search...")).toBeInTheDocument();
  });

  it("opens the command palette via Ctrl+K", async () => {
    renderTopBar();

    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    expect(await screen.findByPlaceholderText("Type a command or search...")).toBeInTheDocument();
  });

  it("lists navigation groups as disabled placeholders in the command palette", async () => {
    renderTopBar();

    fireEvent.click(screen.getByRole("button", { name: /search/i }));
    await screen.findByPlaceholderText("Type a command or search...");

    expect(screen.getByText("Kernel")).toBeInTheDocument();
    expect(screen.getByText("Knowledge")).toBeInTheDocument();
  });

  it("shows the user menu placeholder with a working theme toggle", async () => {
    renderTopBar();

    // Radix's DropdownMenuTrigger opens on pointerdown (not click) for
    // mouse input — see the equivalent note in the design-system's own
    // dropdown-menu.test.tsx.
    fireEvent.pointerDown(screen.getByRole("button", { name: "User menu" }), {
      button: 0,
      pointerId: 1,
    });

    // Not authenticated in this standalone render (no login flow was run),
    // so the identity label falls back to a generic placeholder — see
    // `TopBar.tsx`'s `identityLabel`. `AuthGate.test.tsx`/
    // `AuthProvider.test.tsx` cover the real principal_id being shown once
    // AUTHENTICATED.
    expect(await screen.findByText("Signed in")).toBeInTheDocument();
    expect(screen.getByText("Switch to dark theme")).toBeInTheDocument();
    expect(screen.getByText("Profile").closest("[role=menuitem]")).toHaveAttribute(
      "data-disabled",
    );
    expect(screen.getByText("Sign out").closest("[role=menuitem]")).not.toHaveAttribute(
      "data-disabled",
    );
  });
});
