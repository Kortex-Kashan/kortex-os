import { describe, expect, it } from "vitest";

import { DEFAULT_PANELS } from "./defaultPanels";
import { PanelRegistry } from "./PanelRegistry";

describe("DEFAULT_PANELS", () => {
  it("registers exactly the three demo panels", () => {
    expect(DEFAULT_PANELS.map((panel) => panel.title)).toEqual(["Inspector", "Logs", "Assistant"]);
  });

  it("has unique, non-empty IDs and required fields for every entry", () => {
    const ids = DEFAULT_PANELS.map((panel) => panel.id);
    expect(new Set(ids).size).toBe(ids.length);

    for (const panel of DEFAULT_PANELS) {
      expect(panel.id).toBeTruthy();
      expect(panel.title).toBeTruthy();
      expect(typeof panel.icon).toBe("function");
      expect(typeof panel.component).toBe("function");
      expect(["left", "right", "bottom"]).toContain(panel.position);
      expect(typeof panel.defaultOpen).toBe("boolean");
      expect(Array.isArray(panel.permissions)).toBe(true);
    }
  });

  it("registers cleanly into a PanelRegistry with no duplicate-ID conflicts", () => {
    const registry = new PanelRegistry();
    for (const panel of DEFAULT_PANELS) {
      expect(() => registry.register(panel)).not.toThrow();
    }
    expect(registry.list()).toHaveLength(3);
  });
});
