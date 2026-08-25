import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  Sidebar,
  SidebarContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "./sidebar";

describe("Sidebar", () => {
  it("expands by default and collapses to a narrow rail via the trigger", () => {
    render(
      <SidebarProvider>
        <SidebarTrigger />
        <Sidebar data-testid="sidebar">
          <SidebarContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton>Dashboard</SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarContent>
        </Sidebar>
      </SidebarProvider>,
    );

    const sidebar = screen.getByTestId("sidebar");
    expect(sidebar).toHaveClass("w-64");

    fireEvent.click(screen.getByRole("button", { name: "Toggle sidebar" }));

    expect(sidebar).toHaveClass("w-14");
    expect(sidebar).toHaveAttribute("data-collapsed", "true");
  });

  it("throws when a Sidebar part is used outside its provider", () => {
    // Suppress the expected React error-boundary console noise for this assertion.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Sidebar>content</Sidebar>)).toThrow(
      "useSidebar must be used within a SidebarProvider",
    );
    spy.mockRestore();
  });
});
