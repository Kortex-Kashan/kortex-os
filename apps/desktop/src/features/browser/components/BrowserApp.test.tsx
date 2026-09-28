import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  createMock,
  navigateMock,
  reloadMock,
  goBackMock,
  goForwardMock,
  setBoundsMock,
  queryMock,
  destroyMock,
  listProfilesMock,
  createProfileMock,
  renameProfileMock,
  deleteProfileMock,
  onPolicyDeniedMock,
  onNavigatedMock,
  setActiveProfileMock,
} = vi.hoisted(() => ({
  createMock: vi.fn(),
  navigateMock: vi.fn(),
  reloadMock: vi.fn(),
  goBackMock: vi.fn(),
  goForwardMock: vi.fn(),
  setBoundsMock: vi.fn(),
  queryMock: vi.fn(),
  destroyMock: vi.fn(),
  listProfilesMock: vi.fn(),
  createProfileMock: vi.fn(),
  renameProfileMock: vi.fn(),
  deleteProfileMock: vi.fn(),
  // Real `onBrowserPolicyDenied` wraps `@tauri-apps/api/event`'s `listen()`,
  // which reaches into `window.__TAURI_INTERNALS__` — never present in this
  // jsdom test environment (no other test in this file mocks the Tauri IPC
  // bridge either). Mocked here the same way every other `../api` call is,
  // to a resolved no-op unlisten, so `useBrowserTabs`'s policy-denied
  // subscription effect never touches the real bridge.
  onPolicyDeniedMock: vi.fn(),
  onNavigatedMock: vi.fn(),
  setActiveProfileMock: vi.fn(),
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
    listBrowserProfiles: listProfilesMock,
    createBrowserProfile: createProfileMock,
    renameBrowserProfile: renameProfileMock,
    deleteBrowserProfile: deleteProfileMock,
    onBrowserPolicyDenied: onPolicyDeniedMock,
    onBrowserSurfaceNavigated: onNavigatedMock,
    setActiveBrowserProfile: setActiveProfileMock,
  };
});

import { BROWSER_NEW_TAB_URL } from "../newTab";
import { BrowserApp } from "./BrowserApp";

const TEST_PROFILE_ID = "profile-test-1";

function stateFor(surfaceId: string, overrides: Partial<import("../api").BrowserSurfaceState> = {}) {
  return { surfaceId, url: "https://example.com/", loading: false, canGoBack: false, canGoForward: false, ...overrides };
}

function testProfile(overrides: Partial<import("../api").ProfileSummary> = {}) {
  return {
    profileId: TEST_PROFILE_ID,
    displayName: "Default",
    createdAt: 0,
    lastOpenedAt: null,
    availability: { kind: "available" as const },
    ...overrides,
  };
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
  listProfilesMock.mockReset();
  createProfileMock.mockReset();
  renameProfileMock.mockReset();
  deleteProfileMock.mockReset();
  onPolicyDeniedMock.mockReset();
  onNavigatedMock.mockReset();
  setActiveProfileMock.mockReset();
  setBoundsMock.mockResolvedValue(undefined);
  onPolicyDeniedMock.mockResolvedValue(() => {});
  onNavigatedMock.mockResolvedValue(() => {});
  setActiveProfileMock.mockResolvedValue(undefined);
  // A single already-existing, available profile by default — most tests
  // exercise tab behavior, not profile auto-creation/switching, so they
  // don't need `useBrowserProfiles` to take the auto-create path.
  listProfilesMock.mockResolvedValue([testProfile()]);
});

