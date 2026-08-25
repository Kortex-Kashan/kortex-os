import { Outlet } from "react-router-dom";

/**
 * The mounting point for future KORTEX applications (dashboards, AI Studio,
 * workflow/connector UIs, etc.) — those are out of M2.1 scope; this route
 * outlet is what they'll render into once their own milestones land.
 */
export function Workspace() {
  return (
    <main className="flex-1 overflow-auto bg-background p-6">
      <Outlet />
    </main>
  );
}

export function WorkspaceEmptyState() {
  return (
    <div className="flex h-full min-h-[60vh] flex-col items-center justify-center gap-2 text-center">
      <p className="text-heading">No application mounted</p>
      <p className="max-w-sm text-body text-muted-foreground">
        This workspace is the mounting point for future KORTEX applications.
        Select a section from the sidebar once modules ship in later
        milestones.
      </p>
    </div>
  );
}
