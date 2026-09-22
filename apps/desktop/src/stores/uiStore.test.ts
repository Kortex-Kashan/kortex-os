import { afterEach, describe, expect, it } from "vitest";

import { useUiStore } from "./uiStore";

afterEach(() => {
  useUiStore.setState({ theme: "dark" });
});

describe("uiStore", () => {
  it("defaults to the dark theme", () => {
    expect(useUiStore.getState().theme).toBe("dark");
  });

  describe("toggleTheme", () => {
    it("flips dark to light", () => {
      useUiStore.getState().setTheme("dark");
      useUiStore.getState().toggleTheme();

      expect(useUiStore.getState().theme).toBe("light");
    });

    it("flips light back to dark", () => {
      useUiStore.getState().setTheme("light");
      useUiStore.getState().toggleTheme();

      expect(useUiStore.getState().theme).toBe("dark");
    });
  });

  describe("setTheme", () => {
    it("sets an absolute theme regardless of the current value", () => {
      useUiStore.getState().setTheme("light");

      expect(useUiStore.getState().theme).toBe("light");
    });

    it("is idempotent when set to the value already current", () => {
      useUiStore.getState().setTheme("dark");

      expect(useUiStore.getState().theme).toBe("dark");
    });

    it("does not require toggleTheme to have run first", () => {
      useUiStore.getState().setTheme("light");

      expect(useUiStore.getState().theme).toBe("light");
    });
  });
});
