import { StrictMode, useState } from "react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  createMock,
  setBoundsMock,
  queryMock,
  destroyMock,
  listProfilesMock,
  createProfileMock,
  deleteProfileMock,
  onPolicyDeniedMock,
  onNavigatedMock,
  setActiveProfileMock,
} = vi.hoisted(() => ({
  createMock: vi.fn(),
  setBoundsMock: vi.fn(),
  queryMock: vi.fn(),
  destroyMock: vi.fn(),
  listProfilesMock: vi.fn(),
  createProfileMock: vi.fn(),
  deleteProfileMock: vi.fn(),
  onPolicyDeniedMock: vi.fn(),
  onNavigatedMock: vi.fn(),
  setActiveProfileMock: vi.fn(),
}));

// The signed-in shell's auth state, as `BrowserSessionProvider` reads it.
// `null` (the default) is "no AuthProvider" -- the standalone case.
let authContext: { state: { status: string; identity?: unknown } } | null = null;
vi.mock("@/auth/AuthProvider", () => ({ useOptionalAuth: () => authContext }));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    createBrowserSurface: createMock,
    setBrowserSurfaceBounds: setBoundsMock,
    queryBrowserSurfaceState: queryMock,
    destroyBrowserSurface: destroyMock,
    listBrowserProfiles: listProfilesMock,
    createBrowserProfile: createProfileMock,
    deleteBrowserProfile: deleteProfileMock,
    onBrowserPolicyDenied: onPolicyDeniedMock,
    onBrowserSurfaceNavigated: onNavigatedMock,
    setActiveBrowserProfile: setActiveProfileMock,
  };
});

import { BrowserSessionProvider } from "./BrowserSessionProvider";
import { BrowserApp } from "./components/BrowserApp";
import { useBrowserProfiles } from "./hooks/useBrowserProfiles";

const WORK = "profile-work";
const PERSONAL = "profile-personal";
const OFFSCREEN = { x: -20_000, y: -20_000, width: 800, height: 600 };
const PAGES: Record<string, string> = {
  "browser-surface-1": "https://work-one.test/",
  "browser-surface-2": "https://work-two.test/",
  "browser-surface-3": "https://personal-one.test/",
};

function profile(profileId: string, displayName: string) {
  return { profileId, displayName, createdAt: 0, lastOpenedAt: null, availability: { kind: "available" as const } };
}

beforeEach(() => {
  authContext = null;
  for (const mock of [
    createMock,
    setBoundsMock,
    queryMock,
    destroyMock,
    listProfilesMock,
    createProfileMock,
    deleteProfileMock,
    onPolicyDeniedMock,
    onNavigatedMock,
    setActiveProfileMock,
  ]) {
    mock.mockReset();
  }
  let next = 0;
  createMock.mockImplementation(async () => `browser-surface-${++next}`);
  queryMock.mockImplementation(async (id: string) => ({
    surfaceId: id,
    url: PAGES[id] ?? "https://www.google.com/",
    loading: false,
    canGoBack: false,
    canGoForward: false,
  }));
  setBoundsMock.mockResolvedValue(undefined);
  destroyMock.mockResolvedValue(undefined);
  onPolicyDeniedMock.mockResolvedValue(() => {});
  onNavigatedMock.mockResolvedValue(() => {});
  setActiveProfileMock.mockResolvedValue(undefined);
  listProfilesMock.mockResolvedValue([profile(WORK, "Work"), profile(PERSONAL, "Personal")]);
});

/** The signed-in shell in miniature: one session provider, and a workspace
 * showing either the Browser view or another KORTEX application. */
function Shell() {
  const [app, setApp] = useState<"browser" | "dashboard">("browser");
  return (
    <BrowserSessionProvider>
      <button onClick={() => setApp("dashboard")}>Go to Dashboard</button>
      <button onClick={() => setApp("browser")}>Go to Browser</button>
      {app === "browser" ? <BrowserApp /> : <p>Dashboard content</p>}
    </BrowserSessionProvider>
  );
}

