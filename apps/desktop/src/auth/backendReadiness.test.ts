import { afterEach, describe, expect, it, vi } from "vitest";

import type { SystemHealthOutcome } from "@/ipc/client";

const { fetchSystemHealthMock } = vi.hoisted(() => ({ fetchSystemHealthMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  fetchSystemHealth: fetchSystemHealthMock,
}));

import { waitForBackendReady } from "./backendReadiness";

function healthy(body: unknown = {}): SystemHealthOutcome {
  return { ok: true, statusCode: 200, body };
}

function unhealthy(): SystemHealthOutcome {
  return { ok: false, error: "unreachable" };
}

/** A `sleep` double that resolves immediately — makes the up-to-~19s of
 * real backoff waiting the default policy would otherwise incur instant
 * and deterministic, per the M7.1 master prompt's explicit "make the
 * timeout/retry behavior deterministic enough to test" requirement. */
function instantSleep(): (ms: number) => Promise<void> {
  return () => Promise.resolve();
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("waitForBackendReady", () => {
  it("resolves ready:true with bootstrapRequired:false on the first successful attempt", async () => {
    fetchSystemHealthMock.mockResolvedValue(healthy({ bootstrap_required: false }));

    const outcome = await waitForBackendReady({ sleep: instantSleep() });

    expect(outcome).toEqual({ ready: true, bootstrapRequired: false });
    expect(fetchSystemHealthMock).toHaveBeenCalledTimes(1);
  });

  it("propagates bootstrap_required: true from the health body", async () => {
    fetchSystemHealthMock.mockResolvedValue(healthy({ bootstrap_required: true }));

    const outcome = await waitForBackendReady({ sleep: instantSleep() });

    expect(outcome).toEqual({ ready: true, bootstrapRequired: true });
  });

  it("treats a missing/malformed bootstrap_required field as false, never throwing", async () => {
    fetchSystemHealthMock.mockResolvedValueOnce(healthy(null));
    const first = await waitForBackendReady({ sleep: instantSleep() });
    expect(first).toEqual({ ready: true, bootstrapRequired: false });

    fetchSystemHealthMock.mockResolvedValueOnce(healthy({ bootstrap_required: "not-a-boolean" }));
    const second = await waitForBackendReady({ sleep: instantSleep() });
    expect(second).toEqual({ ready: true, bootstrapRequired: false });
  });

  it("retries on failure and succeeds once the backend becomes reachable", async () => {
    fetchSystemHealthMock
      .mockResolvedValueOnce(unhealthy())
      .mockResolvedValueOnce(unhealthy())
      .mockResolvedValueOnce(healthy({ bootstrap_required: false }));

    const outcome = await waitForBackendReady({ maxAttempts: 5, sleep: instantSleep() });

    expect(outcome).toEqual({ ready: true, bootstrapRequired: false });
    expect(fetchSystemHealthMock).toHaveBeenCalledTimes(3);
  });

  it("treats a thrown/rejected fetchSystemHealth call as not-ready-yet, not a crash", async () => {
    fetchSystemHealthMock.mockRejectedValueOnce(new Error("IPC bridge failure")).mockResolvedValueOnce(healthy());

    const outcome = await waitForBackendReady({ maxAttempts: 3, sleep: instantSleep() });

    expect(outcome).toEqual({ ready: true, bootstrapRequired: false });
  });

  it("gives up after maxAttempts, resolving ready:false — never an infinite loop", async () => {
    fetchSystemHealthMock.mockResolvedValue(unhealthy());

    const outcome = await waitForBackendReady({ maxAttempts: 4, sleep: instantSleep() });

    expect(outcome).toEqual({ ready: false });
    expect(fetchSystemHealthMock).toHaveBeenCalledTimes(4);
  });

  it("calls onAttempt once per attempt, in order, before each health check", async () => {
    fetchSystemHealthMock
      .mockResolvedValueOnce(unhealthy())
      .mockResolvedValueOnce(healthy({ bootstrap_required: false }));
    const attempts: Array<[number, number]> = [];

    await waitForBackendReady({
      maxAttempts: 5,
      sleep: instantSleep(),
      onAttempt: (attempt, maxAttempts) => attempts.push([attempt, maxAttempts]),
    });

    expect(attempts).toEqual([
      [1, 5],
      [2, 5],
    ]);
  });

  it("sleeps between attempts with bounded, doubling backoff — never after the final attempt", async () => {
    fetchSystemHealthMock.mockResolvedValue(unhealthy());
    const delays: number[] = [];
    const sleep = (ms: number) => {
      delays.push(ms);
      return Promise.resolve();
    };

    await waitForBackendReady({ maxAttempts: 4, sleep });

    // 3 sleeps for 4 attempts — no wait follows the last, exhausted attempt.
    expect(delays).toEqual([250, 500, 1000]);
  });

  it("caps backoff at 5 seconds rather than growing unbounded", async () => {
    fetchSystemHealthMock.mockResolvedValue(unhealthy());
    const delays: number[] = [];
    const sleep = (ms: number) => {
      delays.push(ms);
      return Promise.resolve();
    };

    await waitForBackendReady({ maxAttempts: 8, sleep });

    expect(Math.max(...delays)).toBe(5000);
    expect(delays[delays.length - 1]).toBe(5000);
  });

  it("defaults to 8 max attempts when unspecified", async () => {
    fetchSystemHealthMock.mockResolvedValue(unhealthy());

    await waitForBackendReady({ sleep: instantSleep() });

    expect(fetchSystemHealthMock).toHaveBeenCalledTimes(8);
  });
});
