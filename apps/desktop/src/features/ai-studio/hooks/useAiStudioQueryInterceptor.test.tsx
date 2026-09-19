import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { IpcResultEnvelope } from "@/ipc/client";
import { AiStudioAccessDeniedError, AiStudioRequestError } from "../api";
import { useAiStudioQueryInterceptor } from "./useAiStudioQueryInterceptor";

const { useOptionalAuthMock } = vi.hoisted(() => ({
  useOptionalAuthMock: vi.fn(),
}));

vi.mock("@/auth/AuthProvider", () => ({
  useOptionalAuth: useOptionalAuthMock,
}));

function makeEnvelope(httpStatus: number): IpcResultEnvelope {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "FAILURE",
    httpStatus,
    payload: null,
    errors: [
      {
        category: "PERMISSION_DENIED",
        message: "denied",
        correlationId: "corr-1",
      },
    ],
    warnings: [],
    executionDurationMs: 5,
  };
}

describe("useAiStudioQueryInterceptor", () => {
  it("passes through successful query results", async () => {
    useOptionalAuthMock.mockReturnValue(null);
    const { result } = renderHook(() => useAiStudioQueryInterceptor());

    const queryFn = vi.fn().mockResolvedValue("hello");
    const wrapped = result.current.interceptQuery(queryFn);

    await expect(wrapped()).resolves.toBe("hello");
    expect(queryFn).toHaveBeenCalled();
  });

  it("passes through successful mutation results", async () => {
    useOptionalAuthMock.mockReturnValue(null);
    const { result } = renderHook(() => useAiStudioQueryInterceptor());

    const mutationFn = vi.fn().mockResolvedValue({ success: true });
    const wrapped = result.current.interceptMutation(mutationFn);

    await expect(wrapped("arg")).resolves.toEqual({ success: true });
    expect(mutationFn).toHaveBeenCalledWith("arg");
  });

  it("calls reportIpcResult with the envelope when AiStudioAccessDeniedError is thrown in query", async () => {
    const reportIpcResult = vi.fn();
    useOptionalAuthMock.mockReturnValue({ reportIpcResult });
    const { result } = renderHook(() => useAiStudioQueryInterceptor());

    const envelope = makeEnvelope(403);
    const queryFn = vi.fn().mockRejectedValue(new AiStudioAccessDeniedError("Forbidden", envelope));
    const wrapped = result.current.interceptQuery(queryFn);

    await expect(wrapped()).rejects.toBeInstanceOf(AiStudioAccessDeniedError);
    expect(reportIpcResult).toHaveBeenCalledWith(envelope);
  });

  it("calls reportIpcResult with the envelope when AiStudioRequestError is thrown in mutation (401 session expiry)", async () => {
    const reportIpcResult = vi.fn();
    useOptionalAuthMock.mockReturnValue({ reportIpcResult });
    const { result } = renderHook(() => useAiStudioQueryInterceptor());

    const envelope = makeEnvelope(401);
    const mutationFn = vi.fn().mockRejectedValue(new AiStudioRequestError("Unauthorized", envelope));
    const wrapped = result.current.interceptMutation(mutationFn);

    await expect(wrapped("test")).rejects.toBeInstanceOf(AiStudioRequestError);
    expect(reportIpcResult).toHaveBeenCalledWith(envelope);
  });

  it("does not crash if useOptionalAuth is null and rethrows ordinary error", async () => {
    useOptionalAuthMock.mockReturnValue(null);
    const { result } = renderHook(() => useAiStudioQueryInterceptor());

    const queryFn = vi.fn().mockRejectedValue(new Error("network"));
    const wrapped = result.current.interceptQuery(queryFn);

    await expect(wrapped()).rejects.toThrow("network");
  });
});
