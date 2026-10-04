import { useCallback, useEffect, useRef, useState } from "react";

import {
  createBrowserSurface,
  destroyBrowserSurface,
  goBackBrowserSurface,
  goForwardBrowserSurface,
  isBrowserProfileError,
  isBrowserRuntimeError,
  navigateBrowserSurface,
  onBrowserPolicyDenied,
  onBrowserSurfaceNavigated,
  policyDenyReasonMessage,
  queryBrowserSurfaceState,
  reloadBrowserSurface,
  setActiveBrowserProfile,
  setBrowserSurfaceBounds,
  type BrowserProfileId,
  type BrowserSurfaceId,
  type BrowserSurfaceState,
} from "../api";
import { BROWSER_NEW_TAB_URL } from "../newTab";

/** Browser-B2 proof: every visible tab is a real `BrowserSurfaceId` created
 * via `create_surface` — there is no frontend-only "fake tab" state. Only
 * one tab's surface is ever positioned in the visible content area at a
 * time; every other open tab's surface is parked at `OFFSCREEN_BOUNDS`
 * (still alive — switching back to it does not reload or lose its state,
 * unlike destroying and recreating it would).
 *
 * Browser-B3: every tab belongs to a real, persistent `BrowserProfileId`
 * supplied by the caller (`useBrowserProfiles`'s active profile). WebView2
 * gives no way to re-point an already-created surface at a different
 * profile directory, so a surface's profile is immutable for its lifetime —
 * which is why each tab records the profile it was created in. */
const OFFSCREEN_BOUNDS = { x: -20_000, y: -20_000, width: 800, height: 600 };

export interface BrowserTab {
  id: BrowserSurfaceId;
  profileId: BrowserProfileId;
  state: BrowserSurfaceState | null;
}

export function browserRuntimeErrorMessage(error: unknown): string {
  if (isBrowserRuntimeError(error)) {
    switch (error.kind) {
      case "surfaceNotFound":
        return `No browser surface with id ${error.surfaceId}.`;
      case "alreadyExists":
        return `A browser surface with id ${error.surfaceId} already exists.`;
      case "platform":
        return error.message;
    }
  }
  if (isBrowserProfileError(error)) {
    switch (error.kind) {
      case "profileIdentityUnavailable":
        return "No authenticated tenant is available yet — sign in before using the browser.";
      case "profileStorageUnavailable":
        return error.message;
      case "profileNotFound":
        return `No browser profile with id ${error.profileId}.`;
      case "profilePathViolation":
        return `Browser profile ${error.profileId} could not be resolved safely.`;
      case "profileLocked":
        return "This profile is already open elsewhere.";
      case "profileCorrupted":
        return `Browser profile ${error.profileId} is corrupted: ${error.reason}`;
      case "alreadyExists":
        return `A browser profile with id ${error.profileId} already exists.`;
      case "platform":
        return error.message;
    }
  }
  return error instanceof Error ? error.message : "An unexpected browser runtime error occurred.";
}

/**
 * Browser tabs, grouped by the profile each tab's surface belongs to.
 *
 * Profile switching (supersedes Browser-B3 D24): the previously active
 * profile's tabs are PARKED off-screen, never destroyed — its surfaces, and
 * with them each tab's page, history, and scroll state, stay alive in that
 * profile's own WebView2 environment. Returning to the profile shows the
 * same tabs again. A profile with no tabs yet gets exactly one new tab at
 * `BROWSER_NEW_TAB_URL`. Isolation is unchanged: a surface is created in, and
 * only ever shows, its own profile's data directory (B3).
 */
