import { describe, expect, it, vi } from "vitest";

const { fetchSystemHealthMock } = vi.hoisted(() => ({ fetchSystemHealthMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({ fetchSystemHealth: fetchSystemHealthMock }));

import {
  engineCapabilityCount,
  getSystemHealth,
  isEngineHealthy,
  SystemHealthUnavailableError,
} from "./api";

const REALISTIC_BODY = {
  kernel_state: "RUNNING",
  db_dialect: "sqlite",
  db_connected: true,
  system_health: {
    status: "healthy",
    engines: {
      registry: { engine: "registry", status: "healthy", capabilities_count: 6, registered_counts: {} },
      storage: { engine: "storage", status: "RUNNING", healthy: true, stores: {} },
    },
  },
};

describe("getSystemHealth", () => {
  it("returns the validated body when the transport succeeds", async () => {
    fetchSystemHealthMock.mockResolvedValueOnce({ ok: true, statusCode: 200, body: REALISTIC_BODY });

    const result = await getSystemHealth();

    expect(result).toEqual(REALISTIC_BODY);
  });

  it("throws SystemHealthUnavailableError with the backend's message when the transport reports a failure", async () => {
    fetchSystemHealthMock.mockResolvedValue({
      ok: false,
      error: "Backend unreachable: connection refused",
    });

    await expect(getSystemHealth()).rejects.toThrow(SystemHealthUnavailableError);
    await expect(getSystemHealth()).rejects.toThrow(/unreachable/);
  });

  it("throws SystemHealthUnavailableError when the body is missing system_health.status", async () => {
    fetchSystemHealthMock.mockResolvedValueOnce({ ok: true, statusCode: 200, body: { kernel_state: "RUNNING" } });

    await expect(getSystemHealth()).rejects.toThrow(SystemHealthUnavailableError);
  });

  it("throws SystemHealthUnavailableError when the body is not an object", async () => {
    fetchSystemHealthMock.mockResolvedValueOnce({ ok: true, statusCode: 200, body: "not an object" });

    await expect(getSystemHealth()).rejects.toThrow(SystemHealthUnavailableError);
  });

  it("normalizes an unexpected transport rejection into SystemHealthUnavailableError rather than leaking it raw", async () => {
    // `fetchSystemHealth` is expected to resolve with `{ ok: false }` for a
    // transport failure, never reject — but a caller must still get a
    // consistent, typed error if it somehow does (observed for real when
    // exercising this query outside a Tauri webview during runtime
    // verification, where `invoke()` throws synchronously).
    fetchSystemHealthMock.mockRejectedValueOnce(new TypeError("Cannot read properties of undefined"));

    await expect(getSystemHealth()).rejects.toThrow(SystemHealthUnavailableError);
  });
});

describe("isEngineHealthy", () => {
  it("prefers the explicit healthy boolean when present", () => {
    expect(isEngineHealthy({ status: "RUNNING", healthy: true })).toBe(true);
    expect(isEngineHealthy({ status: "RUNNING", healthy: false })).toBe(false);
  });

  it("falls back to a case-insensitive literal status comparison when healthy is absent", () => {
    expect(isEngineHealthy({ status: "healthy" })).toBe(true);
    expect(isEngineHealthy({ status: "HEALTHY" })).toBe(true);
    expect(isEngineHealthy({ status: "unhealthy" })).toBe(false);
    expect(isEngineHealthy({ status: "RUNNING" })).toBe(false);
  });

  it("treats a missing status as unhealthy rather than throwing", () => {
    expect(isEngineHealthy({})).toBe(false);
  });
});

describe("engineCapabilityCount", () => {
  it("reads capabilities_count off the registry engine's report", () => {
    expect(engineCapabilityCount(REALISTIC_BODY.system_health.engines)).toBe(6);
  });

  it("returns null rather than fabricating a count when unavailable", () => {
    expect(engineCapabilityCount({})).toBeNull();
    expect(engineCapabilityCount({ registry: { status: "healthy" } })).toBeNull();
  });
});
