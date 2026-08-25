import * as React from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useWorkspace } from "@/workspace/WorkspaceProvider";

import { resolveApplicationIdForRoute, resolveRouteForApplicationId } from "./applicationRouter";
import type { ApplicationNavigationState, NavigationIntent } from "./navigationTypes";

export interface ApplicationNavigation {
  state: ApplicationNavigationState;
  navigateToApplication: (intent: NavigationIntent) => void;
}

/**
 * The single bridge between the Sidebar, React Router, and
 * WorkspaceProvider. Callers (AppSidebar) never read `useNavigate()`,
 * `useLocation()`, or `useWorkspace()` directly — they call
 * `navigateToApplication()` and read `state` from here instead.
 */
export function useApplicationNavigation(): ApplicationNavigation {
  const navigate = useNavigate();
  const location = useLocation();
  const { applications, activeApplicationId } = useWorkspace();

  const navigateToApplication = React.useCallback(
    (intent: NavigationIntent) => {
      const route = resolveRouteForApplicationId(applications, intent.applicationId);
      if (!route) {
        throw new Error(`Cannot navigate to unknown workspace application "${intent.applicationId}".`);
      }
      navigate(route);
    },
    [applications, navigate],
  );

  const state = React.useMemo<ApplicationNavigationState>(
    () => ({ applicationId: activeApplicationId, route: location.pathname }),
    [activeApplicationId, location.pathname],
  );

  return { state, navigateToApplication };
}

/**
 * Mounted once at the route level (routes/index.tsx), alongside
 * DesktopShell inside WorkspaceProvider rather than inside the shell
 * itself — the same pattern M2.2 used to add WorkspaceProvider without
 * touching DesktopShell. Keeps the workspace's active application
 * following the browser URL: a sidebar click, a browser back/forward
 * event, and a refreshed/deep-linked URL all change `location.pathname`
 * first, and this effect resolves that path to a known application (or
 * clears it, for an unknown/root path) afterward. This is a one-way
 * sync — URL drives workspace state, never the reverse — so there is
 * exactly one source of truth for "what's active" instead of two stores
 * that could disagree.
 */
export function WorkspaceNavigationSync(): null {
  const location = useLocation();
  const { applications, activeApplicationId, setActiveApplication } = useWorkspace();

  React.useEffect(() => {
    const applicationId = resolveApplicationIdForRoute(applications, location.pathname);
    if (applicationId !== activeApplicationId) {
      setActiveApplication(applicationId);
    }
  }, [applications, location.pathname, activeApplicationId, setActiveApplication]);

  return null;
}
