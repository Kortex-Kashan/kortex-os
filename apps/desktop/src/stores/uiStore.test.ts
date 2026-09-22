import { afterEach, describe, expect, it } from "vitest";

import { useUiStore } from "./uiStore";

afterEach(() => {
  useUiStore.setState({
    theme: "dark",
    copilotOpen: false,
    copilotMode: "floating",
    copilotTab: "chat",
  });
});

describe("uiStore", () => {
  it("defaults to the dark theme and copilot closed", () => {
    expect(useUiStore.getState().theme).toBe("dark");
    expect(useUiStore.getState().copilotOpen).toBe(false);
    expect(useUiStore.getState().copilotMode).toBe("floating");
    expect(useUiStore.getState().copilotTab).toBe("chat");
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

  describe("copilot state", () => {
    it("toggles and sets copilotOpen", () => {
      expect(useUiStore.getState().copilotOpen).toBe(false);
      useUiStore.getState().toggleCopilot();
      expect(useUiStore.getState().copilotOpen).toBe(true);
      useUiStore.getState().setCopilotOpen(false);
      expect(useUiStore.getState().copilotOpen).toBe(false);
    });

    it("sets copilotMode and copilotTab", () => {
      useUiStore.getState().setCopilotMode("docked");
      expect(useUiStore.getState().copilotMode).toBe("docked");

      useUiStore.getState().setCopilotTab("agents");
      expect(useUiStore.getState().copilotTab).toBe("agents");
    });
  });
});