export function useBrowserTabs(profileId: BrowserProfileId | null) {
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  // The active tab remembered PER PROFILE, so returning to a profile
  // re-activates the tab the user left there.
  const [activeByProfile, setActiveByProfile] = useState<Record<BrowserProfileId, BrowserSurfaceId | null>>({});
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The Browser view's content-area element — the rect the active tab is
  // placed over. It is also this hook's visibility signal: the hook may
  // outlive its view (it lives in the shell-level `BrowserSessionProvider`,
  // so tabs survive switching KORTEX applications), and while no view is
  // mounted there is nowhere to show a tab, so every surface is parked
  // off-screen instead of drawing over whatever application is showing.
  const [containerElement, setContainerElement] = useState<HTMLDivElement | null>(null);
  const containerRef = setContainerElement;
  const containerElementRef = useRef(containerElement);
  containerElementRef.current = containerElement;
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const activeTabId = profileId === null ? null : (activeByProfile[profileId] ?? null);
  const activeTabIdRef = useRef(activeTabId);
  activeTabIdRef.current = activeTabId;
  const profileIdRef = useRef(profileId);
  profileIdRef.current = profileId;
  // Dedupes a no-op re-run of the profile effect below (identical
  // `profileId`) — including React StrictMode's dev-only double invocation
  // of mount effects — so one profile change opens at most one new tab.
  const previousProfileIdRef = useRef<BrowserProfileId | null>(null);
  // Tab creations still in flight, per profile: a quick P1 -> P2 -> P1 while
  // P1's first tab is still being created must not open a second one.
  const pendingOpensRef = useRef<Map<BrowserProfileId, number>>(new Map());
  // Adversarial-review defect 1: `openTab`'s `createBrowserSurface` call can
  // resolve *after* the Browser view has already unmounted. Set `false` in the
  // unmount cleanup below and `true` again on (re)mount.
  const isMountedRef = useRef(true);

  const setActiveFor = useCallback((profile: BrowserProfileId, id: BrowserSurfaceId | null) => {
    setActiveByProfile((current) => ({ ...current, [profile]: id }));
  }, []);

  const applyActiveBounds = useCallback(() => {
    const element = containerElementRef.current;
    const activeId = activeTabIdRef.current;
    if (!element || !activeId) return;
    const rect = element.getBoundingClientRect();
    void setBrowserSurfaceBounds(activeId, {
      x: rect.left,
      y: rect.top,
      width: rect.width,
      height: rect.height,
    });
  }, []);

  // Tracks the content area's real on-screen rect (initial layout, sidebar
  // toggles, window resize, maximize) via ResizeObserver — event-driven, not
  // a polling loop. The `window resize` listener is a backstop for the case
  // where the content area's own size is unchanged but its position shifted.
  // With no content area (the view is not mounted — another KORTEX
  // application is showing), every surface, the active one included, is
  // parked: the native surfaces stay alive but never draw over it.
  useEffect(() => {
    const element = containerElement;
    if (!element) {
      for (const tab of tabsRef.current) {
        void setBrowserSurfaceBounds(tab.id, OFFSCREEN_BOUNDS);
      }
      return;
    }
    applyActiveBounds();
    const observer = new ResizeObserver(applyActiveBounds);
    observer.observe(element);
    window.addEventListener("resize", applyActiveBounds);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", applyActiveBounds);
    };
  }, [containerElement, applyActiveBounds]);

  // Browser-B4: surfaces a policy-denied navigation, popup, download, or
  // native permission request as the same error banner every other
  // operation's failure already uses — a denied action must never be silent.
  useEffect(() => {
    const unlistenPromise = onBrowserPolicyDenied((event) => {
      setError(`Blocked: ${policyDenyReasonMessage(event.reason, event.action)}.`);
    });
    return () => {
      void unlistenPromise.then((unlisten) => unlisten());
    };
  }, []);

  const refreshTabState = useCallback(async (id: BrowserSurfaceId) => {
    try {
      const state = await queryBrowserSurfaceState(id);
      setTabs((current) => current.map((tab) => (tab.id === id ? { ...tab, state } : tab)));
    } catch (err) {
      // Adversarial-review defect 3: only "already destroyed" stays silent.
      if (isBrowserRuntimeError(err) && err.kind === "surfaceNotFound") {
        return;
      }
      setError(browserRuntimeErrorMessage(err));
    }
  }, []);

  // A tab's own page changed (typed address, link click, back/forward):
  // re-read that tab's state. Without this, the label and address bar kept
  // the page the tab was on when the command returned — before the load
  // completed — so a navigated tab looked like it was still on its old page.
  useEffect(() => {
    const unlistenPromise = onBrowserSurfaceNavigated((surfaceId) => {
      if (tabsRef.current.some((tab) => tab.id === surfaceId)) {
        void refreshTabState(surfaceId);
      }
    });
    return () => {
      void unlistenPromise.then((unlisten) => unlisten());
    };
  }, [refreshTabState]);

  const openTab = useCallback(
    async (url: string = BROWSER_NEW_TAB_URL) => {
      const currentProfileId = profileIdRef.current;
      if (currentProfileId === null) {
        // Fail closed (mirrors the backend's own OD-B7 posture): never
        // fall back to a shared/default profile id.
        setError("No browser profile is active yet.");
        return;
      }
      setIsBusy(true);
      setError(null);
      const pending = pendingOpensRef.current;
      pending.set(currentProfileId, (pending.get(currentProfileId) ?? 0) + 1);
      try {
        const id = await createBrowserSurface(currentProfileId, url);
        if (!isMountedRef.current) {
          // Adversarial-review defect 1: never tracked, so destroy it now
          // rather than leak it.
          void destroyBrowserSurface(id);
          return;
        }
        setTabs((current) => [...current, { id, profileId: currentProfileId, state: null }]);
        await setBrowserSurfaceBounds(id, OFFSCREEN_BOUNDS);
        setActiveFor(currentProfileId, id);
        await refreshTabState(id);
      } catch (err) {
        if (isMountedRef.current) {
          setError(browserRuntimeErrorMessage(err));
        }
      } finally {
        pending.set(currentProfileId, (pending.get(currentProfileId) ?? 1) - 1);
        if (isMountedRef.current) {
          setIsBusy(false);
        }
      }
    },
    [refreshTabState, setActiveFor],
  );

  // Whenever the active tab (or the active profile) changes: move the active
  // tab into the real content-area rect, and park EVERY other open tab —
  // every other profile's included — off-screen. This is what makes both
  // "switching tabs" and "switching profiles" real without destroying or
  // recreating a surface.
  useEffect(() => {
    applyActiveBounds();
    for (const tab of tabsRef.current) {
      if (tab.id !== activeTabId) {
        void setBrowserSurfaceBounds(tab.id, OFFSCREEN_BOUNDS);
      }
    }
  }, [activeTabId, profileId, applyActiveBounds]);

  const switchTab = useCallback(
    (id: BrowserSurfaceId) => {
      const tab = tabsRef.current.find((candidate) => candidate.id === id);
      if (!tab || tab.profileId !== profileIdRef.current) return;
      setActiveFor(tab.profileId, id);
    },
    [setActiveFor],
  );

  const closeTab = useCallback(async (id: BrowserSurfaceId) => {
    setIsBusy(true);
    setError(null);
    try {
      await destroyBrowserSurface(id);
      // Adversarial-review defect 2: tracking is only dropped on SUCCESS, so
      // a surface that failed to close stays identifiable and retryable.
      const closing = tabsRef.current.find((tab) => tab.id === id);
      setTabs((current) => {
        const remaining = current.filter((tab) => tab.id !== id);
        if (closing) {
          setActiveByProfile((active) => {
            if (active[closing.profileId] !== id) return active;
            const sameProfile = remaining.filter((tab) => tab.profileId === closing.profileId);
            return {
              ...active,
              [closing.profileId]: sameProfile.length > 0 ? sameProfile[sameProfile.length - 1].id : null,
            };
          });
        }
        return remaining;
      });
    } catch (err) {
      setError(browserRuntimeErrorMessage(err));
    } finally {
      setIsBusy(false);
    }
  }, []);

  /** Destroys every tab of one profile — used before that profile is
   * deleted, since a profile with live (possibly parked) surfaces is in use. */
  const closeProfileTabs = useCallback(
    async (profile: BrowserProfileId) => {
      for (const tab of tabsRef.current.filter((candidate) => candidate.profileId === profile)) {
        await closeTab(tab.id);
      }
    },
    [closeTab],
  );

  // The desktop's Grant boundary only lets a Browser Grant act on the
  // profile shown here; parked tabs of other profiles are never eligible.
  // A failed declaration leaves the desktop failing closed (nothing
  // eligible) — browsing itself is unaffected.
  useEffect(() => {
    void setActiveBrowserProfile(profileId).catch(() => undefined);
  }, [profileId]);

  // A profile change never destroys or recreates a tab: the active-tab
  // effect above parks the previous profile's tabs and shows the new
  // profile's own tabs again. Only a profile that has no tab yet (and none
  // being created) gets one — a genuinely new tab at `BROWSER_NEW_TAB_URL`.
  useEffect(() => {
    if (profileId === null) return;
    if (previousProfileIdRef.current === profileId) return;
    previousProfileIdRef.current = profileId;
    const hasTab = tabsRef.current.some((tab) => tab.profileId === profileId);
    if (!hasTab && (pendingOpensRef.current.get(profileId) ?? 0) === 0) {
      void openTab();
    }
  }, [profileId, openTab]);

  const withActiveTab = useCallback(
    async (action: (id: BrowserSurfaceId) => Promise<void>) => {
      const id = activeTabIdRef.current;
      if (!id) return;
      setIsBusy(true);
      setError(null);
      try {
        await action(id);
        await refreshTabState(id);
      } catch (err) {
        setError(browserRuntimeErrorMessage(err));
      } finally {
        setIsBusy(false);
      }
    },
    [refreshTabState],
  );

  const navigate = useCallback((url: string) => withActiveTab((id) => navigateBrowserSurface(id, url)), [withActiveTab]);
  const reload = useCallback(() => withActiveTab((id) => reloadBrowserSurface(id)), [withActiveTab]);
  const goBack = useCallback(() => withActiveTab((id) => goBackBrowserSurface(id)), [withActiveTab]);
  const goForward = useCallback(() => withActiveTab((id) => goForwardBrowserSurface(id)), [withActiveTab]);

  // Browser-B1's "no orphaned browser runtime" requirement: destroy every
  // surface this hook ever created — every profile's — when its owner
  // unmounts. In the app that owner is the shell-level
  // `BrowserSessionProvider`, which unmounts with the signed-in shell
  // (sign-out), NOT when the user merely switches to another KORTEX
  // application; the desktop independently tears the same surfaces down at
  // the session boundary (`lib.rs::teardown_browser_session`). `isMountedRef` is set `true` on
  // (re)mount: React StrictMode (every development build) mounts, unmounts,
  // and remounts each component once, and without this the simulated
  // unmount left the hook believing it was unmounted forever (every tab
  // surface destroyed on creation, `isBusy` never cleared).
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      for (const tab of tabsRef.current) {
        void destroyBrowserSurface(tab.id);
      }
    };
  }, []);

  // Only the active profile's tabs are ever shown.
  const profileTabs = profileId === null ? [] : tabs.filter((tab) => tab.profileId === profileId);
  const activeTab = profileTabs.find((tab) => tab.id === activeTabId) ?? null;

  return {
    tabs: profileTabs,
    activeTabId,
    activeTab,
    containerRef,
    isBusy,
    error,
    openTab,
    closeTab,
    closeProfileTabs,
    switchTab,
    navigate,
    reload,
    goBack,
    goForward,
  };
}
