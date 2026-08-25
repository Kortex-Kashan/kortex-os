import { act, render, screen } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DEFAULT_APPLICATIONS } from "@/workspace/defaultApps";

import { router } from "./index";

// Exercises the real, singleton `router` exported by routes/index.tsx —
// not a hand-built test router — so this file is the one place that
// actually proves the production SessionProvider > WorkspaceProvider >
// PanelProvider > DesktopShell composition (plus the dev/not-found
// routes) is wired correctly end to end. Navigation *behavior* itself
// (URL <-> workspace sync, deep-link restore, etc.) already has thorough
// dedicated coverage in navigation/navigationBridge.test.tsx and
// navigation/applicationRoutes.test.tsx against isolated test routers —
// this file does not re-test that, only that the real router assembles
// the same pieces the same way.
beforeEach(async () => {
  window.localStorage.clear();
  await act(async () => {
    await router.navigate("/");
  });
});

afterEach(() => {
  window.localStorage.clear();
});

describe("routes/index provider composition", () => {
  it("mounts the desktop shell through the full provider stack at the workspace root", () => {
    render(<RouterProvider router={router} />);

    expect(screen.getByText("KORTEX OS")).toBeInTheDocument();
    expect(screen.getByText("No application mounted")).toBeInTheDocument();
  });

  it("routes a default application through the real singleton router and renders its content", async () => {
    render(<RouterProvider router={router} />);
    const [firstApp] = DEFAULT_APPLICATIONS;

    await act(async () => {
      await router.navigate(firstApp.route);
    });

    expect(screen.getByText(firstApp.description)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: firstApp.name })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("falls back to the not-found route for an unregistered path", async () => {
    render(<RouterProvider router={router} />);

    await act(async () => {
      await router.navigate("/this-route-does-not-exist");
    });

    expect(screen.getByText("404")).toBeInTheDocument();
    expect(screen.getByText("Page not found")).toBeInTheDocument();
  });

  it("includes the dev-only component gallery route only when import.meta.env.DEV is true", () => {
    const devRoute = router.routes.find((route) => route.path === "/dev/components");

    if (import.meta.env.DEV) {
      expect(devRoute).toBeDefined();
    } else {
      expect(devRoute).toBeUndefined();
    }
  });
});
