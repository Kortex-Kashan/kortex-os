import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { createMock, navigateMock, reloadMock, goBackMock, goForwardMock, setBoundsMock, queryMock, destroyMock } = vi.hoisted(() => ({
  createMock: vi.fn(),
  navigateMock: vi.fn(),
  reloadMock: vi.fn(),
  goBackMock: vi.fn(),
  goForwardMock: vi.fn(),
  setBoundsMock: vi.fn(),
  queryMock: vi.fn(),
  destroyMock: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    createBrowserSurface: createMock,
    navigateBrowserSurface: navigateMock,
    reloadBrowserSurface: reloadMock,
    goBackBrowserSurface: goBackMock,
    goForwardBrowserSurface: goForwardMock,
    setBrowserSurfaceBounds: setBoundsMock,
    queryBrowserSurfaceState: queryMock,
    destroyBrowserSurface: destroyMock,
  };
});

import { BrowserApp } from "./BrowserApp";

function stateFor(surfaceId: string, overrides: Partial<import("../api").BrowserSurfaceState> = {}) {
  return { surfaceId, url: "https://example.com/", loading: false, canGoBack: false, canGoForward: false, ...overrides };
}

beforeEach(() => {
  createMock.mockReset();
  navigateMock.mockReset();
  reloadMock.mockReset();
  goBackMock.mockReset();
  goForwardMock.mockReset();
  setBoundsMock.mockReset();
  queryMock.mockReset();
  destroyMock.mockReset();
  setBoundsMock.mockResolvedValue(undefined);
});

