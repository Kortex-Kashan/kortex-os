/**
 * A request to navigate to a workspace application, raised by any
 * navigation source — a sidebar click today, a command-palette entry or
 * deep link later — before it has been resolved against the registry's
 * known applications.
 */
export interface NavigationIntent {
  applicationId: string;
}

/**
 * The resolved, observable navigation state kept in sync between the
 * browser URL and the workspace runtime. `applicationId` is null when
 * the current route matches no known application (the workspace's own
 * empty state, or an unrecognized path).
 */
export interface ApplicationNavigationState {
  applicationId: string | null;
  route: string;
}
