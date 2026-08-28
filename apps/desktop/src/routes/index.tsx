import { createHashRouter, type RouteObject } from "react-router-dom";
import { AuthGate } from "@/auth/AuthGate";
import { AuthProvider } from "@/auth/AuthProvider";
import { DesktopShell } from "@/shell/DesktopShell";
import { buildApplicationRoutes } from "@/navigation/applicationRoutes";
import { WorkspaceNavigationSync } from "@/navigation/navigationBridge";
import { DEFAULT_PANELS } from "@/panels/defaultPanels";
import { PanelProvider } from "@/panels/PanelProvider";
import { SessionProvider, SessionSync } from "@/session/SessionProvider";
import { DEFAULT_APPLICATIONS } from "@/workspace/defaultApps";
import { WorkspaceProvider } from "@/workspace/WorkspaceProvider";
import { WorkspaceView } from "@/workspace/WorkspaceView";
import { NotFound } from "./NotFound";

// ADR-0002 §10.7: the design system gallery is a dev-build-only route
// (no Storybook). `import.meta.env.DEV` is statically replaced by Vite,
// so this branch — and the dynamic import inside it — is dead-code
// eliminated from production builds rather than merely hidden at runtime.
const devRoutes: RouteObject[] = import.meta.env.DEV
  ? [
      {
        path: "/dev/components",
        lazy: async () => {
          const { ComponentGallery } = await import("./dev/ComponentGallery");
          return { Component: ComponentGallery };
        },
      },
    ]
  : [];

// Hash routing per ADR-0002 §7.2 — the desktop shell has no address bar,
// no SEO requirement, and no deep-linking-from-the-web requirement.
//
// "/" is the DesktopShell layout route (M2.1) — TopBar/Sidebar/StatusBar
// chrome plus a Workspace route outlet. WorkspaceProvider (M2.2) wraps the
// shell here at the route level, not inside DesktopShell itself, so the
// shell component stays untouched while still gaining access to the
// workspace runtime. WorkspaceNavigationSync (M2.3, navigation/
// navigationBridge.tsx) is mounted the same way — a sibling of
// DesktopShell, still inside WorkspaceProvider — to keep the active
// application in sync with the URL without touching shell internals.
// PanelProvider (M2.4, panels/PanelProvider.tsx) wraps DesktopShell the
// same way again, one level in, so registered panels stay independent of
// which application route is current — WorkspaceView (rendered through
// DesktopShell's Workspace route outlet) reads it via PanelLayout.
//
// SessionProvider (M2.5, session/SessionProvider.tsx) wraps everything,
// outermost, so it can restore theme/application state before the
// providers below it render — including before authentication resolves
// (M4.1 §13: the login screen must render in the correct theme, so theme
// restoration is never gated behind sign-in). AuthProvider + AuthGate
// (M4.1, auth/AuthProvider.tsx + auth/AuthGate.tsx) sit one level in, just
// inside SessionProvider: AuthGate renders the login screen or a checking
// state in place of everything below it, so WorkspaceProvider/
// PanelProvider/DesktopShell — the authenticated shell — never mount until
// authentication actually succeeds. SessionSync — a second export from
// SessionProvider's own module — is mounted one level further in, inside
// AuthGate's children alongside WorkspaceNavigationSync, since restoring
// the active application and mirroring live workspace state both need
// context (useWorkspace) that doesn't exist at SessionProvider's own
// position in the tree, and both are themselves part of the authenticated
// shell. Panel layout is intentionally excluded from the session document
// — PanelProvider persists/restores it independently via its own
// "kortex.panels.v1" key (ADR-0003 removed an earlier session-level mirror
// of the same data).
//
// The index child is WorkspaceView, the runtime's own empty-state
// mounting point; one sibling child per default application (M2.3,
// navigation/applicationRoutes.ts) gives each a real, bookmarkable/
// refreshable URL (e.g. "#/dashboard") that resolves to the same
// WorkspaceView — WorkspaceNavigationSync is what activates the matching
// application once that route is current. These child routes are only
// ever reachable through DesktopShell's own `<Outlet/>`, which — per the
// above — does not exist in the render tree at all until AuthGate resolves
// to AUTHENTICATED, so a deep link to e.g. "#/dashboard" while signed out
// renders the login screen, never the workspace route underneath it.
export const router = createHashRouter([
  {
    path: "/",
    element: (
      <SessionProvider>
        <AuthProvider>
          <AuthGate>
            <WorkspaceProvider initialApplications={DEFAULT_APPLICATIONS}>
              <PanelProvider initialPanels={DEFAULT_PANELS}>
                <WorkspaceNavigationSync />
                <SessionSync />
                <DesktopShell />
              </PanelProvider>
            </WorkspaceProvider>
          </AuthGate>
        </AuthProvider>
      </SessionProvider>
    ),
    children: [
      { index: true, element: <WorkspaceView /> },
      ...buildApplicationRoutes(DEFAULT_APPLICATIONS),
    ],
  },
  ...devRoutes,
  {
    path: "*",
    element: <NotFound />,
  },
]);
