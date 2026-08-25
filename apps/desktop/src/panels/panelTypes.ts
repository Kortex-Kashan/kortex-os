import type { ComponentType, SVGProps } from "react";

/**
 * Where a panel renders inside PanelLayout's four composition areas.
 * "Contextual tools" (per the M2.4 task brief) are panels registered at
 * "right" — the contextual-tools rail, alongside ordinary side panels
 * such as an inspector. "main" is not a panel position: the active
 * workspace application already owns the main area (WorkspaceView), and
 * PanelLayout renders it as the `children` it wraps, not as a registered
 * panel.
 */
export type PanelPosition = "left" | "right" | "bottom";

/**
 * A panel's sizing hint. `default` seeds PanelProvider's size state the
 * first time a panel is registered (or when no persisted size exists
 * yet); `min`/`max` are declared for future resize-interaction
 * enforcement and are not yet read by PanelLayout (M2.4 scope is the
 * size *state and persistence* foundation, not a drag-to-resize
 * interaction).
 */
export interface PanelSize {
  default: number;
  min?: number;
  max?: number;
}

/** Fallback width/height (px) for a panel that declares no `defaultSize`. */
export const DEFAULT_PANEL_SIZE_PX = 280;

/**
 * A panel a future KORTEX application can register with the OS-owned
 * workspace layout. `permissions` follows the same `kortex.<domain>.
 * <resource>.<action>` convention already declared on WorkspaceApplication
 * (workspaceTypes.ts) — nothing in M2.4 enforces them yet, since the
 * Security Engine isn't reachable until the IPC bridge (M3) exists.
 */
export interface PanelDefinition {
  id: string;
  title: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  position: PanelPosition;
  component: ComponentType;
  defaultOpen: boolean;
  permissions: string[];
  defaultSize?: PanelSize;
}

/** A panel's observable runtime state, as tracked by PanelProvider. */
export interface PanelState {
  id: string;
  open: boolean;
  size: number;
}