describe("BrowserApp", () => {
  it("opens exactly one real browser surface on mount — no frontend-only fake tab", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValueOnce(stateFor("browser-surface-1"));

    render(<BrowserApp />);

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    expect(createMock).toHaveBeenCalledWith(TEST_PROFILE_ID, BROWSER_NEW_TAB_URL);
    expect(await screen.findAllByRole("tab")).toHaveLength(1);
  });

  it("opens every genuinely new tab at the single BROWSER_NEW_TAB_URL constant", async () => {
    expect(BROWSER_NEW_TAB_URL).toBe("https://www.google.com/");
    createMock.mockResolvedValueOnce("browser-surface-1").mockResolvedValueOnce("browser-surface-2");
    queryMock.mockResolvedValue(stateFor("browser-surface-1"));

    render(<BrowserApp />);
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "New tab" }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(2));

    expect(createMock.mock.calls.map(([, url]) => url)).toEqual([BROWSER_NEW_TAB_URL, BROWSER_NEW_TAB_URL]);
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

  it("re-reads a tab's state when its surface reports a completed navigation, so the label shows the new page", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValueOnce(stateFor("browser-surface-1", { url: "https://www.google.com/" }));
    navigateMock.mockResolvedValueOnce(undefined);

    render(<BrowserApp />);
    await screen.findByText("www.google.com");
    await waitFor(() => expect(onNavigatedMock).toHaveBeenCalled());
    const notifyNavigated = onNavigatedMock.mock.calls[0][0] as (surfaceId: string) => void;

    // The navigate command returns before the page loads: its own refresh
    // still sees the old page.
    queryMock.mockResolvedValueOnce(stateFor("browser-surface-1", { url: "https://www.google.com/" }));
    fireEvent.change(screen.getByTestId("browser-address-input"), { target: { value: "https://example.org" } });
    fireEvent.click(screen.getByTestId("browser-navigate-button"));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("browser-surface-1", "https://example.org/"));

    queryMock.mockResolvedValue(stateFor("browser-surface-1", { url: "https://example.org/" }));
    notifyNavigated("browser-surface-1");

    expect(await screen.findByText("example.org")).toBeInTheDocument();
    expect(screen.getByTestId("browser-address-input")).toHaveValue("https://example.org/");
  });

  it("ignores a navigation event for a surface it does not track", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValue(stateFor("browser-surface-1"));

    render(<BrowserApp />);
    await screen.findAllByRole("tab");
    await waitFor(() => expect(onNavigatedMock).toHaveBeenCalled());
    const calls = queryMock.mock.calls.length;

    (onNavigatedMock.mock.calls[0][0] as (surfaceId: string) => void)("provider-auth-elsewhere");

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(queryMock).toHaveBeenCalledTimes(calls);
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

  describe("Browser-B3 profiles", () => {
    it("auto-creates a Default profile when the current tenant has none yet", async () => {
      listProfilesMock
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([testProfile({ displayName: "Default" })]);
      createProfileMock.mockResolvedValueOnce(TEST_PROFILE_ID);
      createMock.mockResolvedValueOnce("browser-surface-1");
      queryMock.mockResolvedValueOnce(stateFor("browser-surface-1"));

      render(<BrowserApp />);

      await waitFor(() => expect(createProfileMock).toHaveBeenCalledWith("Default"));
      expect(await screen.findByRole("radio", { name: /Default/ })).toBeInTheDocument();
    });

    // Supersedes Browser-B3 D24 (switching closed every tab): the previous
    // profile's tabs are parked, never destroyed, and come back on return.
    describe("profile switching keeps each profile's own tabs", () => {
      const WORK = TEST_PROFILE_ID;
      const PERSONAL = "profile-test-2";
      const OFFSCREEN = { x: -20_000, y: -20_000, width: 800, height: 600 };
      // Each surface's own page: Work's two tabs and Personal's two tabs all differ.
      const PAGES: Record<string, string> = {
        "browser-surface-1": "https://work-one.test/",
        "browser-surface-2": "https://work-two.test/",
        "browser-surface-3": "https://personal-one.test/",
        "browser-surface-4": "https://personal-two.test/",
      };

      function twoProfiles() {
        listProfilesMock.mockResolvedValue([
          testProfile({ displayName: "Work" }),
          testProfile({ profileId: PERSONAL, displayName: "Personal" }),
        ]);
        let next = 0;
        createMock.mockImplementation(async () => `browser-surface-${++next}`);
        queryMock.mockImplementation(async (id: string) => stateFor(id, { url: PAGES[id] }));
        destroyMock.mockResolvedValue(undefined);
        deleteProfileMock.mockResolvedValue(undefined);
      }

      function tabLabels() {
        return screen.queryAllByRole("tab").map((tab) => tab.textContent);
      }

      async function openSecondTab(expectedCreates: number) {
        fireEvent.click(screen.getByRole("button", { name: "New tab" }));
        await waitFor(() => expect(createMock).toHaveBeenCalledTimes(expectedCreates));
      }

      async function switchTo(name: string, expectedLabels: string[]) {
        fireEvent.click(screen.getByRole("button", { name }));
        await waitFor(() => expect(tabLabels()).toEqual(expectedLabels));
      }

      /** Work: two tabs; then Personal: two tabs. Returns once Personal is active. */
      async function setUpBothProfiles() {
        render(<BrowserApp />);
        await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
        await openSecondTab(2);
        await waitFor(() => expect(tabLabels()).toEqual(["work-one.test", "work-two.test"]));
        await switchTo("Personal", ["personal-one.test"]);
        await openSecondTab(4);
        await waitFor(() => expect(tabLabels()).toEqual(["personal-one.test", "personal-two.test"]));
      }

      it("the first switch to a profile opens exactly one new tab there and destroys nothing", async () => {
        twoProfiles();
        render(<BrowserApp />);
        await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
        await openSecondTab(2);

        await switchTo("Personal", ["personal-one.test"]);

        expect(createMock).toHaveBeenCalledTimes(3);
        expect(createMock).toHaveBeenNthCalledWith(1, WORK, BROWSER_NEW_TAB_URL);
        expect(createMock).toHaveBeenNthCalledWith(3, PERSONAL, BROWSER_NEW_TAB_URL);
        expect(destroyMock).not.toHaveBeenCalled();
      });

      it("switching back restores the profile's own tabs and active tab without recreating any surface", async () => {
        twoProfiles();
        await setUpBothProfiles();
        // Work's second tab was opened last, so it is the one left active there.
        await switchTo("Work", ["work-one.test", "work-two.test"]);

        expect(createMock).toHaveBeenCalledTimes(4);
        expect(destroyMock).not.toHaveBeenCalled();
        const selected = screen.getAllByRole("tab").map((tab) => tab.getAttribute("aria-selected"));
        expect(selected).toEqual(["false", "true"]);
        expect(screen.getByTestId("browser-address-input")).toHaveValue("https://work-two.test/");
      });

      it("repeated switch cycles never create, destroy, or mix tabs between profiles", async () => {
        twoProfiles();
        await setUpBothProfiles();

        for (let cycle = 0; cycle < 3; cycle++) {
          await switchTo("Work", ["work-one.test", "work-two.test"]);
          await switchTo("Personal", ["personal-one.test", "personal-two.test"]);
        }

        expect(createMock).toHaveBeenCalledTimes(4);
        expect(destroyMock).not.toHaveBeenCalled();
        // Each surface was created in exactly one profile.
        expect(createMock.mock.calls.map(([profile]) => profile)).toEqual([WORK, WORK, PERSONAL, PERSONAL]);
      });

      it("parks the other profile's surfaces off-screen and shows only the active profile's active tab", async () => {
        twoProfiles();
        await setUpBothProfiles();
        setBoundsMock.mockClear();

        await switchTo("Work", ["work-one.test", "work-two.test"]);

        await waitFor(() => {
          for (const parked of ["browser-surface-1", "browser-surface-3", "browser-surface-4"]) {
            expect(setBoundsMock).toHaveBeenCalledWith(parked, OFFSCREEN);
          }
        });
        expect(setBoundsMock).not.toHaveBeenCalledWith("browser-surface-2", OFFSCREEN);
        expect(setBoundsMock.mock.calls.some(([id]) => id === "browser-surface-2")).toBe(true);
      });

      it("navigation acts only on the active profile's active tab", async () => {
        twoProfiles();
        navigateMock.mockResolvedValue(undefined);
        await setUpBothProfiles();

        fireEvent.change(screen.getByTestId("browser-address-input"), { target: { value: "https://example.org" } });
        fireEvent.click(screen.getByTestId("browser-navigate-button"));

        await waitFor(() => expect(navigateMock).toHaveBeenCalledTimes(1));
        expect(navigateMock.mock.calls[0][0]).toBe("browser-surface-4");
      });

      it("a profile whose tabs were all closed gets one new tab when it becomes active again", async () => {
        twoProfiles();
        render(<BrowserApp />);
        await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
        fireEvent.click(screen.getByRole("button", { name: /close/i }));
        await waitFor(() => expect(tabLabels()).toEqual([]));

        // (Surfaces are numbered in creation order, so Personal's first tab is
        // surface 2 and Work's replacement tab is surface 3.)
        await switchTo("Personal", ["work-two.test"]);
        await switchTo("Work", ["personal-one.test"]);

        expect(createMock.mock.calls).toEqual([
          [WORK, BROWSER_NEW_TAB_URL],
          [PERSONAL, BROWSER_NEW_TAB_URL],
          [WORK, BROWSER_NEW_TAB_URL],
        ]);
      });

      it("declares the shown profile to the desktop on every switch, so only it is a Grant target", async () => {
        twoProfiles();
        await setUpBothProfiles();
        await switchTo("Work", ["work-one.test", "work-two.test"]);

        const declared = setActiveProfileMock.mock.calls.map(([profile]) => profile).filter((p) => p !== null);
        expect(declared).toEqual([WORK, PERSONAL, WORK]);
        expect(setActiveProfileMock).toHaveBeenLastCalledWith(WORK);
      });

      it("keeps browsing when the desktop refuses the declaration (it then fails closed on its side)", async () => {
        twoProfiles();
        setActiveProfileMock.mockRejectedValue({ kind: "profileIdentityUnavailable" });
        await setUpBothProfiles();

        expect(tabLabels()).toEqual(["personal-one.test", "personal-two.test"]);
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      });

      it("deleting a profile first closes its parked tabs, and leaves the active profile's tabs alone", async () => {
        twoProfiles();
        await setUpBothProfiles();

        fireEvent.click(screen.getByRole("button", { name: /Delete Work/ }));
        fireEvent.click(screen.getByRole("button", { name: "Delete" }));

        await waitFor(() => expect(deleteProfileMock).toHaveBeenCalledWith(WORK));
        expect(destroyMock.mock.calls.map(([id]) => id).sort()).toEqual(["browser-surface-1", "browser-surface-2"]);
        const lastDestroy = Math.max(...destroyMock.mock.invocationCallOrder);
        expect(deleteProfileMock.mock.invocationCallOrder[0]).toBeGreaterThan(lastDestroy);
        expect(tabLabels()).toEqual(["personal-one.test", "personal-two.test"]);
      });

      it("unmounting destroys every profile's surfaces, parked ones included", async () => {
        twoProfiles();
        render(<BrowserApp />);
        await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
        await openSecondTab(2);
        await switchTo("Personal", ["personal-one.test"]);

        cleanup();

        await waitFor(() =>
          expect(destroyMock.mock.calls.map(([id]) => id).sort()).toEqual([
            "browser-surface-1",
            "browser-surface-2",
            "browser-surface-3",
          ]),
        );
      });

      it("under StrictMode, a switch still opens exactly one tab and keeps the previous profile's tabs", async () => {
        twoProfiles();
        render(
          <StrictMode>
            <BrowserApp />
          </StrictMode>,
        );
        await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
        await switchTo("Personal", ["work-two.test"]);
        await switchTo("Work", ["work-one.test"]);

        expect(createMock).toHaveBeenCalledTimes(2);
        expect(destroyMock).not.toHaveBeenCalled();
      });
    });

    it("creating a new profile through the switcher calls createBrowserProfile and makes it active", async () => {
      listProfilesMock.mockResolvedValue([testProfile()]);
      createMock.mockResolvedValue("browser-surface-1");
      queryMock.mockResolvedValue(stateFor("browser-surface-1"));
      createProfileMock.mockResolvedValueOnce("profile-new");

      render(<BrowserApp />);
      await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));

      fireEvent.click(screen.getByTestId("new-profile-button"));
      fireEvent.change(screen.getByTestId("new-profile-name-input"), { target: { value: "Shopping" } });
      fireEvent.click(screen.getByRole("button", { name: "Create" }));

      await waitFor(() => expect(createProfileMock).toHaveBeenCalledWith("Shopping"));
    });

    it("deleting a profile calls deleteBrowserProfile after confirmation", async () => {
      listProfilesMock.mockResolvedValue([testProfile({ displayName: "Work" }), testProfile({
        profileId: "profile-test-2",
        displayName: "Personal",
      })]);
      createMock.mockResolvedValue("browser-surface-1");
      queryMock.mockResolvedValue(stateFor("browser-surface-1"));
      deleteProfileMock.mockResolvedValueOnce(undefined);

      render(<BrowserApp />);
      await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));

      // Only the INACTIVE profile ("Personal") exposes a delete control —
      // the active one is being used by the visible tab(s).
      fireEvent.click(screen.getByRole("button", { name: /Delete Personal/ }));
      fireEvent.click(screen.getByRole("button", { name: "Delete" }));

      await waitFor(() => expect(deleteProfileMock).toHaveBeenCalledWith("profile-test-2"));
    });

    it("shows a locked/corrupted badge for profiles reporting that availability", async () => {
      listProfilesMock.mockResolvedValue([
        testProfile({ displayName: "Work" }),
        testProfile({ profileId: "profile-test-2", displayName: "Locked One", availability: { kind: "locked" } }),
        testProfile({
          profileId: "profile-test-3",
          displayName: "Broken One",
          availability: { kind: "corrupted", reason: "profile.json is malformed" },
        }),
      ]);
      createMock.mockResolvedValue("browser-surface-1");
      queryMock.mockResolvedValue(stateFor("browser-surface-1"));

      render(<BrowserApp />);
      await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));

      expect(await screen.findByText("In use")).toBeInTheDocument();
      expect(await screen.findByText("Corrupted")).toBeInTheDocument();
    });
  });
});

