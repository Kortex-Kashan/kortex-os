import { useCallback, useEffect, useRef, useState } from "react";

import {
  createBrowserSurface,
  destroyBrowserSurface,
  goBackBrowserSurface,
  goForwardBrowserSurface,
  isBrowserRuntimeError,
  navigateBrowserSurface,
  queryBrowserSurfaceState,
  reloadBrowserSurface,
  setBrowserSurfaceBounds,
  type BrowserRuntimeError,
  type BrowserSurfaceId,
  type BrowserSurfaceState,
} from "../api";

/** Browser-B2 proof: every visible tab is a real `BrowserSurfaceId` created
 * via `create_surface` — there is no frontend-only "fake tab" state. Only
 * one tab's surface is ever positioned in the visible content area at a
 * time; every other open tab's surface is parked at `OFFSCREEN_BOUNDS`
 * (still alive — switching back to it does not reload or lose its state,
 * unlike destroying and recreating it would). Persisting tabs across app
 * restarts, or giving each tab its own profile, is explicitly Browser-B3's
 * job — every Browser-B2 tab shares one process-lifetime, non-persisted
 * profile id. */
const DEFAULT_PROFILE_ID = "default";
const DEFAULT_NEW_TAB_URL = "https://example.com";
const OFFSCREEN_BOUNDS = { x: -20_000, y: -20_000, width: 800, height: 600 };

export interface BrowserTab {
  id: BrowserSurfaceId;
  state: BrowserSurfaceState | null;
}

export function browserRuntimeErrorMessage(error: unknown): string {
  if (isBrowserRuntimeError(error)) {
    const runtimeError = error as BrowserRuntimeError;
    switch (runtimeError.kind) {
      case "surfaceNotFound":
        return `No browser surface with id ${runtimeError.surfaceId}.`;
      case "alreadyExists":
        return `A browser surface with id ${runtimeError.surfaceId} already exists.`;
      case "platform":
        return runtimeError.message;
    }
  }
  return error instanceof Error ? error.message : "An unexpected browser runtime error occurred.";
}

export function useBrowserTabs() {
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<BrowserSurfaceId | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const activeTabIdRef = useRef(activeTabId);
  activeTabIdRef.current = activeTabId;
  const hasOpenedInitialTabRef = useRef(false);
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
      setIsBusy(true);
      setError(null);
      try {
        const id = await createBrowserSurface(DEFAULT_PROFILE_ID, url);
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

  // Exactly one initial tab, opened once — guarded against React 18
  // StrictMode's dev-only double-invocation of mount effects.
  useEffect(() => {
    if (hasOpenedInitialTabRef.current) return;
    hasOpenedInitialTabRef.current = true;
    void openTab();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
