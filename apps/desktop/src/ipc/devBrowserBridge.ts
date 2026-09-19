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
