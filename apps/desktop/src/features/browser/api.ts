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
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

/** Opaque handle to a live browser surface — never a WebView2 handle,
 * never anything the frontend can use to reach the surface except through
 * these commands. */
export type BrowserSurfaceId = string;

/** Browser-B3: opaque, persisted profile identifier. The frontend never
 * constructs one itself (only `createBrowserProfile`'s response ever
 * mints one) and never supplies a tenant id anywhere — the backend
 * resolves the current tenant itself (OD-B7); see `browser_profile_store.rs`. */
export type BrowserProfileId = string;

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

const BROWSER_RUNTIME_ERROR_KINDS = ["surfaceNotFound", "alreadyExists", "platform"];

export function isBrowserRuntimeError(value: unknown): value is BrowserRuntimeError {
  return (
    typeof value === "object" &&
    value !== null &&
    "kind" in value &&
    BROWSER_RUNTIME_ERROR_KINDS.includes((value as { kind: unknown }).kind as string)
  );
}

/** Browser-B3: mirrors `browser_profile_store::BrowserProfileError`'s
 * `#[serde(tag = "kind")]` shape exactly — every field is renamed to
 * camelCase explicitly on the Rust side (serde's `rename_all` on an enum
 * does NOT cascade into a struct-variant's own fields, confirmed while
 * building that type; every field below was verified against the real
 * serialized shape, not assumed). */
export type BrowserProfileError =
  | { kind: "profileIdentityUnavailable" }
  | { kind: "profileStorageUnavailable"; message: string }
  | { kind: "profileNotFound"; profileId: BrowserProfileId }
  | { kind: "profilePathViolation"; profileId: BrowserProfileId }
  | { kind: "profileLocked"; profileId: BrowserProfileId }
  | { kind: "profileCorrupted"; profileId: BrowserProfileId; reason: string }
  | { kind: "alreadyExists"; profileId: BrowserProfileId }
  | { kind: "platform"; message: string };

const BROWSER_PROFILE_ERROR_KINDS = [
  "profileIdentityUnavailable",
  "profileStorageUnavailable",
  "profileNotFound",
  "profilePathViolation",
  "profileLocked",
  "profileCorrupted",
  "alreadyExists",
  "platform",
];

export function isBrowserProfileError(value: unknown): value is BrowserProfileError {
  return (
    typeof value === "object" &&
    value !== null &&
    "kind" in value &&
    BROWSER_PROFILE_ERROR_KINDS.includes((value as { kind: unknown }).kind as string)
  );
}

/** Browser-B3: `browser_create_surface` can fail resolving the profile
 * (before any surface exists) OR creating the surface itself — Rust's
 * `#[serde(untagged)]` `BrowserSurfaceCreationError` means the JSON on the
 * wire is exactly whichever inner error's own shape, so this is a plain
 * union rather than a second wrapper shape. `"alreadyExists"` and
 * `"platform"` appear in both halves with different sibling fields
 * (`surfaceId` vs `profileId`); narrowing on `kind` alone still works,
 * checking for the `profileId`/`surfaceId` field distinguishes the rest. */
export type BrowserSurfaceCreationError = BrowserRuntimeError | BrowserProfileError;

/** Browser-B3: every non-deleted profile for the current tenant, merged
 * with its current (never-persisted) availability — mirrors
 * `browser_profile_store::ProfileSummary`. */
export interface ProfileSummary {
  profileId: BrowserProfileId;
  displayName: string;
  createdAt: number;
  lastOpenedAt: number | null;
  availability: ProfileAvailability;
}

export type ProfileAvailability =
  | { kind: "available" }
  | { kind: "locked" }
  | { kind: "corrupted"; reason: string };

export async function listBrowserProfiles(): Promise<ProfileSummary[]> {
  return invoke<ProfileSummary[]>("browser_list_profiles");
}

export async function createBrowserProfile(displayName: string): Promise<BrowserProfileId> {
  return invoke<BrowserProfileId>("browser_create_profile", { displayName });
}

export async function renameBrowserProfile(profileId: BrowserProfileId, displayName: string): Promise<void> {
  await invoke("browser_rename_profile", { profileId, displayName });
}

/** Refused (as `ProfileLocked`) while any tab is still open against this
 * profile — the caller must close every tab using it first. */
export async function deleteBrowserProfile(profileId: BrowserProfileId): Promise<void> {
  await invoke("browser_delete_profile", { profileId });
}

/** `profileId` is opaque — Rust resolves it, via `BrowserProfileStore`,
 * to a containment-checked, tenant-scoped directory; this module must
 * never construct or accept a filesystem path itself (see
 * `browser_runtime.rs`'s own doc comment). */
export async function createBrowserSurface(profileId: BrowserProfileId, initialUrl: string): Promise<BrowserSurfaceId> {
  return invoke<BrowserSurfaceId>("browser_create_surface", { profileId, initialUrl });
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

/** Browser-B4: coarse network classification for a denied destination —
 * mirrors `browser_policy::NetworkClassification` exactly. Deliberately
 * never the raw host/IP — see `PolicyDeniedEvent`'s own doc comment. */
export type NetworkClassification =
  | "loopback"
  | "privateIpv4"
  | "linkLocalIpv4"
  | "thisNetworkIpv4"
  | "carrierGradeNat"
  | "uniqueLocalIpv6"
  | "linkLocalIpv6"
  | "localHostname";

export type PolicyDenyReason =
  | { kind: "malformed" }
  | { kind: "schemeNotAllowed"; scheme: string }
  | { kind: "privateNetworkAccess"; classification: NetworkClassification }
  | { kind: "notYetSupported" };

export type PolicyAction = "navigation" | "popup" | "download" | "permission";

/** Browser-B4: emitted (to the trusted "main" webview only — never the
 * browser surface itself) whenever navigation policy blocks an action.
 * Deliberately minimal: never the full URI, never a path/query/fragment —
 * see `browser_policy::PolicyDeniedEvent`'s own doc comment on why. */
export interface PolicyDeniedEvent {
  surfaceId: BrowserSurfaceId;
  action: PolicyAction;
  reason: PolicyDenyReason;
}

const POLICY_DENIED_EVENT_NAME = "browser://policy-denied";

export function onBrowserPolicyDenied(handler: (event: PolicyDeniedEvent) => void): Promise<UnlistenFn> {
  return listen<PolicyDeniedEvent>(POLICY_DENIED_EVENT_NAME, (event) => handler(event.payload));
}

/** Human-readable summary for `PolicyDeniedEvent.reason` — used by the
 * frontend's error banner. Never surfaces a URI/host (there isn't one in
 * the payload to surface — see the event's own doc comment). `action` is
 * only consulted for `"notYetSupported"`, whose message would otherwise be
 * too vague to be useful ("this action is not yet supported" says nothing
 * a user could act on) — every other reason is self-describing regardless
 * of which action triggered it. */
export function policyDenyReasonMessage(reason: PolicyDenyReason, action: PolicyAction): string {
  switch (reason.kind) {
    case "malformed":
      return "the destination could not be understood";
    case "schemeNotAllowed":
      return `the "${reason.scheme}:" scheme is not allowed`;
    case "privateNetworkAccess":
      return "local/private network destinations are not allowed";
    case "notYetSupported":
      switch (action) {
        case "popup":
          return "popups are not yet supported";
        case "download":
          return "downloads are not yet supported";
        case "permission":
          return "this site's permission request is not yet supported";
        case "navigation":
          return "this action is not yet supported";
      }
  }
}
