import { afterEach, describe, expect, it, vi } from "vitest";

import { SessionManager } from "./SessionManager";
import { loadSession, saveSession } from "./sessionStorage";
import type { KortexSession } from "./sessionTypes";

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("SessionManager", () => {
  it("has no current session before create or restore is called", () => {
    const manager = new SessionManager();

    expect(manager.getSession()).toBeNull();
  });

  describe("createSession", () => {
    it("creates and persists a default session", () => {
      const manager = new SessionManager();

      const session = manager.createSession();

      expect(session).toEqual({
        version: 1,
        activeApplication: null,
        panelState: { openPanelIds: [], sizes: {} },
        theme: "light",
        preferences: { sidebarCollapsed: false },
        updatedAt: expect.any(String),
      });
      expect(loadSession()).toEqual(session);
      expect(manager.getSession()).toEqual(session);
    });
  });

  describe("restoreSession", () => {
    it("returns null when nothing has been persisted", () => {
      const manager = new SessionManager();

      expect(manager.restoreSession()).toBeNull();
      expect(manager.getSession()).toBeNull();
    });

    it("restores a previously persisted session", () => {
      const writer = new SessionManager();
      writer.createSession();
      writer.updateSession({ activeApplication: "dashboard", theme: "dark" });

      const reader = new SessionManager();
      const restored = reader.restoreSession();

      expect(restored?.activeApplication).toBe("dashboard");
      expect(restored?.theme).toBe("dark");
      expect(reader.getSession()).toEqual(restored);
    });

    it("returns null and discards a session with a mismatched version", () => {
      saveSession({
        version: 999,
        activeApplication: "dashboard",
        panelState: { openPanelIds: [], sizes: {} },
        theme: "dark",
        preferences: { sidebarCollapsed: false },
        updatedAt: new Date().toISOString(),
      } as KortexSession);

      const manager = new SessionManager();

      expect(manager.restoreSession()).toBeNull();
      expect(manager.getSession()).toBeNull();
    });

    it("returns null for corrupted storage instead of throwing", () => {
      window.localStorage.setItem("kortex.session.v1", "{not valid json");
      const manager = new SessionManager();

      expect(() => manager.restoreSession()).not.toThrow();
      expect(manager.restoreSession()).toBeNull();
    });
  });

  describe("updateSession", () => {
    it("merges a partial update onto the current session and persists it", () => {
      const manager = new SessionManager();
      manager.createSession();

      const updated = manager.updateSession({ activeApplication: "ai-studio", theme: "dark" });

      expect(updated.activeApplication).toBe("ai-studio");
      expect(updated.theme).toBe("dark");
      expect(loadSession()).toEqual(updated);
    });

    it("creates a default session first if none exists yet", () => {
      const manager = new SessionManager();

      const updated = manager.updateSession({ activeApplication: "dashboard" });

      expect(updated.activeApplication).toBe("dashboard");
      expect(manager.getSession()).toEqual(updated);
    });

    it("leaves preferences untouched when not included in the update", () => {
      const manager = new SessionManager();
      manager.createSession();
      manager.updateSession({ preferences: { sidebarCollapsed: true } });

      const updated = manager.updateSession({ theme: "dark" });

      expect(updated.preferences).toEqual({ sidebarCollapsed: true });
    });

    it("advances updatedAt on every update", async () => {
      const manager = new SessionManager();
      const created = manager.createSession();

      await new Promise((resolve) => setTimeout(resolve, 2));
      const updated = manager.updateSession({ theme: "dark" });

      expect(updated.updatedAt).not.toBe(created.updatedAt);
    });

    it("always writes the current version, even after restoring an older matching version", () => {
      const manager = new SessionManager();
      manager.createSession();

      const updated = manager.updateSession({ theme: "dark" });

      expect(updated.version).toBe(1);
    });
  });

  describe("clearSession", () => {
    it("discards the in-memory session and removes it from storage", () => {
      const manager = new SessionManager();
      manager.createSession();

      manager.clearSession();

      expect(manager.getSession()).toBeNull();
      expect(loadSession()).toBeNull();
    });
  });
});
