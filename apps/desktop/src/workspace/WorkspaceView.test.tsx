import * as React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PanelProvider } from "@/panels/PanelProvider";
import type { PanelDefinition } from "@/panels/panelTypes";

import { useWorkspace, WorkspaceProvider } from "./WorkspaceProvider";
import { WorkspaceView } from "./WorkspaceView";
import type { WorkspaceApplication } from "./workspaceTypes";

// See PanelLayout.test.tsx: PanelProvider persists panel state to
// localStorage, which jsdom shares across `it` blocks in this file.
afterEach(() => {
  window.localStorage.clear();
});

function makeApp(overrides: Partial<WorkspaceApplication> = {}): WorkspaceApplication {
  return {
    id: "test-app",
    name: "Test App",
    description: "A test application.",
    icon: () => null,
    route: "/test-app",
    component: () => <div>Test App content</div>,
    permissions: [],
    ...overrides,
  };
}

/** Auto-activates the given app on mount, then renders WorkspaceView. */
function ActiveWorkspace({ appId }: { appId: string }) {
  const { setActiveApplication } = useWorkspace();
  React.useEffect(() => {
    setActiveApplication(appId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <WorkspaceView />;
}

function renderWithApp(app: WorkspaceApplication, panels: PanelDefinition[] = []) {
  return render(
    <PanelProvider initialPanels={panels}>
      <WorkspaceProvider initialApplications={[app]}>
        <ActiveWorkspace appId={app.id} />
      </WorkspaceProvider>
    </PanelProvider>,
  );
}

describe("WorkspaceView", () => {
  it("renders the shell's empty state when no application is active", () => {
    render(
      <PanelProvider>
        <WorkspaceProvider>
          <WorkspaceView />
        </WorkspaceProvider>
      </PanelProvider>,
    );

    expect(screen.getByText("No application mounted")).toBeInTheDocument();
  });

  it("renders the active application's component", async () => {
    renderWithApp(makeApp());

    expect(await screen.findByText("Test App content")).toBeInTheDocument();
  });

  it("shows a loading fallback while a lazy application resolves, then its content", async () => {
    let resolveImport!: () => void;
    const importPromise = new Promise<void>((resolve) => {
      resolveImport = resolve;
    });
    const LazyApp = React.lazy(() =>
      importPromise.then(() => ({ default: () => <div>Lazy app content</div> })),
    );

    renderWithApp(makeApp({ component: LazyApp }));

    expect(screen.getByText("Loading application…")).toBeInTheDocument();

    resolveImport();

    expect(await screen.findByText("Lazy app content")).toBeInTheDocument();
    expect(screen.queryByText("Loading application…")).not.toBeInTheDocument();
  });

  it("catches a render error, shows the fallback, and recovers via Try again", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    // A plain boolean toggled from inside the component's own render (e.g.
    // "throw only on the first call") is unreliable here: React 19 retries
    // a failed render once internally before committing to the error
    // boundary, so a self-flipping flag can "heal" during that internal
    // retry and the boundary's fallback never even commits. Using an
    // external flag that the test flips itself — only after confirming
    // the fallback is showing — avoids racing React's own recovery pass.
    const flaky = { shouldThrow: true };
    function FlakyComponent() {
      if (flaky.shouldThrow) {
        throw new Error("Simulated render failure");
      }
      return <div>Recovered content</div>;
    }

    renderWithApp(makeApp({ component: FlakyComponent }));

    expect(await screen.findByText("Application failed to load")).toBeInTheDocument();
    expect(screen.getByText("Simulated render failure")).toBeInTheDocument();

    flaky.shouldThrow = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Recovered content")).toBeInTheDocument();
    spy.mockRestore();
  });

  it("renders through PanelLayout, showing registered panels alongside the active application", async () => {
    const panel: PanelDefinition = {
      id: "inspector",
      title: "Inspector",
      icon: () => null,
      position: "right",
      component: () => <div>Inspector content</div>,
      defaultOpen: true,
      permissions: [],
    };

    renderWithApp(makeApp(), [panel]);

    expect(await screen.findByText("Test App content")).toBeInTheDocument();
    expect(screen.getByTestId("panel-area-main")).toHaveTextContent("Test App content");
    expect(within(screen.getByTestId("panel-area-right")).getByText("Inspector content")).toBeInTheDocument();
  });

  it("still renders the empty state inside PanelLayout's main area when no panels are registered", () => {
    render(
      <PanelProvider>
        <WorkspaceProvider>
          <WorkspaceView />
        </WorkspaceProvider>
      </PanelProvider>,
    );

    expect(screen.getByTestId("panel-layout")).toBeInTheDocument();
    expect(screen.getByTestId("panel-area-main")).toHaveTextContent("No application mounted");
  });
});
