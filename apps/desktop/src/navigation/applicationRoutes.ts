import { createElement } from "react";
import type { RouteObject } from "react-router-dom";

import { WorkspaceView } from "@/workspace/WorkspaceView";
import type { WorkspaceApplication } from "@/workspace/workspaceTypes";

import { toRouterChildPath } from "./applicationRouter";

/**
 * One relative child route per workspace application, each rendering the
 * same WorkspaceView mounting point (M2.2) — WorkspaceNavigationSync (see
 * navigationBridge.tsx) is what activates the matching application once
 * one of these routes becomes current. No route carries a per-application
 * element or feature page; that is out of M2.3 scope (placeholder
 * applications only, per defaultApps.ts).
 */
export function buildApplicationRoutes(applications: WorkspaceApplication[]): RouteObject[] {
  return applications.map((application) => ({
    path: toRouterChildPath(application.route),
    element: createElement(WorkspaceView),
  }));
}
