import type { WorkspaceApplication } from "@/workspace/workspaceTypes";

/**
 * Route ↔ application-id resolution for the workspace runtime. This
 * module holds no state of its own and does not duplicate
 * WorkspaceRegistry — every function is a pure lookup over the
 * `WorkspaceApplication[]` list callers already have (from
 * `useWorkspace()` or `DEFAULT_APPLICATIONS`), so there is nothing here
 * that could drift out of sync with the registry.
 */

/** The workspace's own empty state — no application active. */
export const WORKSPACE_ROOT_ROUTE = "/";

export function resolveRouteForApplicationId(
  applications: WorkspaceApplication[],
  applicationId: string,
): string | null {
  return applications.find((application) => application.id === applicationId)?.route ?? null;
}

export function resolveApplicationIdForRoute(
  applications: WorkspaceApplication[],
  pathname: string,
): string | null {
  return applications.find((application) => application.route === pathname)?.id ?? null;
}

export function isKnownApplicationRoute(
  applications: WorkspaceApplication[],
  pathname: string,
): boolean {
  return (
    pathname === WORKSPACE_ROOT_ROUTE || resolveApplicationIdForRoute(applications, pathname) !== null
  );
}

/**
 * Strips the leading slash from a `WorkspaceApplication.route` (e.g.
 * "/dashboard" → "dashboard") so it can be registered as a relative
 * child of the "/" shell layout route in `routes/index.tsx`.
 */
export function toRouterChildPath(route: string): string {
  return route.replace(/^\/+/, "");
}
