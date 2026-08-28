import { describe, expect, it, vi } from "vitest";
import { clearStoredSession, hasStoredSession } from "@/ipc/session";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: invokeMock,
}));

describe("hasStoredSession", () => {
  it("invokes has_session and returns its boolean result", async () => {
    invokeMock.mockResolvedValueOnce(true);
    const result = await hasStoredSession();
    expect(invokeMock).toHaveBeenCalledWith("has_session");
    expect(result).toBe(true);
  });

  it("returns false when no session token is held", async () => {
    invokeMock.mockResolvedValueOnce(false);
    const result = await hasStoredSession();
    expect(result).toBe(false);
  });
});

describe("clearStoredSession", () => {
  it("invokes logout", async () => {
    invokeMock.mockResolvedValueOnce(undefined);
    await clearStoredSession();
    expect(invokeMock).toHaveBeenCalledWith("logout");
  });
});
