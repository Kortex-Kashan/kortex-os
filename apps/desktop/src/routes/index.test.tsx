import { act, render, screen } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_APPLICATIONS } from "@/workspace/defaultApps";

import { router } from "./index";

// Exercises the real, singleton `router` exported by routes/index.tsx —
// not a hand-built test router — so this file is the one place that
// actually proves the production SessionProvider > AuthProvider > AuthGate
// > WorkspaceProvider > PanelProvider > DesktopShell composition (plus the
// dev/not-found routes) is wired correctly end to end. Navigation
// *behavior* itself (URL <-> workspace sync, deep-link restore, etc.)
// already has thorough dedicated coverage in navigation/
// navigationBridge.test.tsx and navigation/applicationRoutes.test.tsx
// against isolated test routers — this file does not re-test that, only
// that the real router assembles the same pieces the same way.
//
// AuthGate (M4.1) now sits between SessionProvider and WorkspaceProvider,
// so mounting the shell requires authentication to resolve first — mocked
// here as an already-valid stored session (`has_session` true, the
// session-check capability call SUCCESS) so these tests can still assert
// on the authenticated shell itself, which is what this file exists to
// cover. The unauthenticated path (login screen) is covered by
// `auth/AuthGate.test.tsx` and `auth/LoginScreen.test.tsx`.
const { invokeMock } = vi.hoisted(() => ({
  invokeMock: vi.fn((command: string) => {
    if (command === "has_session") {
      return Promise.resolve(true);
    }
    if (command === "invoke_capability") {
      return Promise.resolve({
        requestId: "req-1",
        correlationId: "corr-1",
        status: "SUCCESS",
        payload: null,
        errors: [],
        warnings: [],
        executionDurationMs: 1,
        httpStatus: 200,
      });
    }
    return Promise.resolve(false);
  }),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn().mockResolvedValue(() => {}) }));

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
  it("mounts the desktop shell through the full provider stack at the workspace root", async () => {
    render(<RouterProvider router={router} />);

    expect(await screen.findByText("KORTEX OS")).toBeInTheDocument();
    expect(screen.getByText("No application mounted")).toBeInTheDocument();
  });

  it("renders the login screen, never the shell, through the real singleton router when no session is stored", async () => {
    invokeMock.mockImplementationOnce(() => Promise.resolve(false));

    render(<RouterProvider router={router} />);

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("KORTEX OS")).not.toBeInTheDocument();
  });

  it("routes a default application through the real singleton router and renders its content", async () => {
    render(<RouterProvider router={router} />);
    await screen.findByText("KORTEX OS");
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
