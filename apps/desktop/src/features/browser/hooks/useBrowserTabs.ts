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
  policyDenyReasonMessage,
  queryBrowserSurfaceState,
  reloadBrowserSurface,
  setBrowserSurfaceBounds,
  type BrowserProfileId,
  type BrowserSurfaceId,
  type BrowserSurfaceState,
} from "../api";

/** Browser-B2 proof: every visible tab is a real `BrowserSurfaceId` created
 * via `create_surface` — there is no frontend-only "fake tab" state. Only
 * one tab's surface is ever positioned in the visible content area at a
 * time; every other open tab's surface is parked at `OFFSCREEN_BOUNDS`
 * (still alive — switching back to it does not reload or lose its state,
 * unlike destroying and recreating it would).
 *
 * Browser-B3: every tab now belongs to a real, persistent
 * `BrowserProfileId` supplied by the caller (`useBrowserProfiles`'s active
 * profile), rather than the flat, process-lifetime `"default"` string
 * every Browser-B2 tab shared. Per the approved V1 UX (decision D24),
 * switching the active profile closes every existing tab and opens
 * exactly one fresh tab against the newly active profile — WebView2 gives
 * no way to re-point an already-created surface at a different profile
 * directory, so a surface's profile is immutable for its lifetime; "switch
 * profile" can only ever mean "new tabs from here on use the new profile." */
const DEFAULT_NEW_TAB_URL = "https://example.com";
const OFFSCREEN_BOUNDS = { x: -20_000, y: -20_000, width: 800, height: 600 };

export interface BrowserTab {
  id: BrowserSurfaceId;
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

export function useBrowserTabs(profileId: BrowserProfileId | null) {
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<BrowserSurfaceId | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const activeTabIdRef = useRef(activeTabId);
  activeTabIdRef.current = activeTabId;
  const profileIdRef = useRef(profileId);
  profileIdRef.current = profileId;
  // Browser-B3: distinguishes "the very first profile this hook has ever
  // seen" (open exactly one initial tab, mirroring Browser-B2's own
  // behavior) from "the active profile changed" (D24: close every
  // existing tab, then open exactly one fresh tab against the new
  // profile) — also survives React 18 StrictMode's dev-only double-
  // invocation of mount effects, the same role
  // `hasOpenedInitialTabRef` played before Browser-B3.
  const previousProfileIdRef = useRef<BrowserProfileId | null>(null);
  // Adversarial-review defect 1: `openTab`'s `createBrowserSurface` call can
  // resolve *after* the Browser view has already unmounted (e.g. the user
  // navigates away immediately). Without this flag, the just-created
  // surface would never enter `tabs`/`tabsRef.current` and so would never
  // be reached by anything — including the unmount-cleanup effect below,
  // which only destroys what's already tracked — leaking a live surface
  // with no code path left to destroy it. Set `false` synchronously in the
  // same cleanup that destroys every already-tracked surface, so by the
  // time any in-flight `openTab` promise's continuation runs, this is
  // already correct.
  const isMountedRef = useRef(true);

  const applyActiveBounds = useCallback(() => {
    const element = containerRef.current;
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
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    applyActiveBounds();
    const observer = new ResizeObserver(applyActiveBounds);
    observer.observe(element);
    window.addEventListener("resize", applyActiveBounds);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", applyActiveBounds);
    };
  }, [applyActiveBounds]);