// Regression (found in native live validation, B7): React StrictMode — on in
// every development build (`main.tsx`) — mounts, unmounts, and remounts each
// component once. `useBrowserTabs`'s unmount cleanup marked the hook
// unmounted and nothing marked it mounted again, so every tab surface was
// destroyed the moment it was created and `isBusy` never cleared: "New tab"
// stayed disabled and the address bar never became usable.
describe("BrowserApp under React StrictMode", () => {
  it("keeps the first tab, clears busy, and enables New tab and the address bar", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValue(stateFor("browser-surface-1"));

    render(
      <StrictMode>
        <BrowserApp />
      </StrictMode>,
    );

    expect(await screen.findAllByRole("tab")).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "New tab" })).toBeEnabled());
    expect(screen.getByRole("textbox", { name: "Address" })).toBeEnabled();
    expect(destroyMock).not.toHaveBeenCalled();
  });

  it("opens a second tab from New tab", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1").mockResolvedValueOnce("browser-surface-2");
    queryMock.mockImplementation(async (id: string) => stateFor(id));

    render(
      <StrictMode>
        <BrowserApp />
      </StrictMode>,
    );

    // The first tab must exist before "a second" one can be opened: a New
    // tab click racing the profile's own first-tab creation is, by design,
    // that profile's one new tab (see the profile-switching tests below).
    expect(await screen.findAllByRole("tab")).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "New tab" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "New tab" }));

    await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
    expect(createMock).toHaveBeenCalledTimes(2);
    expect(destroyMock).not.toHaveBeenCalled();
  });

  it("still destroys every surface when the Browser really unmounts", async () => {
    createMock.mockResolvedValueOnce("browser-surface-1");
    queryMock.mockResolvedValue(stateFor("browser-surface-1"));
    destroyMock.mockResolvedValue(undefined);

    const { unmount } = render(
      <StrictMode>
        <BrowserApp />
      </StrictMode>,
    );
    expect(await screen.findAllByRole("tab")).toHaveLength(1);
    unmount();

    expect(destroyMock).toHaveBeenCalledWith("browser-surface-1");
  });
});
