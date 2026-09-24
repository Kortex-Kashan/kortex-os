import { useEffect, useRef, useState } from "react";
import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@kortex/design-system";

import {
  createBrowserSurface,
  destroyBrowserSurface,
  isBrowserRuntimeError,
  navigateBrowserSurface,
  queryBrowserSurfaceState,
  reloadBrowserSurface,
  type BrowserRuntimeError,
  type BrowserSurfaceId,
} from "../api";

// Browser-B1 proof-of-concept surface: no tabs, no address-bar chrome, no
// history — that is Browser-B2's job (`docs/architecture/browser_roadmap_
// b0_b10.md`). This exists to prove the create → navigate → reload → query
// → destroy lifecycle through `IKortexBrowserRuntime`/`WebView2RuntimeAdapter`
// works end to end from real UI, and that no surface outlives this
// component (the unmount cleanup below).
const DEFAULT_PROFILE_ID = "default";
const DESCRIPTION =
  "Browser runtime foundation (Browser-B1) — a single proof-of-concept surface. Tabs, an address bar, history, and AI actions are not implemented yet.";

function errorMessage(error: unknown): string {
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

export function BrowserApp() {
  const [surfaceId, setSurfaceId] = useState<BrowserSurfaceId | null>(null);
  const [urlInput, setUrlInput] = useState("https://example.com");
  const [currentUrl, setCurrentUrl] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Read inside the unmount effect below without making the effect
  // re-subscribe on every create/destroy — this component's own lifecycle
  // (mount/unmount), not the surface's, is what that effect tracks.
  const surfaceIdRef = useRef<BrowserSurfaceId | null>(null);
  surfaceIdRef.current = surfaceId;

  useEffect(() => {
    return () => {
      // Browser-B1's explicit "no orphaned browser runtime" requirement,
      // from the frontend lifecycle side: navigating away from this
      // application must not leave a live WebView2 surface behind. The
      // Rust-side app-shutdown handlers (`lib.rs`'s `CloseRequested`/
      // `ExitRequested`) are the second, independent backstop for the
      // whole-app-quitting case this effect cannot observe.
      const id = surfaceIdRef.current;
      if (id) {
        void destroyBrowserSurface(id);
      }
    };
  }, []);

  async function handleOpen() {
    setIsBusy(true);
    setError(null);
    try {
      const id = await createBrowserSurface(DEFAULT_PROFILE_ID, urlInput);
      setSurfaceId(id);
      const state = await queryBrowserSurfaceState(id);
      setCurrentUrl(state.url);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleNavigate() {
    if (!surfaceId) return;
    setIsBusy(true);
    setError(null);
    try {
      await navigateBrowserSurface(surfaceId, urlInput);
      const state = await queryBrowserSurfaceState(surfaceId);
      setCurrentUrl(state.url);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleReload() {
    if (!surfaceId) return;
    setIsBusy(true);
    setError(null);
    try {
      await reloadBrowserSurface(surfaceId);
      const state = await queryBrowserSurfaceState(surfaceId);
      setCurrentUrl(state.url);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleClose() {
    if (!surfaceId) return;
    setIsBusy(true);
    setError(null);
    try {
      await destroyBrowserSurface(surfaceId);
      setSurfaceId(null);
      setCurrentUrl(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Browser</CardTitle>
          <CardDescription>{DESCRIPTION}</CardDescription>
        </div>
        {surfaceId && <Badge variant="secondary">Surface active</Badge>}
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder="https://example.com"
            aria-label="URL"
            className="flex-1 bg-background text-body text-foreground border border-input rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary"
            data-testid="browser-url-input"
          />
          {!surfaceId ? (
            <Button size="sm" onClick={() => void handleOpen()} disabled={isBusy}>
              Open
            </Button>
          ) : (
            <Button size="sm" variant="outline" onClick={() => void handleNavigate()} disabled={isBusy}>
              Navigate
            </Button>
          )}
        </div>

        {surfaceId && (
          <div className="space-y-2" data-testid="browser-surface-state">
            <p className="text-caption text-muted-foreground">
              Surface <span className="font-mono">{surfaceId}</span> — currently at{" "}
              <span className="font-mono">{currentUrl}</span>
            </p>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => void handleReload()} disabled={isBusy}>
                Reload
              </Button>
              <Button size="sm" variant="destructive" onClick={() => void handleClose()} disabled={isBusy}>
                Close
              </Button>
            </div>
          </div>
        )}

        {error && (
          <p className="text-body text-destructive" role="alert">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
