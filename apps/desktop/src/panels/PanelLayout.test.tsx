import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PanelLayout } from "./PanelLayout";
import { PanelProvider } from "./PanelProvider";
import type { PanelDefinition } from "./panelTypes";

// PanelProvider persists open/closed state to localStorage on every
// change (panelPersistence.ts), and jsdom's localStorage is shared across
// `it` blocks within a file — without clearing it, one test's persisted
// state (even an empty one) would leak into and override the next test's
// `defaultOpen` expectations.
afterEach(() => {
  window.localStorage.clear();
});

function makePanel(overrides: Partial<PanelDefinition> = {}): PanelDefinition {
  return {
    id: "test-panel",
    title: "Test Panel",
    icon: () => null,
    position: "right",
    component: () => <div>Panel body</div>,
    defaultOpen: true,
    permissions: [],
    ...overrides,
  };
}

describe("PanelLayout", () => {
  it("renders only the main area when no panels are registered", () => {
    render(
      <PanelProvider>
        <PanelLayout>
          <p>Main content</p>
        </PanelLayout>
      </PanelProvider>,
    );

    expect(screen.getByTestId("panel-area-main")).toHaveTextContent("Main content");
    expect(screen.queryByTestId("panel-area-left")).not.toBeInTheDocument();
    expect(screen.queryByTestId("panel-area-right")).not.toBeInTheDocument();
    expect(screen.queryByTestId("panel-area-bottom")).not.toBeInTheDocument();
  });

  it("renders left, right, and bottom panels in their own areas", () => {
    render(
      <PanelProvider
        initialPanels={[
          makePanel({ id: "left-panel", title: "Left Panel", position: "left" }),
          makePanel({ id: "right-panel", title: "Right Panel", position: "right" }),
          makePanel({ id: "bottom-panel", title: "Bottom Panel", position: "bottom" }),
        ]}
      >
        <PanelLayout>
          <p>Main content</p>
        </PanelLayout>
      </PanelProvider>,
    );

    expect(within(screen.getByTestId("panel-area-left")).getByText("Left Panel")).toBeInTheDocument();
    expect(within(screen.getByTestId("panel-area-right")).getByText("Right Panel")).toBeInTheDocument();
    expect(within(screen.getByTestId("panel-area-bottom")).getByText("Bottom Panel")).toBeInTheDocument();
    expect(screen.getByTestId("panel-area-main")).toHaveTextContent("Main content");
  });

  it("omits an area's panels once they are all closed", () => {
    render(
      <PanelProvider initialPanels={[makePanel({ defaultOpen: false })]}>
        <PanelLayout>
          <p>Main content</p>
        </PanelLayout>
      </PanelProvider>,
    );

    expect(screen.queryByTestId("panel-area-right")).not.toBeInTheDocument();
  });

  it("stacks multiple panels registered at the same position", () => {
    render(
      <PanelProvider
        initialPanels={[
          makePanel({ id: "inspector", title: "Inspector", position: "right" }),
          makePanel({ id: "assistant", title: "Assistant", position: "right" }),
        ]}
      >
        <PanelLayout>
          <p>Main content</p>
        </PanelLayout>
      </PanelProvider>,
    );

    const rightArea = screen.getByTestId("panel-area-right");
    expect(within(rightArea).getByText("Inspector")).toBeInTheDocument();
    expect(within(rightArea).getByText("Assistant")).toBeInTheDocument();
  });
});
