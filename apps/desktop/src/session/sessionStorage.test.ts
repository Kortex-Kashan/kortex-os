import { afterEach, describe, expect, it, vi } from "vitest";

import { clearSession, loadSession, saveSession } from "./sessionStorage";
import { CURRENT_SESSION_VERSION, type KortexSession } from "./sessionTypes";

const STORAGE_KEY = "kortex.session.v1";

function makeSession(overrides: Partial<KortexSession> = {}): KortexSession {
  return {
    version: CURRENT_SESSION_VERSION,
    activeApplication: null,
    theme: "light",
    preferences: { sidebarCollapsed: false },
    updatedAt: "2026-08-26T00:00:00.000Z",
    ...overrides,
  };
}

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("sessionStorage", () => {
  it("returns null when nothing has been persisted yet", () => {
    expect(loadSession()).toBeNull();
  });

  it("round-trips a session through save and load", () => {
    const session = makeSession({ activeApplication: "dashboard" });
    saveSession(session);

    expect(loadSession()).toEqual(session);
  });

  it("removes the persisted session on clear", () => {
    saveSession(makeSession());
    clearSession();

    expect(loadSession()).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("returns null for corrupted JSON instead of throwing", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not valid json");

    expect(() => loadSession()).not.toThrow();
    expect(loadSession()).toBeNull();
  });

  it("returns null for well-formed JSON that doesn't match the expected shape", () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ unrelated: true }));

    expect(loadSession()).toBeNull();
  });

  it("returns null when a required field is missing", () => {
    const malformed = makeSession() as unknown as Record<string, unknown>;
    delete malformed.theme;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(malformed));

    expect(loadSession()).toBeNull();
  });

  it("degrades to a no-op when localStorage.setItem throws", () => {
    vi.spyOn(window.localStorage.__proto__, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    expect(() => saveSession(makeSession())).not.toThrow();
  });

  it("degrades to null when localStorage.getItem throws", () => {
    vi.spyOn(window.localStorage.__proto__, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    expect(loadSession()).toBeNull();
  });

  it("degrades to a no-op when localStorage.removeItem throws", () => {
    vi.spyOn(window.localStorage.__proto__, "removeItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    expect(() => clearSession()).not.toThrow();
  });
});