function tabLabels() {
  return screen.queryAllByRole("tab").map((tab) => tab.textContent);
}

async function openSecondTab(expectedCreates: number) {
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  await waitFor(() => expect(createMock).toHaveBeenCalledTimes(expectedCreates));
}

async function leaveBrowser() {
  fireEvent.click(screen.getByText("Go to Dashboard"));
  await screen.findByText("Dashboard content");
}

async function returnToBrowser(expectedLabels: (string | null)[]) {
  fireEvent.click(screen.getByText("Go to Browser"));
  await waitFor(() => expect(tabLabels()).toEqual(expectedLabels));
}

describe("Browser tabs survive switching KORTEX applications", () => {
  it("leaving and returning to the Browser keeps the same tabs, surfaces and active tab, destroying nothing", async () => {
    render(<Shell />);
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
    await openSecondTab(2);
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test", "work-two.test"]));

    await leaveBrowser();
    expect(tabLabels()).toEqual([]);
    await returnToBrowser(["work-one.test", "work-two.test"]);

    expect(createMock).toHaveBeenCalledTimes(2);
    expect(destroyMock).not.toHaveBeenCalled();
    const selected = screen.getAllByRole("tab").map((tab) => tab.getAttribute("aria-selected"));
    expect(selected).toEqual(["false", "true"]);
    expect(screen.getByTestId("browser-address-input")).toHaveValue("https://work-two.test/");
  });

  it("repeated application switching never creates or destroys a surface", async () => {
    render(<Shell />);
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
    await openSecondTab(2);

    for (let round = 0; round < 5; round += 1) {
      await leaveBrowser();
      await returnToBrowser(["work-one.test", "work-two.test"]);
    }

    expect(createMock).toHaveBeenCalledTimes(2);
    expect(destroyMock).not.toHaveBeenCalled();
    expect(listProfilesMock).toHaveBeenCalledTimes(1);
  });

  it("while the Browser is hidden every surface is parked off-screen, so none can draw over another application", async () => {
    render(<Shell />);
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
    await openSecondTab(2);
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test", "work-two.test"]));

    setBoundsMock.mockClear();
    await leaveBrowser();

    await waitFor(() => {
      for (const id of ["browser-surface-1", "browser-surface-2"]) {
        expect(setBoundsMock).toHaveBeenCalledWith(id, OFFSCREEN);
      }
    });
    // Nothing was placed anywhere visible while the Browser was hidden.
    expect(setBoundsMock.mock.calls.every(([, bounds]) => bounds === OFFSCREEN || bounds.x === OFFSCREEN.x)).toBe(true);

    setBoundsMock.mockClear();
    await returnToBrowser(["work-one.test", "work-two.test"]);
    // Coming back places the ACTIVE tab into the content area again.
    await waitFor(() =>
      expect(
        setBoundsMock.mock.calls.some(([id, bounds]) => id === "browser-surface-2" && bounds.x !== OFFSCREEN.x),
      ).toBe(true),
    );
  });

  it("each profile's tabs and active tab survive application switching", async () => {
    render(<Shell />);
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
    await openSecondTab(2);
    fireEvent.click(screen.getByRole("button", { name: "Personal" }));
    await waitFor(() => expect(tabLabels()).toEqual(["personal-one.test"]));

    await leaveBrowser();
    await returnToBrowser(["personal-one.test"]);
    fireEvent.click(screen.getByRole("button", { name: "Work" }));
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test", "work-two.test"]));

    expect(createMock.mock.calls.map(([profileId]) => profileId)).toEqual([WORK, WORK, PERSONAL]);
    expect(destroyMock).not.toHaveBeenCalled();
  });

  it("keeps the active profile declared while the Browser is merely hidden (hidden is not a different tenant)", async () => {
    render(<Shell />);
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
    await waitFor(() => expect(setActiveProfileMock).toHaveBeenLastCalledWith(WORK));
    const declarations = setActiveProfileMock.mock.calls.length;

    await leaveBrowser();
    await returnToBrowser(["work-one.test"]);

    // Hiding and showing the Browser declared nothing new — in particular
    // it never cleared the declaration — so WORK is still what the desktop
    // checks Browser Grants against.
    expect(setActiveProfileMock.mock.calls.length).toBe(declarations);
    expect(setActiveProfileMock).toHaveBeenLastCalledWith(WORK);
  });

  it("starts nothing until the Browser is first opened", async () => {
    function ShellNeverOpeningBrowser() {
      return (
        <BrowserSessionProvider>
          <p>Dashboard content</p>
        </BrowserSessionProvider>
      );
    }
    render(<ShellNeverOpeningBrowser />);
    await screen.findByText("Dashboard content");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(listProfilesMock).not.toHaveBeenCalled();
    expect(createMock).not.toHaveBeenCalled();
  });

  it("the session ending (sign-out unmounts the shell) destroys every surface, the parked ones included", async () => {
    const { unmount } = render(<Shell />);
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
    await openSecondTab(2);
    fireEvent.click(screen.getByRole("button", { name: "Personal" }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(3));
    await leaveBrowser();

    unmount();

    await waitFor(() =>
      expect(destroyMock.mock.calls.map(([id]) => id).sort()).toEqual([
        "browser-surface-1",
        "browser-surface-2",
        "browser-surface-3",
      ]),
    );
  });

  it("works the same under StrictMode", async () => {
    render(
      <StrictMode>
        <Shell />
      </StrictMode>,
    );
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
    await leaveBrowser();
    await returnToBrowser(["work-one.test"]);

    expect(createMock).toHaveBeenCalledTimes(1);
    expect(destroyMock).not.toHaveBeenCalled();
  });
});

describe("the Browser profile list follows the signed-in identity", () => {
  it("asks for no profiles once the session has ended, while the shell is still on screen", async () => {
    authContext = {
      state: { status: "AUTHENTICATED", identity: { tenantId: "tenant-a", principalId: "alice" } },
    };
    const { rerender } = render(<Shell />);
    await waitFor(() => expect(tabLabels()).toEqual(["work-one.test"]));
    const loads = listProfilesMock.mock.calls.length;

    // Sign-out: the auth state ends before the shell finishes unmounting.
    authContext = { state: { status: "UNAUTHENTICATED" } };
    rerender(<Shell />);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(listProfilesMock.mock.calls.length).toBe(loads);
  });

  it("loads again when the identity changes, instead of only once", async () => {
    const { rerender } = renderHook(({ identityKey }) => useBrowserProfiles({ identityKey }), {
      initialProps: { identityKey: "tenant-a\u0000alice" as string | null },
    });
    await waitFor(() => expect(listProfilesMock).toHaveBeenCalledTimes(1));

    listProfilesMock.mockResolvedValue([profile("profile-b", "B")]);
    rerender({ identityKey: "tenant-b\u0000bob" });

    await waitFor(() => expect(listProfilesMock).toHaveBeenCalledTimes(2));
  });

  it("an unauthenticated desktop fails closed: no profile is active, and the cause is shown", async () => {
    listProfilesMock.mockRejectedValue({ kind: "profileIdentityUnavailable" });
    render(<Shell />);

    expect(await screen.findByRole("alert")).toHaveTextContent("No authenticated tenant is available yet");
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(createMock).not.toHaveBeenCalled();
  });

  it("a load that recovers after the identity becomes available shows the profiles", async () => {
    listProfilesMock.mockRejectedValueOnce({ kind: "profileIdentityUnavailable" });
    const { result, rerender } = renderHook(({ identityKey }) => useBrowserProfiles({ identityKey }), {
      initialProps: { identityKey: null as string | null },
    });
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.activeProfileId).toBeNull();

    await act(async () => rerender({ identityKey: "tenant-a\u0000alice" }));

    await waitFor(() => expect(result.current.activeProfileId).toBe(WORK));
    expect(result.current.error).toBeNull();
  });
});
