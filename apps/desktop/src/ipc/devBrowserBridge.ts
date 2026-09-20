/**
 * Dev-only browser bridge for when running `pnpm dev` in a standard browser
 * outside of the Tauri runtime.
 *
 * Tauri's `@tauri-apps/api/core` uses `window.__TAURI_INTERNALS__.invoke`.
 * In Tauri webview, that is injected by Rust.
 * In a standard browser (http://localhost:1420), this bridge routes IPC commands
 * through Vite's dev proxy to the local backend and stores the session token
 * in sessionStorage, exactly mirroring `apps/desktop/src-tauri/src/ipc.rs`.
 */

import { mockIPC } from "@tauri-apps/api/mocks";

const PROXY_PREFIX = "/dev-ipc";
const SESSION_KEY = "kortex_desktop_dev_session_token";
// Phase F security correction: a distinct dev-mode storage slot mirroring
// Rust's own separate keychain entry for the refresh token -- never read
// by the generic `invoke_capability` case below, only by `refresh_session`.
const REFRESH_SESSION_KEY = "kortex_desktop_dev_refresh_token";
const REFRESH_CAPABILITY_NAME = "kortex.security.auth.refresh";

export function initDevBrowserBridge(): void {
  if (!import.meta.env.DEV || typeof window === "undefined") {
    return;
  }
  // If Tauri internals are already present from real Tauri Webview, do nothing.
  if ((window as unknown as { __TAURI_INTERNALS__?: { invoke?: unknown } }).__TAURI_INTERNALS__?.invoke) {
    return;
  }

  mockIPC(
    async (cmd, args: unknown) => {
      switch (cmd) {
        case "get_system_health": {
          try {
            const res = await fetch(`${PROXY_PREFIX}/health`);
            const body = await res.json();
            return { ok: res.ok, statusCode: res.status, body };
          } catch (e) {
            return { ok: false, error: (e as Error).message };
          }
        }

        case "has_session": {
          return sessionStorage.getItem(SESSION_KEY) !== null;
        }

        case "logout": {
          sessionStorage.removeItem(SESSION_KEY);
          // Phase F: logout must end both credentials' usefulness, not
          // just the access token's -- mirrors Rust's `logout` command.
          sessionStorage.removeItem(REFRESH_SESSION_KEY);
          return;
        }

        case "invoke_capability": {
          const typedArgs = args as { request?: { requestId?: string; correlationId?: string } } | undefined;
          const token = sessionStorage.getItem(SESSION_KEY);
          const headers: Record<string, string> = {
            "Content-Type": "application/json",
          };
          if (token) {
            headers["Authorization"] = `Bearer ${token}`;
          }
          try {
            const res = await fetch(`${PROXY_PREFIX}/capabilities/invoke`, {
              method: "POST",
              headers,
              body: JSON.stringify(typedArgs?.request ?? {}),
            });
            const envelope = await res.json();
            envelope.httpStatus = res.status;
            if (envelope.sessionToken) {
              sessionStorage.setItem(SESSION_KEY, envelope.sessionToken);
              delete envelope.sessionToken;
            }
            if (envelope.refreshToken) {
              sessionStorage.setItem(REFRESH_SESSION_KEY, envelope.refreshToken);
              delete envelope.refreshToken;
            }
            return envelope;
          } catch (e) {
            return {
              requestId: typedArgs?.request?.requestId ?? "dev-error",
              correlationId: typedArgs?.request?.correlationId ?? "dev-error",
              status: "FAILURE",
              payload: null,
              errors: [
                {
                  category: "SERVICE_UNAVAILABLE",
                  message: `Backend unreachable: ${(e as Error).message}`,
                  correlationId: "dev-error",
                },
              ],
              warnings: [],
              executionDurationMs: 0,
              httpStatus: undefined,
            };
          }
        }

        // Phase F security correction: mirrors Rust's `refresh_session`
        // command -- exchanges the dev-stored refresh token for a fresh
        // access token via the same `kortex.security.auth.refresh`
        // capability, never attaching the refresh token as a Bearer
        // credential. Returns a synthetic FAILURE envelope without any
        // network call when no refresh token is held, matching Rust's own
        // "nothing to exchange" short-circuit.
        case "refresh_session": {
          const refreshToken = sessionStorage.getItem(REFRESH_SESSION_KEY);
          if (!refreshToken) {
            return {
              requestId: "dev-refresh-no-token",
              correlationId: "dev-refresh-no-token",
              status: "FAILURE",
              payload: null,
              errors: [
                {
                  category: "PERMISSION_DENIED",
                  message: "No refresh token is held.",
                  correlationId: "dev-refresh-no-token",
                },
              ],
              warnings: [],
              executionDurationMs: 0,
              httpStatus: undefined,
            };
          }
          const accessToken = sessionStorage.getItem(SESSION_KEY);
          const headers: Record<string, string> = { "Content-Type": "application/json" };
          if (accessToken) {
            headers["Authorization"] = `Bearer ${accessToken}`;
          }
          try {
            const res = await fetch(`${PROXY_PREFIX}/capabilities/invoke`, {
              method: "POST",
              headers,
              body: JSON.stringify({
                requestId: "dev-refresh",
                capabilityName: REFRESH_CAPABILITY_NAME,
                parameters: { refresh_token: refreshToken },
              }),
            });
            const envelope = await res.json();
            envelope.httpStatus = res.status;
            if (envelope.sessionToken) {
              sessionStorage.setItem(SESSION_KEY, envelope.sessionToken);
              delete envelope.sessionToken;
            }
            if (envelope.refreshToken) {
              sessionStorage.setItem(REFRESH_SESSION_KEY, envelope.refreshToken);
              delete envelope.refreshToken;
            }
            return envelope;
          } catch (e) {
            return {
              requestId: "dev-refresh-error",
              correlationId: "dev-refresh-error",
              status: "FAILURE",
              payload: null,
              errors: [
                {
                  category: "SERVICE_UNAVAILABLE",
                  message: `Backend unreachable: ${(e as Error).message}`,
                  correlationId: "dev-refresh-error",
                },
              ],
              warnings: [],
              executionDurationMs: 0,
              httpStatus: undefined,
            };
          }
        }

        case "connect_event_stream": {
          return true;
        }

        default:
          console.warn(`[DevBrowserBridge] Unhandled IPC command: ${cmd}`, args);
          return null;
      }
    },
    { shouldMockEvents: true },
  );
}
