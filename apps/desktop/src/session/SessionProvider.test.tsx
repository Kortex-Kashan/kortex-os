import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildApplicationRoutes } from "@/navigation/applicationRoutes";
import { WorkspaceNavigationSync } from "@/navigation/navigationBridge";
import { PanelProvider, usePanels } from "@/panels/PanelProvider";
import { savePanelState } from "@/panels/panelPersistence";
import type { PanelDefinition } from "@/panels/panelTypes";
import { Workspace } from "@/shell/Workspace";
import { useUiStore } from "@/stores/uiStore";
import { useWorkspace, WorkspaceProvider } from "@/workspace/WorkspaceProvider";
import type { WorkspaceApplication } from "@/workspace/workspaceTypes";

import { SessionProvider, SessionSync, useSession } from "./SessionProvider";
import { loadSession, saveSession } from "./sessionStorage";
import { CURRENT_SESSION_VERSION, type KortexSession } from "./sessionTypes";

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

function makePanel(overrides: Partial<PanelDefinition> = {}): PanelDefinition {
  return {
    id: "inspector",
    title: "Inspector",
    icon: () => null,
    position: "right",
    component: () => <div>Inspector content</div>,
    defaultOpen: false,
    permissions: [],
    ...overrides,
  };
}

function makeSession(overrides: Partial<KortexSession> = {}): KortexSession {
  return {
    version: CURRENT_SESSION_VERSION,
    activeApplication: null,
    theme: "light",
    preferences: { sidebarCollapsed: false },
    updatedAt: new Date(0).toISOString(),
    ...overrides,
  };
}

function Probe() {
  const { session } = useSession();
  const { activeApplicationId } = useWorkspace();
  const panels = usePanels();
  const theme = useUiStore((state) => state.theme);
  return (
    <div>
      <p data-testid="session-active">{session.activeApplication ?? "none"}</p>
      <p data-testid="workspace-active">{activeApplicationId ?? "none"}</p>
      <p data-testid="live-theme">{theme}</p>
      <p data-testid="inspector-open">{String(panels.isPanelOpen("inspector"))}</p>
      <button onClick={() => panels.togglePanel("inspector")}>Toggle Inspector</button>
      <button onClick={() => useUiStore.getState().toggleTheme()}>Toggle Theme</button>
    </div>
  );
}

function buildTestRouter(
  applications: WorkspaceApplication[],
  panels: PanelDefinition[],
  initialEntries: string[],
) {
  return createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <SessionProvider>
            <WorkspaceProvider initialApplications={applications}>
              <PanelProvider initialPanels={panels}>
                <WorkspaceNavigationSync />
                <SessionSync />
                <Probe />
                <Workspace />
              </PanelProvider>
            </WorkspaceProvider>
          </SessionProvider>
        ),
        children: [
          { index: true, element: <div>empty workspace</div> },
          ...buildApplicationRoutes(applications),
        ],
      },
    ],
    { initialEntries },
  );
}

afterEach(() => {
  window.localStorage.clear();
  useUiStore.setState({ theme: "light" });
  vi.restoreAllMocks();
});

describe("SessionProvider", () => {
  it("initializes with a fresh default session when nothing is persisted", () => {
    render(<RouterProvider router={buildTestRouter(makeApps(), [], ["/"])} />);

    expect(screen.getByTestId("session-active")).toHaveTextContent("none");
    expect(screen.getByTestId("live-theme")).toHaveTextContent("light");
  });

  it("persists session updates to localStorage as workspace state changes", () => {
    render(<RouterProvider router={buildTestRouter(makeApps(), [], ["/dashboard"])} />);

    expect(loadSession()?.activeApplication).toBe("dashboard");
  });

  it("throws when useSession is called outside a provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    function Orphan() {
      useSession();
      return null;
    }

    expect(() => render(<Orphan />)).toThrow("useSession must be used within a SessionProvider");
    spy.mockRestore();
  });
});

describe("session restoration", () => {
  it("restores the active application from a previous session on a fresh launch", () => {
    saveSession(makeSession({ activeApplication: "ai-studio" }));

    render(<RouterProvider router={buildTestRouter(makeApps(), [], ["/"])} />);

    expect(screen.getByTestId("workspace-active")).toHaveTextContent("ai-studio");
    expect(screen.getByText("AI Studio content")).toBeInTheDocument();
  });

  it("does not override a deep-linked/refreshed URL with the persisted session", () => {
    saveSession(makeSession({ activeApplication: "ai-studio" }));

    render(<RouterProvider router={buildTestRouter(makeApps(), [], ["/dashboard"])} />);

    expect(screen.getByTestId("workspace-active")).toHaveTextContent("dashboard");
  });

  it("ignores a persisted application that no longer exists in the registry", () => {
    saveSession(makeSession({ activeApplication: "does-not-exist" }));

    render(<RouterProvider router={buildTestRouter(makeApps(), [], ["/"])} />);

    expect(screen.getByTestId("workspace-active")).toHaveTextContent("none");
  });

  it("restores panels on launch via PanelProvider's own persisted state, in the correct order", () => {
    savePanelState({ openPanelIds: ["inspector"], sizes: { inspector: 320 } });

    render(<RouterProvider router={buildTestRouter(makeApps(), [makePanel()], ["/"])} />);

    expect(screen.getByTestId("inspector-open")).toHaveTextContent("true");
  });

  it("restores the theme from a previous session", () => {
    saveSession(makeSession({ theme: "dark" }));

    render(<RouterProvider router={buildTestRouter(makeApps(), [], ["/"])} />);

    expect(screen.getByTestId("live-theme")).toHaveTextContent("dark");
  });
});

describe("session persistence of live changes", () => {
  it("does not mirror panel state into the session document (ADR-0003: PanelProvider's own kortex.panels.v1 key is the single source of truth)", () => {
    render(<RouterProvider router={buildTestRouter(makeApps(), [makePanel()], ["/"])} />);

    fireEvent.click(screen.getByRole("button", { name: "Toggle Inspector" }));

    expect(screen.getByTestId("inspector-open")).toHaveTextContent("true");
    expect(loadSession()).not.toHaveProperty("panelState");
  });

  it("updates the session when the theme changes after mount", () => {
    render(<RouterProvider router={buildTestRouter(makeApps(), [], ["/"])} />);

    fireEvent.click(screen.getByRole("button", { name: "Toggle Theme" }));

    expect(screen.getByTestId("live-theme")).toHaveTextContent("dark");
    expect(loadSession()?.theme).toBe("dark");
  });
});
