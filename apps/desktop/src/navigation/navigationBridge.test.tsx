import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, useLocation, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { Workspace } from "@/shell/Workspace";
import { PanelProvider } from "@/panels/PanelProvider";
import { WorkspaceProvider } from "@/workspace/WorkspaceProvider";
import type { WorkspaceApplication } from "@/workspace/workspaceTypes";

import { buildApplicationRoutes } from "./applicationRoutes";
import { useApplicationNavigation, WorkspaceNavigationSync } from "./navigationBridge";

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

/** Exposes the bridge's resolved state and imperative controls for assertions. */
function NavigationProbe() {
  const { state, navigateToApplication } = useApplicationNavigation();
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <div>
      <p data-testid="active-id">{state.applicationId ?? "none"}</p>
      <p data-testid="pathname">{location.pathname}</p>
      <p data-testid="search">{location.search}</p>
      <button onClick={() => navigateToApplication({ applicationId: "dashboard" })}>
        Go to Dashboard
      </button>
      <button onClick={() => navigateToApplication({ applicationId: "ai-studio" })}>
        Go to AI Studio
      </button>
      <button onClick={() => navigateToApplication({ applicationId: "dashboard", search: "?tab=approvals" })}>
        Go to Dashboard Approvals Tab
      </button>
      <button onClick={() => navigate(-1)}>Back</button>
      <button onClick={() => navigate(1)}>Forward</button>
    </div>
  );
}

function buildTestRouter(applications: WorkspaceApplication[], initialEntries: string[]) {
  return createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <PanelProvider>
            <WorkspaceProvider initialApplications={applications}>
              <WorkspaceNavigationSync />
              <NavigationProbe />
              <Workspace />
            </WorkspaceProvider>
          </PanelProvider>
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

describe("useApplicationNavigation", () => {
  it("activates the application and updates the URL when navigating", () => {
    render(<RouterProvider router={buildTestRouter(makeApps(), ["/"])} />);

    fireEvent.click(screen.getByRole("button", { name: "Go to Dashboard" }));

    expect(screen.getByTestId("pathname")).toHaveTextContent("/dashboard");
    expect(screen.getByTestId("active-id")).toHaveTextContent("dashboard");
  });

  it("M7.2: appends an optional search string to the resolved route", () => {
    render(<RouterProvider router={buildTestRouter(makeApps(), ["/"])} />);

    fireEvent.click(screen.getByRole("button", { name: "Go to Dashboard Approvals Tab" }));

    expect(screen.getByTestId("pathname")).toHaveTextContent("/dashboard");
    expect(screen.getByTestId("search")).toHaveTextContent("?tab=approvals");
  });

  it("throws when asked to navigate to an unknown application id", () => {
    let caught: unknown;
    function ThrowingProbe() {
      const { navigateToApplication } = useApplicationNavigation();
      return (
        <button
          onClick={() => {
            try {
              navigateToApplication({ applicationId: "does-not-exist" });
            } catch (error) {
              caught = error;
            }
          }}
        >
          Try navigate
        </button>
      );
    }
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: (
            <WorkspaceProvider initialApplications={makeApps()}>
              <ThrowingProbe />
            </WorkspaceProvider>
          ),
        },
      ],
      { initialEntries: ["/"] },
    );
    render(<RouterProvider router={router} />);

    fireEvent.click(screen.getByRole("button", { name: "Try navigate" }));

    expect(caught).toBeInstanceOf(Error);
    expect((caught as Error).message).toBe(
      'Cannot navigate to unknown workspace application "does-not-exist".',
    );
  });
});

describe("WorkspaceNavigationSync", () => {
  it("restores the active application from a deep-linked/refreshed URL", () => {
    render(<RouterProvider router={buildTestRouter(makeApps(), ["/ai-studio"])} />);

    expect(screen.getByTestId("active-id")).toHaveTextContent("ai-studio");
    expect(screen.getByText("AI Studio content")).toBeInTheDocument();
  });

  it("leaves no application active when the initial URL is the workspace root", () => {
    render(<RouterProvider router={buildTestRouter(makeApps(), ["/"])} />);

    expect(screen.getByTestId("active-id")).toHaveTextContent("none");
  });

  it("clears the active application when navigating back to the workspace root", () => {
    // Two history entries so "Back" has somewhere to go — a single
    // initial entry has no prior state for history.back() to land on.
    render(<RouterProvider router={buildTestRouter(makeApps(), ["/", "/dashboard"])} />);

    expect(screen.getByTestId("active-id")).toHaveTextContent("dashboard");

    fireEvent.click(screen.getByRole("button", { name: "Back" }));

    expect(screen.getByTestId("active-id")).toHaveTextContent("none");
  });

  it("supports browser back/forward navigation between applications", async () => {
    render(<RouterProvider router={buildTestRouter(makeApps(), ["/"])} />);

    fireEvent.click(screen.getByRole("button", { name: "Go to Dashboard" }));
    expect(screen.getByTestId("active-id")).toHaveTextContent("dashboard");

    fireEvent.click(screen.getByRole("button", { name: "Go to AI Studio" }));
    expect(screen.getByTestId("active-id")).toHaveTextContent("ai-studio");

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(await screen.findByTestId("active-id")).toHaveTextContent("dashboard");

    fireEvent.click(screen.getByRole("button", { name: "Forward" }));
    expect(await screen.findByTestId("active-id")).toHaveTextContent("ai-studio");
  });
});
