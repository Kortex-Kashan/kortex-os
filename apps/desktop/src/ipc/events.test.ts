import { describe, expect, it, vi } from "vitest";
import { connectEventStream, onEventStreamStatus, onKortexEvent } from "@/ipc/events";

const { invokeMock, listenMock } = vi.hoisted(() => ({
  invokeMock: vi.fn(),
  listenMock: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: invokeMock,
}));

vi.mock("@tauri-apps/api/event", () => ({
  listen: listenMock,
}));

describe("connectEventStream", () => {
  it("invokes connect_event_stream with the given topic", async () => {
    invokeMock.mockResolvedValueOnce(true);
    const started = await connectEventStream("kortex.event.hr.*");
    expect(invokeMock).toHaveBeenCalledWith("connect_event_stream", { topic: "kortex.event.hr.*" });
    expect(started).toBe(true);
  });

  it("defaults topic to undefined (Rust defaults to '*')", async () => {
    invokeMock.mockResolvedValueOnce(false);
    await connectEventStream();
    expect(invokeMock).toHaveBeenCalledWith("connect_event_stream", { topic: undefined });
  });
});

describe("onKortexEvent", () => {
  it("subscribes to kortex://event and unwraps the payload for the handler", async () => {
    let capturedCallback: ((event: { payload: unknown }) => void) | undefined;
    listenMock.mockImplementation((_topic: string, callback: typeof capturedCallback) => {
      capturedCallback = callback;
      return Promise.resolve(() => {});
    });

    const handler = vi.fn();
    await onKortexEvent(handler);

    expect(listenMock).toHaveBeenCalledWith("kortex://event", expect.any(Function));
    const fakeEvent = { topic: "kortex.event.test.created", payload: { foo: "bar" }, eventId: "e1", correlationId: "c1", timestampUtc: "now" };
    capturedCallback?.({ payload: fakeEvent });
    expect(handler).toHaveBeenCalledWith(fakeEvent);
  });
});

describe("onEventStreamStatus", () => {
  it("subscribes to kortex://event-stream-status", async () => {
    listenMock.mockResolvedValueOnce(() => {});
    await onEventStreamStatus(vi.fn());
    expect(listenMock).toHaveBeenCalledWith("kortex://event-stream-status", expect.any(Function));
  });
});
