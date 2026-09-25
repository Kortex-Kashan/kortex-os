// Browser-B1/B2: thin wrapper over the `browser_*` Tauri commands
// (`apps/desktop/src-tauri/src/browser_runtime.rs`). Mirrors `ipc/session.ts`'s
// own convention of a small, dedicated module per Rust command group, using
// `@tauri-apps/api/core`'s `invoke()` directly — these are plain native
// Tauri IPC calls, deliberately NOT routed through `ipc/client.ts`'s
// `invokeCapability()`, since there is no backend capability/governance
// layer for Browser yet (that is Browser-B5's job; see
// docs/architecture/browser_b1_preflight_report.md §7).
//
// Unlike `ipc/session.ts`'s commands, these can genuinely fail in ways the
// caller must react to (an unknown surface id, an invalid URL) — so, unlike
// that module, a failed call here rejects the returned promise with the
// `BrowserRuntimeError` shape below, rather than resolving to an envelope.

import { invoke } from "@tauri-apps/api/core";

/** Opaque handle to a live browser surface — never a WebView2 handle,
 * never anything the frontend can use to reach the surface except through
 * these commands. */
export type BrowserSurfaceId = string;

/** A surface's on-screen placement in the KORTEX main window's own logical
 * (DPI-independent) coordinate space — matches the CSS pixel values
 * `getBoundingClientRect()` already reports at 100% scale. Browser-B2 uses
 * this both to track the content area's real size (window/workspace
 * resize) and to park an inactive tab's surface off-screen without
 * destroying it. */
export interface SurfaceBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface BrowserSurfaceState {
  surfaceId: BrowserSurfaceId;
  url: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
}

/** Mirrors `browser_runtime::BrowserRuntimeError`'s `#[serde(tag = "kind")]`
 * shape exactly. */
export type BrowserRuntimeError =
  | { kind: "surfaceNotFound"; surfaceId: BrowserSurfaceId }
  | { kind: "alreadyExists"; surfaceId: BrowserSurfaceId }
  | { kind: "platform"; message: string };

export function isBrowserRuntimeError(value: unknown): value is BrowserRuntimeError {
  return (
    typeof value === "object" &&
    value !== null &&
    "kind" in value &&
    ["surfaceNotFound", "alreadyExists", "platform"].includes((value as { kind: unknown }).kind as string)
  );
}

/** `profileId` is opaque — Rust resolves it to a sanitized directory under
 * its own profile root; this module must never construct or accept a
 * filesystem path itself (see `browser_runtime.rs`'s own doc comment). */
export async function createBrowserSurface(profileId: string, initialUrl: string): Promise<BrowserSurfaceId> {
  return invoke<BrowserSurfaceId>("browser_create_surface", {
    request: { profileId, initialUrl },
  });
}

export async function navigateBrowserSurface(surfaceId: BrowserSurfaceId, url: string): Promise<void> {
  await invoke("browser_navigate", { surfaceId, url });
}

export async function reloadBrowserSurface(surfaceId: BrowserSurfaceId): Promise<void> {
  await invoke("browser_reload", { surfaceId });
}

/** Browser-B2: session-history back/forward, backed by real WebView2
 * `ICoreWebView2::GoBack`/`GoForward` (Windows only — see
 * `browser_runtime.rs`'s platform-boundary doc; rejects on any other
 * platform, since no non-Windows adapter implements this yet). */
export async function goBackBrowserSurface(surfaceId: BrowserSurfaceId): Promise<void> {
  await invoke("browser_go_back", { surfaceId });
}

export async function goForwardBrowserSurface(surfaceId: BrowserSurfaceId): Promise<void> {
  await invoke("browser_go_forward", { surfaceId });
}

/** Browser-B2: reposition/resize a surface — used both for window/workspace
 * resize tracking and to park an inactive tab's surface off-screen. */
export async function setBrowserSurfaceBounds(surfaceId: BrowserSurfaceId, bounds: SurfaceBounds): Promise<void> {
  await invoke("browser_set_bounds", { surfaceId, bounds });
}

export async function queryBrowserSurfaceState(surfaceId: BrowserSurfaceId): Promise<BrowserSurfaceState> {
  return invoke<BrowserSurfaceState>("browser_query_state", { surfaceId });
}

export async function destroyBrowserSurface(surfaceId: BrowserSurfaceId): Promise<void> {
  await invoke("browser_destroy", { surfaceId });
}
