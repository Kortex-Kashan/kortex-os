import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HEARTBEAT_INTERVAL_MS, INACTIVITY_TIMEOUT_MS, useInactivityLogout } from "./useInactivityLogout";

function fireActivity(eventName: string) {
  window.dispatchEvent(new Event(eventName));
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useInactivityLogout", () => {
  it("does not fire before the configured timeout elapses", () => {
    const onTimeout = vi.fn();
    renderHook(() => useInactivityLogout(true, onTimeout, vi.fn(), { timeoutMs: 1000 }));

    vi.advanceTimersByTime(999);

    expect(onTimeout).not.toHaveBeenCalled();
  });

  it("fires once the configured timeout elapses with no activity", () => {
    const onTimeout = vi.fn();
    renderHook(() => useInactivityLogout(true, onTimeout, vi.fn(), { timeoutMs: 1000 }));

    vi.advanceTimersByTime(1000);

    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it("resets the countdown on genuine user activity (mousemove/keydown/click/touch/scroll)", () => {
    const onTimeout = vi.fn();
    renderHook(() => useInactivityLogout(true, onTimeout, vi.fn(), { timeoutMs: 1000 }));

    // Activity at the 900ms mark, repeatedly, well before the 1000ms
    // threshold would fire -- each one must push the deadline out again.
    for (const eventName of ["mousemove", "keydown", "mousedown", "touchstart", "wheel"]) {
      vi.advanceTimersByTime(900);
      fireActivity(eventName);
    }
    expect(onTimeout).not.toHaveBeenCalled();

    // No further activity -- now it should fire exactly `timeoutMs` after
    // the LAST reset above.
    vi.advanceTimersByTime(1000);
    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it("does NOT reset the countdown on a heartbeat firing -- background activity is not user activity", () => {
    const onTimeout = vi.fn();
    const onHeartbeat = vi.fn();
    renderHook(() =>
      useInactivityLogout(true, onTimeout, onHeartbeat, { timeoutMs: 1000, heartbeatIntervalMs: 300 }),
    );

    // Several heartbeats fire well before the 1000ms inactivity deadline,
    // but none of them are activity -- the deadline must still land at
    // exactly 1000ms of wall-clock time from mount, not be pushed out.
    vi.advanceTimersByTime(999);
    expect(onHeartbeat.mock.calls.length).toBeGreaterThan(0);
    expect(onTimeout).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it("does not treat an arbitrary/synthetic non-activity event as activity", () => {
    const onTimeout = vi.fn();
    renderHook(() => useInactivityLogout(true, onTimeout, vi.fn(), { timeoutMs: 1000 }));

    vi.advanceTimersByTime(900);
    // Not in the tracked activity event list.
    fireActivity("visibilitychange");
    fireActivity("focus");
    vi.advanceTimersByTime(100);

    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it("fires the heartbeat on its own fixed interval, independent of activity", () => {
    const onHeartbeat = vi.fn();
    renderHook(() =>
      useInactivityLogout(true, vi.fn(), onHeartbeat, { timeoutMs: 10_000, heartbeatIntervalMs: 500 }),
    );

    vi.advanceTimersByTime(500);
    expect(onHeartbeat).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1000);
    expect(onHeartbeat).toHaveBeenCalledTimes(3);
  });

  it("does nothing at all while disabled", () => {
    const onTimeout = vi.fn();
    const onHeartbeat = vi.fn();
    renderHook(() =>
      useInactivityLogout(false, onTimeout, onHeartbeat, { timeoutMs: 100, heartbeatIntervalMs: 100 }),
    );

    vi.advanceTimersByTime(10_000);

    expect(onTimeout).not.toHaveBeenCalled();
    expect(onHeartbeat).not.toHaveBeenCalled();
  });

  it("tears down the timer, interval, and listeners when disabled after being enabled -- no logout/heartbeat after that point", () => {
    const onTimeout = vi.fn();
    const onHeartbeat = vi.fn();
    const removeSpy = vi.spyOn(window, "removeEventListener");
    const { rerender } = renderHook(
      ({ enabled }) => useInactivityLogout(enabled, onTimeout, onHeartbeat, { timeoutMs: 1000, heartbeatIntervalMs: 200 }),
      { initialProps: { enabled: true } },
    );

    vi.advanceTimersByTime(300);
    expect(onHeartbeat.mock.calls.length).toBeGreaterThan(0);

    rerender({ enabled: false });

    // Every activity-event listener that was installed must be removed.
    const activityEvents = ["mousemove", "mousedown", "keydown", "touchstart", "wheel"];
    for (const eventName of activityEvents) {
      expect(removeSpy).toHaveBeenCalledWith(eventName, expect.any(Function));
    }

    const heartbeatCallsAtDisable = onHeartbeat.mock.calls.length;
    vi.advanceTimersByTime(10_000);

    expect(onTimeout).not.toHaveBeenCalled();
    expect(onHeartbeat.mock.calls.length).toBe(heartbeatCallsAtDisable);
    removeSpy.mockRestore();
  });

  it("tears down on unmount, leaving no pending timer/interval/listeners", () => {
    const onTimeout = vi.fn();
    const onHeartbeat = vi.fn();
    const { unmount } = renderHook(() =>
      useInactivityLogout(true, onTimeout, onHeartbeat, { timeoutMs: 1000, heartbeatIntervalMs: 200 }),
    );

    unmount();
    vi.advanceTimersByTime(10_000);

    expect(onTimeout).not.toHaveBeenCalled();
  });

  it("never installs more than one listener per event across re-renders with unchanged enabled/timeoutMs", () => {
    const addSpy = vi.spyOn(window, "addEventListener");
    const onTimeout = vi.fn();
    const { rerender } = renderHook(
      ({ cb }: { cb: () => void }) => useInactivityLogout(true, cb, vi.fn(), { timeoutMs: 1000 }),
      { initialProps: { cb: onTimeout } },
    );
    const callsAfterMount = addSpy.mock.calls.filter((call) => call[0] === "mousemove").length;
    expect(callsAfterMount).toBe(1);

    // A new callback identity alone (the common case: the consumer's
    // `logout`/`onHeartbeat` closures are re-created every render) must
    // NOT tear down and reinstall the listeners -- only enabled/timeoutMs/
    // heartbeatIntervalMs changing should.
    rerender({ cb: vi.fn() });
    rerender({ cb: vi.fn() });

    const callsAfterRerenders = addSpy.mock.calls.filter((call) => call[0] === "mousemove").length;
    expect(callsAfterRerenders).toBe(1);
    addSpy.mockRestore();
  });

  it("exports the documented production defaults", () => {
    expect(INACTIVITY_TIMEOUT_MS).toBe(60 * 60 * 1000);
    expect(HEARTBEAT_INTERVAL_MS).toBe(5 * 60 * 1000);
  });
});
