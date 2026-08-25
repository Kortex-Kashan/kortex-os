import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { SidebarProvider } from "@kortex/design-system";

import { buildApplicationRoutes } from "@/navigation/applicationRoutes";
import { WorkspaceNavigationSync } from "@/navigation/navigationBridge";
import { WorkspaceProvider } from "@/workspace/WorkspaceProvider";
import type { WorkspaceApplication } from "@/workspace/workspaceTypes";

import { AppSidebar } from "./AppSidebar";
import { NAV_GROUPS } from "./navConfig";

function makeApps(): WorkspaceApplication[] {
  return [
    {
      id: "dashboard",
      name: "Dashboard",
      description: "Dashboard app",
      icon: () => null,
      route: "/dashboard",
      component: () => <div>Dashboard content</div>,
      permissions: [],
    },
    {
      id: "ai-studio",
      name: "AI Studio",
      description: "AI Studio app",
      icon: () => null,
      route: "/ai-studio",
      component: () => <div>AI Studio content</div>,
      permissions: [],
    },
  ];
}

function renderSidebar(applications: WorkspaceApplication[] = makeApps(), initialEntries = ["/"]) {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <WorkspaceProvider initialApplications={applications}>
            <WorkspaceNavigationSync />
            <SidebarProvider>
              <AppSidebar />
            </SidebarProvider>
          </WorkspaceProvider>
        ),
        children: buildApplicationRoutes(applications),
      },
    ],
    { initialEntries },
  );
  return render(<RouterProvider router={router} />);
}

describe("AppSidebar", () => {
  it("renders every registered workspace application as an enabled item", () => {
    renderSidebar();

    for (const app of makeApps()) {
      expect(screen.getByRole("button", { name: app.name })).toBeEnabled();
    }
  });

  it("activates an application and marks it current when clicked", () => {
    renderSidebar();

    const dashboardButton = screen.getByRole("button", { name: "Dashboard" });
    expect(dashboardButton).not.toHaveAttribute("aria-current");

    fireEvent.click(dashboardButton);

    expect(dashboardButton).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "AI Studio" })).not.toHaveAttribute("aria-current");
  });

  it("switches the active item when a different application is clicked", () => {
    renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: "Dashboard" }));
    fireEvent.click(screen.getByRole("button", { name: "AI Studio" }));

    expect(screen.getByRole("button", { name: "AI Studio" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
  });

  it("marks the application matching the initial URL as active on load", () => {
    renderSidebar(makeApps(), ["/ai-studio"]);

    expect(screen.getByRole("button", { name: "AI Studio" })).toHaveAttribute("aria-current", "page");
  });

  it("still renders every other nav group and item as a disabled placeholder", () => {
    renderSidebar();

    for (const group of NAV_GROUPS) {
      expect(screen.getByText(group.label)).toBeInTheDocument();
      for (const item of group.items) {
        expect(screen.getByRole("button", { name: item.label })).toBeDisabled();
      }
    }
  });

  it("collapses via the sidebar trigger", () => {
    renderSidebar();

    const trigger = screen.getByRole("button", { name: "Toggle sidebar" });
    const sidebar = trigger.closest("aside");
    expect(sidebar).toHaveClass("w-64");

    fireEvent.click(trigger);

    expect(sidebar).toHaveClass("w-14");
  });
});
