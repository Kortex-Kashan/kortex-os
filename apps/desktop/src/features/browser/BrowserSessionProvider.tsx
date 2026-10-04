import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

import { useOptionalAuth } from "@/auth/AuthProvider";

import { useBrowserProfiles } from "./hooks/useBrowserProfiles";
import { useBrowserTabs } from "./hooks/useBrowserTabs";

export interface BrowserSession {
  profiles: ReturnType<typeof useBrowserProfiles>;
  tabs: ReturnType<typeof useBrowserTabs>;
  /** Called by the Browser view when it mounts: nothing (no profile list,
   * no WebView2 surface) is started until the Browser is first opened. */
  activate: () => void;
}

const BrowserSessionContext = createContext<BrowserSession | null>(null);

/** The Browser session of the signed-in shell, or `null` outside one. */
export function useBrowserSession(): BrowserSession | null {
  return useContext(BrowserSessionContext);
}

/**
 * Owns the Browser's profiles and tabs for as long as the signed-in shell
 * exists — mounted above the workspace outlet, so switching KORTEX
 * applications unmounts only the Browser *view* (`BrowserApp`), never its
 * tabs or their live WebView2 surfaces. While no view is mounted every
 * surface is parked off-screen (`useBrowserTabs`); the view coming back
 * restores them exactly as they were.
 *
 * It holds the existing `useBrowserProfiles` + `useBrowserTabs` state —
 * moved up, not duplicated: there is one tab registry, and the desktop's
 * `BrowserRuntime` stays the authority over every surface. Sign-out
 * unmounts the shell and this provider with it, which destroys every
 * surface; the desktop also tears them down itself at the session boundary.
 */
export function BrowserSessionProvider({ children }: { children: ReactNode }) {
  const [activated, setActivated] = useState(false);
  const auth = useOptionalAuth();
  // Once the session has ended (sign-out: the shell is still mounted for its
  // exit transition), nothing asks the desktop for profiles any more — the
  // desktop would refuse anyway, since it has already cleared the tenant.
  const authenticated = auth === null || auth.state.status === "AUTHENTICATED";
  const identity = auth?.state.status === "AUTHENTICATED" ? auth.state.identity : null;
  const identityKey = identity ? `${identity.tenantId}\u0000${identity.principalId}` : null;

  const profiles = useBrowserProfiles({ enabled: activated && authenticated, identityKey });
  const tabs = useBrowserTabs(profiles.activeProfileId);
  const activate = useCallback(() => setActivated(true), []);

  return (
    <BrowserSessionContext.Provider value={{ profiles, tabs, activate }}>{children}</BrowserSessionContext.Provider>
  );
}
