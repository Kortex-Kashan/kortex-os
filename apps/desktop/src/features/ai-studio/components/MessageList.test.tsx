import { fireEvent, render, screen } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import type { ChatMessage } from "../chat-types";
import { MessageList } from "./MessageList";

function message(overrides: Partial<ChatMessage> & { id: string }): ChatMessage {
  return {
    role: "assistant",
    content: "content",
    createdAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

/**
 * jsdom never computes real layout, so `scrollTop`/`scrollHeight`/
 * `clientHeight` are inert, unconfigurable-in-practice properties by
 * default. This installs real getter/setter behavior backed by a per-element
 * state bag, so `MessageList`'s own `scrollTop = scrollHeight` assignments
 * and its `scrollHeight`/`clientHeight` reads behave exactly as they would
 * against a real browser's layout — the standard technique for exercising
 * scroll-position logic under jsdom.
 *
 * `nextDefaults` controls the geometry a *newly created* element starts
 * with (read lazily, on first access — i.e. effectively "at mount"), which
 * is what lets a test simulate "this transcript already overflows its
 * viewport" from the very first render. `setMetrics` mutates an
 * already-mounted element's tracked state directly, for simulating a scroll
 * or a content-size change that happens after mount.
 */
let nextDefaults = { scrollTop: 0, scrollHeight: 0, clientHeight: 0 };
const scrollState = new WeakMap<Element, { scrollTop: number; scrollHeight: number; clientHeight: number }>();

function stateFor(el: Element) {
  let state = scrollState.get(el);
  if (!state) {
    state = { ...nextDefaults };
    scrollState.set(el, state);
  }
  return state;
}

function setMetrics(el: Element, patch: Partial<{ scrollTop: number; scrollHeight: number; clientHeight: number }>) {
  Object.assign(stateFor(el), patch);
}

let originalDescriptors: Record<string, PropertyDescriptor | undefined>;

beforeAll(() => {
  originalDescriptors = {
    scrollTop: Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollTop"),
    scrollHeight: Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight"),
    clientHeight: Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight"),
  };
  Object.defineProperty(HTMLElement.prototype, "scrollTop", {
    configurable: true,
    get(this: Element) {
      return stateFor(this).scrollTop;
    },
    set(this: Element, value: number) {
      stateFor(this).scrollTop = value;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get(this: Element) {
      return stateFor(this).scrollHeight;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get(this: Element) {
      return stateFor(this).clientHeight;
    },
  });
});

afterAll(() => {
  for (const [prop, descriptor] of Object.entries(originalDescriptors)) {
    if (descriptor) {
      Object.defineProperty(HTMLElement.prototype, prop, descriptor);
    }
  }
});

beforeEach(() => {
  nextDefaults = { scrollTop: 0, scrollHeight: 0, clientHeight: 0 };
});

function getLog(): HTMLElement {
  return screen.getByRole("log", { name: "Chat transcript" });
}

describe("MessageList", () => {
  it("renders the empty state and no scroll container when there are no messages", () => {
    render(<MessageList messages={[]} />);

    expect(screen.getByText("No messages yet. Say hello to get started.")).toBeInTheDocument();
    expect(screen.queryByRole("log")).not.toBeInTheDocument();
  });

  it("scrolls to the latest content on initial load", () => {
    // The transcript already overflows its viewport the moment it mounts
    // (e.g. rehydrated history longer than the panel) -- the very first
    // render must still land at the bottom, not wherever scrollTop=0 leaves it.
    nextDefaults = { scrollTop: 0, scrollHeight: 1000, clientHeight: 300 };

    render(
      <MessageList
        messages={[message({ id: "1", content: "first" }), message({ id: "2", content: "second" })]}
      />,
    );

    expect(getLog().scrollTop).toBe(1000);
  });

  it("keeps following (auto-scrolls) new messages while the user is at the bottom", () => {
    const initial = [message({ id: "1", content: "first" })];
    const { rerender } = render(<MessageList messages={initial} />);
    const log = getLog();
    setMetrics(log, { scrollTop: 0, scrollHeight: 400, clientHeight: 400 });

    setMetrics(log, { scrollHeight: 700 });
    rerender(<MessageList messages={[...initial, message({ id: "2", content: "second" })]} />);

    expect(log.scrollTop).toBe(700);
    expect(screen.queryByRole("button", { name: "Scroll to latest" })).not.toBeInTheDocument();
  });

  it("follows a long response as its content grows in place (streaming-shaped update)", () => {
    const growing = message({ id: "streaming", content: "a" });
    const { rerender } = render(<MessageList messages={[growing]} />);
    const log = getLog();
    setMetrics(log, { scrollTop: 0, scrollHeight: 300, clientHeight: 300 });

    // Same message id, growing content -- the shape an incremental/streamed
    // update would take even without the message count changing.
    setMetrics(log, { scrollHeight: 900 });
    rerender(<MessageList messages={[{ ...growing, content: "a".repeat(500) }]} />);

    expect(log.scrollTop).toBe(900);
  });

  it("stops auto-following once the user manually scrolls away from the bottom", () => {
    const initial = [message({ id: "1", content: "first" })];
    const { rerender } = render(<MessageList messages={initial} />);
    const log = getLog();

    // Simulate a real user scroll far from the bottom of a tall transcript.
    setMetrics(log, { scrollTop: 0, scrollHeight: 2000, clientHeight: 300 });
    fireEvent.scroll(log);

    expect(screen.getByRole("button", { name: "Scroll to latest" })).toBeInTheDocument();

    // A new message arrives while the user is reading older content --
    // must NOT yank them back down.
    setMetrics(log, { scrollHeight: 2400 });
    rerender(<MessageList messages={[...initial, message({ id: "2", content: "second" })]} />);

    expect(log.scrollTop).toBe(0);
    expect(screen.getByRole("button", { name: "Scroll to latest" })).toBeInTheDocument();
  });

  it('"Scroll to latest" jumps to the bottom and resumes auto-follow', () => {
    const initial = [message({ id: "1", content: "first" })];
    const { rerender } = render(<MessageList messages={initial} />);
    const log = getLog();
    setMetrics(log, { scrollTop: 0, scrollHeight: 2000, clientHeight: 300 });
    fireEvent.scroll(log);
    expect(screen.getByRole("button", { name: "Scroll to latest" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Scroll to latest" }));

    expect(log.scrollTop).toBe(2000);
    expect(screen.queryByRole("button", { name: "Scroll to latest" })).not.toBeInTheDocument();

    // Auto-follow resumed: the next message keeps the viewport pinned again.
    setMetrics(log, { scrollHeight: 2400 });
    rerender(<MessageList messages={[...initial, message({ id: "2", content: "second" })]} />);

    expect(log.scrollTop).toBe(2400);
  });

  it("does not treat being near (but not exactly at) the bottom as having scrolled away", () => {
    const initial = [message({ id: "1", content: "first" })];
    const { rerender } = render(<MessageList messages={initial} />);
    const log = getLog();

    // 20px of unscrolled content -- comfortably inside the near-bottom
    // threshold, e.g. rounding from a fractional layout.
    setMetrics(log, { scrollTop: 780, scrollHeight: 1100, clientHeight: 300 });
    fireEvent.scroll(log);

    expect(screen.queryByRole("button", { name: "Scroll to latest" })).not.toBeInTheDocument();

    setMetrics(log, { scrollHeight: 1400 });
    rerender(<MessageList messages={[...initial, message({ id: "2", content: "second" })]} />);

    expect(log.scrollTop).toBe(1400);
  });

  it("treats a genuine scroll far from the bottom as scrolled away even on a tall initial load", () => {
    nextDefaults = { scrollTop: 0, scrollHeight: 2000, clientHeight: 300 };
    render(<MessageList messages={[message({ id: "1", content: "first" })]} />);
    const log = getLog();

    // The mount effect already forced scrollTop to 2000 (the bottom). The
    // user then scrolls back up to read earlier content.
    expect(log.scrollTop).toBe(2000);
    setMetrics(log, { scrollTop: 200 });
    fireEvent.scroll(log);

    expect(screen.getByRole("button", { name: "Scroll to latest" })).toBeInTheDocument();
  });
});
