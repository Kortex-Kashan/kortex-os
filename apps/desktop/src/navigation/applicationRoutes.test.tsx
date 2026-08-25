import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { Workspace } from "@/shell/Workspace";
import { PanelProvider } from "@/panels/PanelProvider";
import { WorkspaceProvider } from "@/workspace/WorkspaceProvider";
import type { WorkspaceApplication } from "@/workspace/workspaceTypes";

import { buildApplicationRoutes } from "./applicationRoutes";

function makeApps(): WorkspaceApplication[] {
  return [
    {
      id: "dashboard",
      name: "Dashboard",
      description: "d",
      icon: () => null,
      route: "/dashboard",
      component: () => <div>Dashboard content</div>,
      permissions: [],
    },
    {
      id: "ai-studio",
      name: "AI Studio",
      description: "a",
      icon: () => null,
      route: "/ai-studio",
      component: () => <div>AI Studio content</div>,
      permissions: [],
    },
  ];
}

describe("buildApplicationRoutes", () => {
  it("builds one relative child route per application", () => {
    const routes = buildApplicationRoutes(makeApps());

    expect(routes.map((route) => route.path)).toEqual(["dashboard", "ai-studio"]);
    for (const route of routes) {
      expect(route.element).toBeDefined();
    }
  });

  it("returns no routes for an empty application list", () => {
    expect(buildApplicationRoutes([])).toEqual([]);
  });

  it("mounts WorkspaceView at each application's route when wired into a router", () => {
    const apps = makeApps();
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: (
            <PanelProvider>
              <WorkspaceProvider initialApplications={apps}>
                <Workspace />
              </WorkspaceProvider>
            </PanelProvider>
          ),
          children: buildApplicationRoutes(apps),
        },
      ],
      { initialEntries: ["/dashboard"] },
    );

    render(<RouterProvider router={router} />);

    // No activation logic lives in applicationRoutes.ts itself (that's
    // navigationBridge.tsx's job) — this only proves the route resolves
    // to a mounted WorkspaceView instead of a 404 or a crash.
    expect(screen.getByText("No application mounted")).toBeInTheDocument();
  });
});
