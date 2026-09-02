import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_APPLICATIONS } from "@/workspace/defaultApps";

import { router } from "./index";

// The real singleton `router` has no QueryClientProvider of its own — that
// wrapping lives one level up, in `app/App.tsx` (App -> QueryClientProvider
// -> RouterProvider -> router), which this file deliberately does not
// render (see the module doc above: it exercises the router's own provider
// composition, not App.tsx's). Every default application is now a real,
// `useQuery`-backed feature (Slice 4.6 made AI Studio the last one), so
// mounting the router here needs its own local QueryClientProvider — a
// fresh, `retry: false` client per render, matching every other feature
// test's own convention, not the shared production `queryClient` singleton.
function renderRouter() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

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
//
// M7.1: `AuthProvider`'s startup effect now gates all of the above behind
// a real (bounded-retry) `get_system_health` poll first (`backendReadiness.ts`).
// Without a `get_system_health` case here, that command falls through to
// this mock's `Promise.resolve(false)` default — a malformed
// `SystemHealthOutcome` whose `.ok` is `undefined` — so every attempt
// reads as "not ready" and the real ~19s bounded backoff runs to
// exhaustion before these tests' `findBy*` queries ever see anything,
// timing out. Mocked here as an immediately-healthy, already-bootstrapped
// backend so these tests can still assert on the authenticated-shell/
// login-screen split they exist to cover, exactly as before M7.1. The
// bootstrap-required path (M7.1's own first-run screen) is covered by
// `auth/AuthGate.test.tsx`, `auth/AuthProvider.test.tsx`, and
// `auth/BootstrapScreen.test.tsx`, not here.
// `hasSessionResponse` is mutable per-test state (reset in `beforeEach`
// below), read by the single shared `invokeMock` implementation — a test
// that needs the no-stored-session path sets it to `false` rather than
// replacing `invokeMock`'s implementation outright. `mockImplementation`
// (unlike `mockImplementationOnce`) replaces it *permanently* until
// something else replaces it again; with no reset between tests in this
// file, an earlier test calling `mockImplementation` would leak into every
// test that runs after it — this pattern avoids that trap entirely.
let hasSessionResponse = true;

const { invokeMock } = vi.hoisted(() => ({
  invokeMock: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn().mockResolvedValue(() => {}) }));

beforeEach(async () => {
  hasSessionResponse = true;
  invokeMock.mockImplementation((command: string) => {
    if (command === "has_session") {
      return Promise.resolve(hasSessionResponse);
    }
    if (command === "get_system_health") {
      return Promise.resolve({ ok: true, statusCode: 200, body: { bootstrap_required: false } });
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
  });

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
    renderRouter();

    expect(await screen.findByText("KORTEX OS")).toBeInTheDocument();
    expect(screen.getByText("No application mounted")).toBeInTheDocument();
  });

  it("renders the login screen, never the shell, through the real singleton router when no session is stored", async () => {
    hasSessionResponse = false;

    renderRouter();

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("KORTEX OS")).not.toBeInTheDocument();
  });

  it("routes a default application through the real singleton router and renders its content", async () => {
    renderRouter();
    // AuthGate (M4.1) gates the shell behind the mocked session check above
    // resolving — "KORTEX OS" (TopBar) does not render until then, so this
    // must be awaited before touching the router/shell below.
    await screen.findByText("KORTEX OS");
    // AI Studio (Slice 4.6) was the last default application to become a
    // real feature (workspace/defaultApps.ts) — all five now render their
    // own real content instead of their `description` field verbatim the
    // way a placeholder application does. Asserting on AiStudioApp's own
    // static description text (not the generic `defaultApps.ts` entry)
    // still exercises exactly what this test checks: that the real
    // singleton router renders whichever application's content is
    // current. `findByText` (not `getByText`) because AiStudioApp's
    // provider/model sections resolve their TanStack Query state
    // asynchronously off the mocked `invoke_capability` above.
    const testApp = DEFAULT_APPLICATIONS.find((app) => app.id === "ai-studio");
    if (!testApp) {
      throw new Error("Expected the ai-studio application to exist.");
    }

    await act(async () => {
      await router.navigate(testApp.route);
    });

    expect(await screen.findByText(/Provider and model registry — browse only\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: testApp.name })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("falls back to the not-found route for an unregistered path", async () => {
    renderRouter();

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
