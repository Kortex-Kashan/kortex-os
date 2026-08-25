import { afterEach, describe, expect, it, vi } from "vitest";

import { loadPanelState, savePanelState } from "./panelPersistence";

const STORAGE_KEY = "kortex.panels.v1";

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("panelPersistence", () => {
  it("returns null when nothing has been persisted yet", () => {
    expect(loadPanelState()).toBeNull();
  });

  it("round-trips state through save and load", () => {
    savePanelState({ openPanelIds: ["logs", "inspector"], sizes: { logs: 160, inspector: 280 } });

    expect(loadPanelState()).toEqual({
      openPanelIds: ["logs", "inspector"],
      sizes: { logs: 160, inspector: 280 },
    });
  });

  it("returns null for corrupted JSON instead of throwing", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not valid json");

    expect(() => loadPanelState()).not.toThrow();
    expect(loadPanelState()).toBeNull();
  });

  it("returns null for well-formed JSON that doesn't match the expected shape", () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ unrelated: true }));

    expect(loadPanelState()).toBeNull();
  });

  it("degrades to a no-op when localStorage.setItem throws", () => {
    vi.spyOn(window.localStorage.__proto__, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    expect(() => savePanelState({ openPanelIds: [], sizes: {} })).not.toThrow();
  });

  it("degrades to null when localStorage.getItem throws", () => {
    vi.spyOn(window.localStorage.__proto__, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    expect(loadPanelState()).toBeNull();
  });
});
