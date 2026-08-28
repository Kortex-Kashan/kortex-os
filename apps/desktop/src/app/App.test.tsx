import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";

// `App` mounts `useKortexEventStream` (M3) and, since M4.1, `AuthProvider`'s
// startup session check — both call the real `@tauri-apps/api` core/event
// modules on mount, which reach into `window.__TAURI_INTERNALS__` (absent
// outside a real Tauri webview). Mocked here (not globally) so this remains
// a plain component smoke test rather than exercising IPC transport, which
// `ipc/client.test.ts`, `ipc/session.test.ts`, and `ipc/events.test.ts`
// already cover directly.
const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn().mockResolvedValue(() => {}) }));

describe("App", () => {
  it("renders the login screen, never the shell, when no session is stored", async () => {
    invokeMock.mockResolvedValue(false);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("KORTEX OS")).not.toBeInTheDocument();
  });

  it("renders the desktop shell with its workspace empty state once a stored session validates", async () => {
    invokeMock.mockImplementation((command: string) => {
      if (command === "has_session") {
        return Promise.resolve(true);
      }
      if (command === "invoke_capability") {
        return Promise.resolve({
          requestId: "req-1",
          correlationId: "corr-1",
          status: "SUCCESS",
          payload: null,
          errors: [],
          warnings: [],
          executionDurationMs: 1,
          httpStatus: 200,
        });
      }
      return Promise.resolve(false);
    });

    render(<App />);

    expect(await screen.findByText("KORTEX OS")).toBeInTheDocument();
    expect(screen.getByText("No application mounted")).toBeInTheDocument();
  });
});
