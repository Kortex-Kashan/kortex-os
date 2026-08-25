import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useWorkspace, WorkspaceProvider } from "./WorkspaceProvider";
import type { WorkspaceApplication } from "./workspaceTypes";

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

type WorkspaceApi = ReturnType<typeof useWorkspace>;

function WorkspaceProbe({
  appToRegister,
  onReady,
}: {
  appToRegister?: WorkspaceApplication;
  onReady?: (api: WorkspaceApi) => void;
}) {
  const workspace = useWorkspace();
  onReady?.(workspace);
  return (
    <div>
      <p data-testid="app-count">{workspace.applications.length}</p>
      <p data-testid="active-id">{workspace.activeApplicationId ?? "none"}</p>
      <ul>
        {workspace.applications.map((app) => (
          <li key={app.id}>
            <button onClick={() => workspace.setActiveApplication(app.id)}>Activate {app.name}</button>
          </li>
        ))}
      </ul>
      <button onClick={() => workspace.setActiveApplication(null)}>Deactivate</button>
      {appToRegister && (
        <button onClick={() => workspace.registerApplication(appToRegister)}>Register</button>
      )}
      <button onClick={() => workspace.unregisterApplication("test-app")}>Unregister test-app</button>
    </div>
  );
}

describe("WorkspaceProvider", () => {
  it("renders children and exposes an empty registry by default", () => {
    render(
      <WorkspaceProvider>
        <WorkspaceProbe />
      </WorkspaceProvider>,
    );

    expect(screen.getByTestId("app-count")).toHaveTextContent("0");
    expect(screen.getByTestId("active-id")).toHaveTextContent("none");
  });

  it("registers initial applications on mount", () => {
    render(
      <WorkspaceProvider initialApplications={[makeApp()]}>
        <WorkspaceProbe />
      </WorkspaceProvider>,
    );

    expect(screen.getByTestId("app-count")).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "Activate Test App" })).toBeInTheDocument();
  });

  it("registers an application dynamically via context", () => {
    const secondApp = makeApp({ id: "second-app", name: "Second App" });
    render(
      <WorkspaceProvider>
        <WorkspaceProbe appToRegister={secondApp} />
      </WorkspaceProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Register" }));

    expect(screen.getByTestId("app-count")).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "Activate Second App" })).toBeInTheDocument();
  });

  it("prevents registering a duplicate ID", () => {
    // Called directly rather than via fireEvent.click: React 19 doesn't
    // let an error thrown inside an event handler propagate synchronously
    // back through fireEvent.click() to the test, so expect().toThrow()
    // can't observe it that way — it surfaces as an unhandled exception
    // instead. Capturing the API and calling it directly sidesteps React's
    // event dispatch entirely, so the throw propagates normally.
    const duplicate = makeApp({ name: "Duplicate" });
    let api!: WorkspaceApi;
    render(
      <WorkspaceProvider initialApplications={[makeApp()]}>
        <WorkspaceProbe onReady={(value) => (api = value)} />
      </WorkspaceProvider>,
    );

    expect(() => api.registerApplication(duplicate)).toThrow(
      'Workspace application "test-app" is already registered.',
    );
    expect(screen.getByTestId("app-count")).toHaveTextContent("1");
  });

  it("switches the active application", () => {
    render(
      <WorkspaceProvider initialApplications={[makeApp()]}>
        <WorkspaceProbe />
      </WorkspaceProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Activate Test App" }));
    expect(screen.getByTestId("active-id")).toHaveTextContent("test-app");

    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    expect(screen.getByTestId("active-id")).toHaveTextContent("none");
  });

  it("throws when activating an unregistered application ID", () => {
    let api!: WorkspaceApi;
    render(
      <WorkspaceProvider>
        <WorkspaceProbe onReady={(value) => (api = value)} />
      </WorkspaceProvider>,
    );

    expect(() => api.setActiveApplication("does-not-exist")).toThrow(
      'Cannot activate unknown workspace application "does-not-exist".',
    );
  });

  it("clears the active application when it is unregistered", () => {
    render(
      <WorkspaceProvider initialApplications={[makeApp()]}>
        <WorkspaceProbe />
      </WorkspaceProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Activate Test App" }));
    expect(screen.getByTestId("active-id")).toHaveTextContent("test-app");

    fireEvent.click(screen.getByRole("button", { name: "Unregister test-app" }));

    expect(screen.getByTestId("app-count")).toHaveTextContent("0");
    expect(screen.getByTestId("active-id")).toHaveTextContent("none");
  });

  it("throws when useWorkspace is called outside a provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    function Orphan() {
      useWorkspace();
      return null;
    }

    expect(() => render(<Orphan />)).toThrow("useWorkspace must be used within a WorkspaceProvider");
    spy.mockRestore();
  });
});