  // Browser-B4: surfaces a policy-denied navigation (scheme not allowed,
  // local/private-network destination, or a malformed URI), popup,
  // download, or native permission request as the same error banner every
  // other operation's failure already uses — a denied action must never be
  // silent. The event payload deliberately never carries the actual
  // URI/host (see `PolicyDeniedEvent`'s own doc comment), so this message
  // is necessarily coarse ("this destination is not allowed"), never a
  // specific address.
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
      // Adversarial-review defect 3: this used to swallow every failure
      // identically. Reusing the *existing* `BrowserRuntimeError` taxonomy
      // (not inventing a new one) lets the one genuinely expected case —
      // the surface was already destroyed (e.g. the tab was closed while
      // this query was in flight) — stay silent, while any other failure
      // (a real platform/runtime bug) surfaces the same way every other
      // operation's failure does, instead of leaving this tab's state
      // silently, permanently stale with no signal to the user or a future
      // maintainer.
      if (isBrowserRuntimeError(err) && err.kind === "surfaceNotFound") {
        return;
      }
      setError(browserRuntimeErrorMessage(err));
    }
  }, []);

  const openTab = useCallback(
    async (url: string = DEFAULT_NEW_TAB_URL) => {
      const currentProfileId = profileIdRef.current;
      if (currentProfileId === null) {
        // Fail closed (mirrors the backend's own OD-B7 posture): never
        // fall back to a shared/default profile id.
        setError("No browser profile is active yet.");
        return;
      }
      setIsBusy(true);
      setError(null);
      try {
        const id = await createBrowserSurface(currentProfileId, url);
        if (!isMountedRef.current) {
          // Adversarial-review defect 1: the Browser view unmounted while
          // this surface was being created. It was never tracked, so
          // nothing else will ever destroy it — destroy it here,
          // immediately, rather than leak it. Never add it to `tabs`: there
          // is no owner left to render it.
          void destroyBrowserSurface(id);
          return;
        }
        setTabs((current) => [...current, { id, state: null }]);
        await setBrowserSurfaceBounds(id, OFFSCREEN_BOUNDS);
        setActiveTabId(id);
        await refreshTabState(id);
      } catch (err) {
        if (isMountedRef.current) {
          setError(browserRuntimeErrorMessage(err));
        }
      } finally {
        if (isMountedRef.current) {
          setIsBusy(false);
        }
      }
    },
    [refreshTabState],
  );

  // Whenever the active tab changes: move it into the real content-area
  // rect, and park every other open tab off-screen — the mechanism that
  // makes "switching tabs" real without destroying/recreating a surface.
  useEffect(() => {
    if (!activeTabId) return;
    applyActiveBounds();
    for (const tab of tabsRef.current) {
      if (tab.id !== activeTabId) {
        void setBrowserSurfaceBounds(tab.id, OFFSCREEN_BOUNDS);
      }
    }
  }, [activeTabId, applyActiveBounds]);

  const switchTab = useCallback((id: BrowserSurfaceId) => {
    setActiveTabId(id);
  }, []);

  const closeTab = useCallback(async (id: BrowserSurfaceId) => {
    setIsBusy(true);
    setError(null);
    try {
      await destroyBrowserSurface(id);
      // Adversarial-review defect 2: tab/surface tracking is only ever
      // dropped on the SUCCESS path now. If `destroyBrowserSurface` throws,
      // the native surface may still be alive — removing it from tracking
      // here regardless (the old `finally`-based behavior) would make it
      // permanently unreachable: not retryable, not reconciled, and not
      // reached by this hook's own unmount cleanup, which only destroys
      // what's still in `tabsRef.current`. Leaving the tab in place keeps
      // its `BrowserSurfaceId` identifiable and lets the user retry Close.
      setTabs((current) => {
        const remaining = current.filter((tab) => tab.id !== id);
        setActiveTabId((currentActive) => {
          if (currentActive !== id) return currentActive;
          return remaining.length > 0 ? remaining[remaining.length - 1].id : null;
        });
        return remaining;
      });
    } catch (err) {
      setError(browserRuntimeErrorMessage(err));
    } finally {
      setIsBusy(false);
    }
  }, []);

  // Browser-B3: the very first real profile this hook ever sees gets
  // exactly one initial tab (mirrors Browser-B2's own behavior). Every
  // SUBSEQUENT profile change is a genuine profile switch (decision D24):
  // close every existing tab, then open exactly one fresh tab against the
  // newly active profile. `previousProfileIdRef`'s guard against a
  // no-op re-render (identical `profileId`) also survives React 18
  // StrictMode's dev-only double-invocation of mount effects, the same
  // role `hasOpenedInitialTabRef` played through Browser-B2.
  useEffect(() => {
    if (profileId === null) return;
    if (previousProfileIdRef.current === profileId) return;
    const isSwitch = previousProfileIdRef.current !== null;
    previousProfileIdRef.current = profileId;

    if (!isSwitch) {
      void openTab();
      return;
    }

    const tabsToClose = tabsRef.current.map((tab) => tab.id);
    void (async () => {
      for (const id of tabsToClose) {
        await closeTab(id);
      }
      await openTab();
    })();
  }, [profileId, openTab, closeTab]);

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

  // Browser-B1's "no orphaned browser runtime" requirement, extended to
  // every Browser-B2 tab: destroy every surface this component ever created
  // when the Browser application itself unmounts. The Rust-side app-
  // shutdown handlers (`lib.rs`'s `CloseRequested`/`ExitRequested`) remain
  // the second, independent backstop for the whole-app-quitting case this
  // effect cannot observe.
  //
  // `isMountedRef.current = false` is set FIRST, synchronously, before
  // destroying whatever is already tracked (adversarial-review defect 1).
  // Cleanup functions run synchronously during React's commit phase, which
  // always completes before any pending promise's `.then` continuation gets
  // a turn on the microtask queue — so by the time an in-flight `openTab`
  // call's `createBrowserSurface` resolves after this point, its own
  // `isMountedRef.current` check is guaranteed to already observe `false`.
  useEffect(() => {
    return () => {
      isMountedRef.current = false;
      for (const tab of tabsRef.current) {
        void destroyBrowserSurface(tab.id);
      }
    };
  }, []);

  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? null;

  return {
    tabs,
    activeTabId,
    activeTab,
    containerRef,
    isBusy,
    error,
    openTab,
    closeTab,
    switchTab,
    navigate,
    reload,
    goBack,
    goForward,
  };
}
