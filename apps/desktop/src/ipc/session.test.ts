import { describe, expect, it, vi } from "vitest";
import { clearStoredSession, hasStoredSession, renewStoredSession } from "@/ipc/session";

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

describe("renewStoredSession", () => {
  it("invokes refresh_session and returns its envelope verbatim", async () => {
    const envelope = { status: "SUCCESS", payload: { result: { principalId: "alice" } } };
    invokeMock.mockResolvedValueOnce(envelope);
    const result = await renewStoredSession();
    expect(invokeMock).toHaveBeenCalledWith("refresh_session");
    expect(result).toEqual(envelope);
  });

  it("propagates a FAILURE envelope without throwing (no refresh token held)", async () => {
    const envelope = { status: "FAILURE", errors: [{ category: "PERMISSION_DENIED" }] };
    invokeMock.mockResolvedValueOnce(envelope);
    const result = await renewStoredSession();
    expect(result).toEqual(envelope);
  });
});
