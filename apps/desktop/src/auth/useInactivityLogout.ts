import * as React from "react";

/**
 * AI Studio functional stabilization, Phase F — true one-hour inactivity
 * logout.
 *
 * Production default: exactly 60 minutes of no genuine user interaction.
 * Injectable via `options.timeoutMs` so tests can exercise this with an
 * accelerated threshold instead of waiting a real hour — the constant here
 * is what the production build actually uses when no override is passed.
 */
export const INACTIVITY_TIMEOUT_MS = 60 * 60 * 1000;

/**
 * How often the heartbeat fires while the session is enabled, well under
 * the backend's own `_TOKEN_TTL` (15 minutes, `auth.py`) so an actively
 * used -- or merely actively *watched* -- session's underlying token is
 * reissued before it can expire on the wall clock alone. Also injectable
 * for tests.
 */
export const HEARTBEAT_INTERVAL_MS = 5 * 60 * 1000;

/**
 * Real, physical user-interaction events only. Deliberately excludes
 * anything automated: no `visibilitychange`, no `focus` (a window can
 * regain focus without any human input, e.g. an OS-level window manager
 * action), no interval/timeout-driven signal of any kind. This is the
 * literal list Phase F's requirement enumerates — mouse movement, keyboard
 * activity, click, and touch/scroll as the touch-device equivalents of
 * "the user is physically present and interacting."
 */
const ACTIVITY_EVENT_NAMES = ["mousemove", "mousedown", "keydown", "touchstart", "wheel"] as const;

export interface UseInactivityLogoutOptions {
  /** Overrides `INACTIVITY_TIMEOUT_MS` — tests only; production omits this. */
  timeoutMs?: number;
  /** Overrides `HEARTBEAT_INTERVAL_MS` — tests only; production omits this. */
  heartbeatIntervalMs?: number;
}

/**
 * Wires a real-activity-driven inactivity timer and a separate,
 * activity-independent heartbeat while `enabled` is true (in practice:
 * while `AuthState.status === "AUTHENTICATED"`).
 *
 * `onTimeout` fires once, `timeoutMs` after the most recent genuine
 * interaction event, and every such event resets the countdown. Only one
 * `setTimeout` is ever pending at a time — resetting clears the previous
 * one first, so activity cannot accumulate duplicate pending timeouts.
 *
 * `onHeartbeat` fires on its own fixed interval, entirely independent of
 * `onTimeout`'s countdown: it is not "activity" and must never reset the
 * inactivity timer (Phase F's explicit requirement — background polling
 * must not count as activity). Its purpose is orthogonal: giving the
 * caller a chance to keep a short-lived backend token alive via a real
 * authenticated capability call for as long as this hook considers the
 * session still within its inactivity window; once `onTimeout` fires the
 * caller is expected to become `enabled={false}` (session ended), which
 * tears down the heartbeat too.
 *
 * Cleanup: the single effect below removes every listener and clears both
 * the pending timeout and the interval whenever `enabled` (or either
 * override) changes, or on unmount — there is never more than one set of
 * listeners/timers alive at once, and disabling (e.g. on logout) leaves
 * nothing running in the background.
 */
export function useInactivityLogout(
  enabled: boolean,
  onTimeout: () => void,
  onHeartbeat: () => void,
  options: UseInactivityLogoutOptions = {},
): void {
  const timeoutMs = options.timeoutMs ?? INACTIVITY_TIMEOUT_MS;
  const heartbeatIntervalMs = options.heartbeatIntervalMs ?? HEARTBEAT_INTERVAL_MS;

  // Refs so a re-render with a new (but referentially-unequal) callback
  // identity never tears down and re-installs the listeners/timers below —
  // only `enabled`/`timeoutMs`/`heartbeatIntervalMs` changing should do that.
  const onTimeoutRef = React.useRef(onTimeout);
  onTimeoutRef.current = onTimeout;
  const onHeartbeatRef = React.useRef(onHeartbeat);
  onHeartbeatRef.current = onHeartbeat;

  React.useEffect(() => {
    if (!enabled) {
      return undefined;
    }

    let idleTimeoutHandle: ReturnType<typeof setTimeout>;

    function resetIdleTimer() {
      clearTimeout(idleTimeoutHandle);
      idleTimeoutHandle = setTimeout(() => onTimeoutRef.current(), timeoutMs);
    }

    resetIdleTimer();
    for (const eventName of ACTIVITY_EVENT_NAMES) {
      window.addEventListener(eventName, resetIdleTimer, { passive: true });
    }

    const heartbeatHandle = setInterval(() => onHeartbeatRef.current(), heartbeatIntervalMs);

    return () => {
      clearTimeout(idleTimeoutHandle);
      clearInterval(heartbeatHandle);
      for (const eventName of ACTIVITY_EVENT_NAMES) {
        window.removeEventListener(eventName, resetIdleTimer);
      }
    };
  }, [enabled, timeoutMs, heartbeatIntervalMs]);
}
