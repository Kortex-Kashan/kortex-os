import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthIdentity } from "./authTypes";
import { clearCachedIdentity, loadCachedIdentity, saveCachedIdentity } from "./identityCache";

const STORAGE_KEY = "kortex.auth.identity.v1";

function makeIdentity(overrides: Partial<AuthIdentity> = {}): AuthIdentity {
  return {
    principalId: "alice",
    principalType: "USER",
    tenantId: "acme",
    roles: ["reader"],
    ...overrides,
  };
}

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("identityCache", () => {
  it("returns null when nothing has been cached yet", () => {
    expect(loadCachedIdentity()).toBeNull();
  });

  it("round-trips an identity through save and load", () => {
    const identity = makeIdentity();
    saveCachedIdentity(identity);

    expect(loadCachedIdentity()).toEqual(identity);
  });

  it("removes the cached identity on clear", () => {
    saveCachedIdentity(makeIdentity());
    clearCachedIdentity();

    expect(loadCachedIdentity()).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("never stores anything under the workspace session's own storage key", () => {
    saveCachedIdentity(makeIdentity());
    expect(window.localStorage.getItem("kortex.session.v1")).toBeNull();
  });

  it("returns null for corrupted JSON instead of throwing", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not valid json");

    expect(() => loadCachedIdentity()).not.toThrow();
    expect(loadCachedIdentity()).toBeNull();
  });

  it("returns null for well-formed JSON that doesn't match the expected shape", () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ unrelated: true }));

    expect(loadCachedIdentity()).toBeNull();
  });

  it("returns null when a required field is missing", () => {
    const malformed = makeIdentity() as unknown as Record<string, unknown>;
    delete malformed.tenantId;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(malformed));

    expect(loadCachedIdentity()).toBeNull();
  });

  it("degrades to a no-op when localStorage.setItem throws", () => {
    vi.spyOn(window.localStorage.__proto__, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    expect(() => saveCachedIdentity(makeIdentity())).not.toThrow();
  });

  it("degrades to null when localStorage.getItem throws", () => {
    vi.spyOn(window.localStorage.__proto__, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    expect(loadCachedIdentity()).toBeNull();
  });

  it("degrades to a no-op when localStorage.removeItem throws", () => {
    vi.spyOn(window.localStorage.__proto__, "removeItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    expect(() => clearCachedIdentity()).not.toThrow();
  });
});
