import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { TooltipProvider } from "@kortex/design-system";

import { TopBar } from "./TopBar";

// TopBar's search trigger uses Tooltip, which requires a TooltipProvider
// ancestor — in the real app DesktopShell provides one once for the whole
// shell; standalone tests need to supply the same context.
function renderTopBar() {
  const router = createMemoryRouter(
    [{ path: "/", element: <TooltipProvider><TopBar /></TooltipProvider> }],
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

    expect(await screen.findByText("Guest")).toBeInTheDocument();
    expect(screen.getByText("Switch to dark theme")).toBeInTheDocument();
    expect(screen.getByText("Profile").closest("[role=menuitem]")).toHaveAttribute(
      "data-disabled",
    );
  });
});
