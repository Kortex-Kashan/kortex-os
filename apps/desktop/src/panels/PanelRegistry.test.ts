import { describe, expect, it } from "vitest";

import { PanelRegistry } from "./PanelRegistry";
import type { PanelDefinition } from "./panelTypes";

function makePanel(overrides: Partial<PanelDefinition> = {}): PanelDefinition {
  return {
    id: "test-panel",
    title: "Test Panel",
    icon: () => null,
    position: "right",
    component: () => null,
    defaultOpen: false,
    permissions: [],
    ...overrides,
  };
}

describe("PanelRegistry", () => {
  it("registers and retrieves a panel", () => {
    const registry = new PanelRegistry();
    const panel = makePanel();

    registry.register(panel);

    expect(registry.get("test-panel")).toBe(panel);
    expect(registry.has("test-panel")).toBe(true);
    expect(registry.list()).toEqual([panel]);
  });

  it("throws when registering a duplicate ID", () => {
    const registry = new PanelRegistry();
    registry.register(makePanel());

    expect(() => registry.register(makePanel({ title: "Different title" }))).toThrow(
      'Panel "test-panel" is already registered.',
    );
    // The original registration must survive a failed duplicate attempt.
    expect(registry.get("test-panel")?.title).toBe("Test Panel");
  });

  it("unregisters a panel", () => {
    const registry = new PanelRegistry();
    registry.register(makePanel());

    registry.unregister("test-panel");

    expect(registry.has("test-panel")).toBe(false);
    expect(registry.get("test-panel")).toBeUndefined();
    expect(registry.list()).toEqual([]);
  });

  it("unregistering an unknown ID is a no-op, not an error", () => {
    const registry = new PanelRegistry();
    expect(() => registry.unregister("does-not-exist")).not.toThrow();
  });

  it("lists every registered panel", () => {
    const registry = new PanelRegistry();
    registry.register(makePanel({ id: "panel-a", title: "Panel A" }));
    registry.register(makePanel({ id: "panel-b", title: "Panel B" }));

    expect(registry.list().map((panel) => panel.id).sort()).toEqual(["panel-a", "panel-b"]);
  });

  it("is isolated per instance — no shared global state", () => {
    const first = new PanelRegistry();
    const second = new PanelRegistry();

    first.register(makePanel());

    expect(first.has("test-panel")).toBe(true);
    expect(second.has("test-panel")).toBe(false);
  });
});
