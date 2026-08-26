import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";

// `App` mounts `useKortexEventStream` (M3), which calls the real
// `@tauri-apps/api` core/event modules on mount — those reach into
// `window.__TAURI_INTERNALS__`, which does not exist outside a real Tauri
// webview. Mocked here (not globally) so this remains a plain component
// smoke test rather than exercising IPC transport, which `ipc/client.test.ts`
// and `ipc/events.test.ts` already cover directly.
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn().mockResolvedValue(false) }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn().mockResolvedValue(() => {}) }));

describe("App", () => {
  it("renders the desktop shell with its workspace empty state", () => {
    render(<App />);
    expect(screen.getByText("KORTEX OS")).toBeInTheDocument();
    expect(screen.getByText("No application mounted")).toBeInTheDocument();
  });
});