describe("BrowserApp", () => {
  it("opens exactly one real browser surface on mount — no frontend-only fake tab", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValueOnce(stateFor("browser-surface-1"));

    render(<BrowserApp />);

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    expect(createMock).toHaveBeenCalledWith("default", "https://example.com");
    expect(await screen.findAllByRole("tab")).toHaveLength(1);
  });

  it("navigates the active tab and refreshes its displayed URL", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock
      .mockResolvedValueOnce(stateFor("browser-surface-1"))
      .mockResolvedValueOnce(stateFor("browser-surface-1", { url: "https://example.org/" }));
    navigateMock.mockResolvedValueOnce(undefined);

    render(<BrowserApp />);
    // Waits for the initial `queryBrowserSurfaceState` round-trip to settle
    // (the address field synchronizes to the active tab's real URL whenever
    // it changes) before typing — matching realistic user timing; typing
    // any earlier would race that sync and have this same value overwritten
    // back to the tab's initial URL mid-edit.
    await screen.findByDisplayValue("https://example.com/");

    fireEvent.change(screen.getByTestId("browser-address-input"), { target: { value: "example.org" } });
    fireEvent.click(screen.getByTestId("browser-navigate-button"));

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("browser-surface-1", "https://example.org/"));
    expect(await screen.findByDisplayValue("https://example.org/")).toBeInTheDocument();
  });

  it("invokes back/forward/reload against the active tab through the runtime API, never a second WebView2 integration", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValue(stateFor("browser-surface-1", { canGoBack: true, canGoForward: true }));
    goBackMock.mockResolvedValueOnce(undefined);
    goForwardMock.mockResolvedValueOnce(undefined);
    reloadMock.mockResolvedValueOnce(undefined);

    render(<BrowserApp />);
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    // Waits for the initial state query to settle (`canGoBack`/`canGoForward`
    // both default to `false` until then) so the Back button is actually
    // enabled before the first click below.
    await waitFor(() => expect(screen.getByRole("button", { name: "Back" })).toBeEnabled());

    // Each action disables the toolbar until it (and the state refresh
    // after it) resolves — real UX to prevent concurrent operations on the
    // same surface, so each click here must be awaited before the next.
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(goBackMock).toHaveBeenCalledWith("browser-surface-1"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Forward" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "Forward" }));
    await waitFor(() => expect(goForwardMock).toHaveBeenCalledWith("browser-surface-1"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Reload" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(reloadMock).toHaveBeenCalledWith("browser-surface-1"));
  });

  it("opening a new tab creates a second real surface and shows two tabs", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1").mockResolvedValueOnce("browser-surface-2");
    queryMock.mockResolvedValue(stateFor("browser-surface-1"));

    render(<BrowserApp />);
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    await screen.findAllByRole("tab");

    fireEvent.click(screen.getByRole("button", { name: "New tab" }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(2));
    expect(await screen.findAllByRole("tab")).toHaveLength(2);
  });

  it("switching tabs repositions the newly-active surface into view and parks the other off-screen, without destroying either", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1").mockResolvedValueOnce("browser-surface-2");
    queryMock.mockResolvedValue(stateFor("browser-surface-1"));

    render(<BrowserApp />);
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "New tab" }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(2));

    setBoundsMock.mockClear();
    const tabs = await screen.findAllByRole("tab");
    fireEvent.click(tabs[0]);

    await waitFor(() => {
      const calls = setBoundsMock.mock.calls.map(([id]) => id);
      expect(calls).toContain("browser-surface-1");
      expect(calls).toContain("browser-surface-2");
    });
    expect(destroyMock).not.toHaveBeenCalled();
  });

  it("closing a tab destroys its real surface and removes it from the tab bar", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValue(stateFor("browser-surface-1"));
    destroyMock.mockResolvedValueOnce(undefined);

    render(<BrowserApp />);
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    await screen.findAllByRole("tab");

    fireEvent.click(screen.getByRole("button", { name: /close/i }));

    await waitFor(() => expect(destroyMock).toHaveBeenCalledWith("browser-surface-1"));
    await waitFor(() => expect(screen.queryAllByRole("tab")).toHaveLength(0));
  });

  it("shows a readable error message when navigation fails, without crashing the toolbar", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValueOnce(stateFor("browser-surface-1"));
    navigateMock.mockRejectedValueOnce({ kind: "platform", message: "invalid url: relative URL without a base" });

    render(<BrowserApp />);
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByTestId("browser-address-input"), { target: { value: "https://example.org" } });
    fireEvent.click(screen.getByTestId("browser-navigate-button"));

    expect(await screen.findByRole("alert")).toHaveTextContent("invalid url: relative URL without a base");
  });

  it("destroys every open tab's surface on unmount so no orphaned runtime is left behind", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1").mockResolvedValueOnce("browser-surface-2");
    queryMock.mockResolvedValue(stateFor("browser-surface-1"));

    const { unmount } = render(<BrowserApp />);
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "New tab" }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(2));

    unmount();

    await waitFor(() => {
      const destroyed = destroyMock.mock.calls.map(([id]) => id);
      expect(destroyed).toContain("browser-surface-1");
      expect(destroyed).toContain("browser-surface-2");
    });
  });

  // Adversarial-review defect 1
  it("destroys a surface whose creation resolves after the Browser view has already unmounted, rather than leaking it", async () => {
    let resolveCreate!: (id: string) => void;
    createMock.mockReturnValueOnce(
      new Promise<string>((resolve) => {
        resolveCreate = resolve;
      }),
    );

    const { unmount } = render(<BrowserApp />);
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));

    // Unmount BEFORE createBrowserSurface resolves — the exact race
    // adversarial review flagged: the surface is about to be created on the
    // Rust side with no owner left to track it.
    unmount();
    resolveCreate("browser-surface-1");

    await waitFor(() => expect(destroyMock).toHaveBeenCalledWith("browser-surface-1"));
    // It must never have been treated as a trackable tab in the meantime —
    // no `setBrowserSurfaceBounds`/`queryBrowserSurfaceState` call for it,
    // since there was no live component left to own it.
    expect(setBoundsMock).not.toHaveBeenCalledWith("browser-surface-1", expect.anything());
    expect(queryMock).not.toHaveBeenCalledWith("browser-surface-1");
  });

  // Adversarial-review defect 2
  it("keeps a tab tracked when destroying its surface fails, rather than losing track of a still-live surface", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValue(stateFor("browser-surface-1"));
    destroyMock.mockRejectedValueOnce({ kind: "platform", message: "transient IPC failure" });

    render(<BrowserApp />);
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    await screen.findAllByRole("tab");

    fireEvent.click(screen.getByRole("button", { name: /close/i }));

    await waitFor(() => expect(destroyMock).toHaveBeenCalledWith("browser-surface-1"));
    // The tab — and therefore its real, possibly-still-alive
    // BrowserSurfaceId — must remain tracked and visible, not silently
    // discarded, so the user can retry and nothing becomes unreachable.
    expect(await screen.findAllByRole("tab")).toHaveLength(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("transient IPC failure");

    // A successful retry still removes it normally.
    destroyMock.mockResolvedValueOnce(undefined);
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    await waitFor(() => expect(screen.queryAllByRole("tab")).toHaveLength(0));
  });

  // Adversarial-review defect 3
  it("surfaces a genuine state-refresh failure instead of leaving the tab silently stale", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock
      .mockResolvedValueOnce(stateFor("browser-surface-1"))
      .mockRejectedValueOnce({ kind: "platform", message: "backend unreachable" });
    navigateMock.mockResolvedValueOnce(undefined);

    render(<BrowserApp />);
    await screen.findByDisplayValue("https://example.com/");

    fireEvent.change(screen.getByTestId("browser-address-input"), { target: { value: "https://example.org" } });
    fireEvent.click(screen.getByTestId("browser-navigate-button"));

    expect(await screen.findByRole("alert")).toHaveTextContent("backend unreachable");
  });

  it("does not show an error when a state refresh finds its surface already destroyed", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock
      .mockResolvedValueOnce(stateFor("browser-surface-1"))
      .mockRejectedValueOnce({ kind: "surfaceNotFound", surfaceId: "browser-surface-1" });
    navigateMock.mockResolvedValueOnce(undefined);

    render(<BrowserApp />);
    await screen.findByDisplayValue("https://example.com/");

    fireEvent.change(screen.getByTestId("browser-address-input"), { target: { value: "https://example.org" } });
    fireEvent.click(screen.getByTestId("browser-navigate-button"));

    // Give the rejected refresh a chance to settle, then confirm no error
    // banner appeared — this is the one case that must stay silent.
    await waitFor(() => expect(navigateMock).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
