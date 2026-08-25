import { afterEach, describe, expect, it } from "vitest";

import { useUiStore } from "./uiStore";

afterEach(() => {
  useUiStore.setState({ theme: "light" });
});

describe("uiStore", () => {
  it("defaults to the light theme", () => {
    expect(useUiStore.getState().theme).toBe("light");
  });

  describe("toggleTheme", () => {
    it("flips light to dark", () => {
      useUiStore.getState().toggleTheme();

      expect(useUiStore.getState().theme).toBe("dark");
    });

    it("flips dark back to light", () => {
      useUiStore.getState().setTheme("dark");
      useUiStore.getState().toggleTheme();

      expect(useUiStore.getState().theme).toBe("light");
    });
  });

  describe("setTheme", () => {
    it("sets an absolute theme regardless of the current value", () => {
      useUiStore.getState().setTheme("dark");

      expect(useUiStore.getState().theme).toBe("dark");
    });

    it("is idempotent when set to the value already current", () => {
      useUiStore.getState().setTheme("light");

      expect(useUiStore.getState().theme).toBe("light");
    });

    it("does not require toggleTheme to have run first", () => {
      useUiStore.getState().setTheme("dark");

      expect(useUiStore.getState().theme).toBe("dark");
    });
  });
});
