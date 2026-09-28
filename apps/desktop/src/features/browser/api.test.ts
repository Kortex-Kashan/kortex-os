import { describe, expect, it, vi } from "vitest";

const { listenMock } = vi.hoisted(() => ({ listenMock: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: listenMock }));

import { onBrowserSurfaceNavigated } from "./api";

describe("Browser surface navigation events", () => {
  it("listens for completed navigations and hands over only the surface id", async () => {
    let deliver: ((event: { payload: unknown }) => void) | undefined;
    listenMock.mockImplementation(async (_name: string, callback: (event: { payload: unknown }) => void) => {
      deliver = callback;
      return () => undefined;
    });
    const handler = vi.fn();

    await onBrowserSurfaceNavigated(handler);
    expect(listenMock).toHaveBeenLastCalledWith("browser://surface-navigated", expect.any(Function));
    deliver?.({ payload: { surfaceId: "browser-surface-1" } });

    expect(handler.mock.calls).toEqual([["browser-surface-1"]]);
  });
});
