import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { loadPanelState, savePanelState } from "./panelPersistence";
import { PanelProvider, usePanels } from "./PanelProvider";
import type { PanelDefinition } from "./panelTypes";

function makePanel(overrides: Partial<PanelDefinition> = {}): PanelDefinition {
  return {
    id: "test-panel",
    title: "Test Panel",
    icon: () => null,
    position: "right",
    component: () => <div>Test Panel content</div>,
    defaultOpen: false,
    permissions: [],
    ...overrides,
  };
}

type PanelsApi = ReturnType<typeof usePanels>;

function PanelsProbe({
  panelToRegister,
  onReady,
}: {
  panelToRegister?: PanelDefinition;
  onReady?: (api: PanelsApi) => void;
}) {
  const panels = usePanels();
  onReady?.(panels);
  return (
    <div>
      <p data-testid="panel-count">{panels.panels.length}</p>
      <p data-testid="active-id">{panels.activePanelId ?? "none"}</p>
      <ul>
        {panels.panels.map((panel) => (
          <li key={panel.id}>
            <span data-testid={`open-${panel.id}`}>{String(panels.isPanelOpen(panel.id))}</span>
            <span data-testid={`size-${panel.id}`}>{panels.getPanelSize(panel.id)}</span>
            <button onClick={() => panels.togglePanel(panel.id)}>Toggle {panel.title}</button>
            <button onClick={() => panels.openPanel(panel.id)}>Open {panel.title}</button>
            <button onClick={() => panels.closePanel(panel.id)}>Close {panel.title}</button>
            <button onClick={() => panels.activatePanel(panel.id)}>Activate {panel.title}</button>
            <button onClick={() => panels.setPanelSize(panel.id, 999)}>Resize {panel.title}</button>
          </li>
        ))}
      </ul>
      {panelToRegister && (
        <button onClick={() => panels.registerPanel(panelToRegister)}>Register</button>
      )}
      <button onClick={() => panels.unregisterPanel("test-panel")}>Unregister test-panel</button>
    </div>
  );
}

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("PanelProvider", () => {
  it("renders children and exposes an empty registry by default", () => {
    render(
      <PanelProvider>
        <PanelsProbe />
      </PanelProvider>,
    );

    expect(screen.getByTestId("panel-count")).toHaveTextContent("0");
  });

  it("registers initial panels on mount, honoring defaultOpen", () => {
    render(
      <PanelProvider initialPanels={[makePanel({ defaultOpen: true })]}>
        <PanelsProbe />
      </PanelProvider>,
    );

    expect(screen.getByTestId("panel-count")).toHaveTextContent("1");
    expect(screen.getByTestId("open-test-panel")).toHaveTextContent("true");
  });

  it("defaults a panel to closed when defaultOpen is false", () => {
    render(
      <PanelProvider initialPanels={[makePanel({ defaultOpen: false })]}>
        <PanelsProbe />
      </PanelProvider>,
    );

    expect(screen.getByTestId("open-test-panel")).toHaveTextContent("false");
  });

  it("seeds panel size from defaultSize, falling back to the shared default", () => {
    render(
      <PanelProvider
        initialPanels={[
          makePanel({ id: "sized", title: "Sized", defaultSize: { default: 320 } }),
          makePanel({ id: "unsized", title: "Unsized" }),
        ]}
      >
        <PanelsProbe />
      </PanelProvider>,
    );

    expect(screen.getByTestId("size-sized")).toHaveTextContent("320");
    expect(screen.getByTestId("size-unsized")).toHaveTextContent("280");
  });

  it("registers a panel dynamically via context", () => {
    const secondPanel = makePanel({ id: "second-panel", title: "Second Panel" });
    render(
      <PanelProvider>
        <PanelsProbe panelToRegister={secondPanel} />
      </PanelProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Register" }));

    expect(screen.getByTestId("panel-count")).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "Toggle Second Panel" })).toBeInTheDocument();
  });

  it("prevents registering a duplicate ID", () => {
    // Called directly rather than via fireEvent.click, matching
    // WorkspaceProvider.test.tsx: React doesn't propagate an error thrown
    // inside an event handler back through fireEvent.click synchronously.
    const duplicate = makePanel({ title: "Duplicate" });
    let api!: PanelsApi;
    render(
      <PanelProvider initialPanels={[makePanel()]}>
        <PanelsProbe onReady={(value) => (api = value)} />
      </PanelProvider>,
    );

    expect(() => api.registerPanel(duplicate)).toThrow('Panel "test-panel" is already registered.');
    expect(screen.getByTestId("panel-count")).toHaveTextContent("1");
  });

  it("toggles a panel open and closed", () => {
    render(
      <PanelProvider initialPanels={[makePanel()]}>
        <PanelsProbe />
      </PanelProvider>,
    );

    expect(screen.getByTestId("open-test-panel")).toHaveTextContent("false");

    fireEvent.click(screen.getByRole("button", { name: "Toggle Test Panel" }));
    expect(screen.getByTestId("open-test-panel")).toHaveTextContent("true");

    fireEvent.click(screen.getByRole("button", { name: "Toggle Test Panel" }));
    expect(screen.getByTestId("open-test-panel")).toHaveTextContent("false");
  });

  it("opens and closes a panel explicitly", () => {
    render(
      <PanelProvider initialPanels={[makePanel()]}>
        <PanelsProbe />
      </PanelProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open Test Panel" }));
    expect(screen.getByTestId("open-test-panel")).toHaveTextContent("true");

    fireEvent.click(screen.getByRole("button", { name: "Close Test Panel" }));
    expect(screen.getByTestId("open-test-panel")).toHaveTextContent("false");
  });

  it("throws when toggling or opening an unregistered panel ID", () => {
    let api!: PanelsApi;
    render(
      <PanelProvider>
        <PanelsProbe onReady={(value) => (api = value)} />
      </PanelProvider>,
    );

    expect(() => api.togglePanel("does-not-exist")).toThrow(
      'Cannot toggle unknown panel "does-not-exist".',
    );
    expect(() => api.openPanel("does-not-exist")).toThrow(
      'Cannot open unknown panel "does-not-exist".',
    );
  });

  it("activates a panel: opens it and marks it active", () => {
    render(
      <PanelProvider initialPanels={[makePanel()]}>
        <PanelsProbe />
      </PanelProvider>,
    );

    expect(screen.getByTestId("active-id")).toHaveTextContent("none");

    fireEvent.click(screen.getByRole("button", { name: "Activate Test Panel" }));

    expect(screen.getByTestId("active-id")).toHaveTextContent("test-panel");
    expect(screen.getByTestId("open-test-panel")).toHaveTextContent("true");
  });

  it("throws when activating an unregistered panel ID", () => {
    let api!: PanelsApi;
    render(
      <PanelProvider>
        <PanelsProbe onReady={(value) => (api = value)} />
      </PanelProvider>,
    );

    expect(() => api.activatePanel("does-not-exist")).toThrow(
      'Cannot activate unknown panel "does-not-exist".',
    );
  });

  it("clears active state when the active panel is closed or unregistered", () => {
    render(
      <PanelProvider initialPanels={[makePanel()]}>
        <PanelsProbe />
      </PanelProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Activate Test Panel" }));
    expect(screen.getByTestId("active-id")).toHaveTextContent("test-panel");

    fireEvent.click(screen.getByRole("button", { name: "Close Test Panel" }));
    expect(screen.getByTestId("active-id")).toHaveTextContent("none");
  });

  it("unregisters a panel and clears its open/active/size state", () => {
    render(
      <PanelProvider initialPanels={[makePanel({ defaultOpen: true })]}>
        <PanelsProbe />
      </PanelProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Unregister test-panel" }));

    expect(screen.getByTestId("panel-count")).toHaveTextContent("0");
  });

  it("resizes a panel", () => {
    render(
      <PanelProvider initialPanels={[makePanel()]}>
        <PanelsProbe />
      </PanelProvider>,
    );

    expect(screen.getByTestId("size-test-panel")).toHaveTextContent("280");

    fireEvent.click(screen.getByRole("button", { name: "Resize Test Panel" }));

    expect(screen.getByTestId("size-test-panel")).toHaveTextContent("999");
  });

  it("throws when usePanels is called outside a provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    function Orphan() {
      usePanels();
      return null;
    }

    expect(() => render(<Orphan />)).toThrow("usePanels must be used within a PanelProvider");
    spy.mockRestore();
  });

  describe("persistence", () => {
    it("persists open/closed state and sizes to localStorage", async () => {
      render(
        <PanelProvider initialPanels={[makePanel()]}>
          <PanelsProbe />
        </PanelProvider>,
      );

      fireEvent.click(screen.getByRole("button", { name: "Open Test Panel" }));
      fireEvent.click(screen.getByRole("button", { name: "Resize Test Panel" }));

      expect(await screen.findByTestId("size-test-panel")).toHaveTextContent("999");
      const persisted = loadPanelState();
      expect(persisted?.openPanelIds).toContain("test-panel");
      expect(persisted?.sizes["test-panel"]).toBe(999);
    });

    it("restores open/closed state and sizes from a prior session", () => {
      savePanelState({ openPanelIds: ["test-panel"], sizes: { "test-panel": 512 } });

      render(
        <PanelProvider initialPanels={[makePanel({ defaultOpen: false })]}>
          <PanelsProbe />
        </PanelProvider>,
      );

      // defaultOpen is false, but persisted state overrides it.
      expect(screen.getByTestId("open-test-panel")).toHaveTextContent("true");
      expect(screen.getByTestId("size-test-panel")).toHaveTextContent("512");
    });
  });
});
