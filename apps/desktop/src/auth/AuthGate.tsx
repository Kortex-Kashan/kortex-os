import * as React from "react";
import { AnimatePresence, motion, MotionConfig } from "motion/react";
import { Spinner } from "@kortex/design-system";
import { motionTokens } from "@kortex/design-system/tokens";

import { useAuth } from "./AuthProvider";
import { LoginScreen } from "./LoginScreen";

export interface AuthGateProps {
  children: React.ReactNode;
}

function CheckingScreen() {
  // Deliberately unanimated (Phase 9 of the M4.1 brief names only "login
  // entrance/exit" and "auth-state transition" as animation-worthy) — this
  // state is typically near-instant (no stored token skips the network
  // round trip entirely, see AuthProvider) and animating a screen meant to
  // barely be seen risks the opposite of its purpose.
  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <Spinner size={24} aria-label="Checking for an existing session" />
    </div>
  );
}

/**
 * Gates the application shell behind authentication. Mounted inside
 * `SessionProvider` (theme restoration) but outside `WorkspaceProvider`/
 * `PanelProvider`/`DesktopShell` (`routes/index.tsx`) — the authenticated
 * shell is `children` here, and it is never rendered while `state.status`
 * is anything other than `"AUTHENTICATED"`. `CHECKING` renders neither the
 * shell nor the login screen, avoiding the CHECKING -> LOGIN -> SHELL
 * flicker the M4.1 brief calls out by name.
 *
 * The only animation here is the crossfade between the login screen and
 * the shell (Phase 9) — `MotionConfig reducedMotion="user"` scopes
 * `prefers-reduced-motion` handling to just this boundary, without
 * changing how `motion` is used elsewhere (`DesktopShell`/`WorkspaceView`/
 * panels already animate their own mounts independently; wrapping them in
 * a second fade here would double-animate the shell's entrance).
 */
export function AuthGate({ children }: AuthGateProps) {
  const { state } = useAuth();

  if (state.status === "CHECKING") {
    return <CheckingScreen />;
  }

  return (
    <MotionConfig reducedMotion="user">
      <AnimatePresence mode="wait" initial={false}>
        {state.status === "AUTHENTICATED" ? (
          <React.Fragment key="shell">{children}</React.Fragment>
        ) : (
          <motion.div
            key="login"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: motionTokens.duration.fast }}
            className="h-screen"
          >
            <LoginScreen />
          </motion.div>
        )}
      </AnimatePresence>
    </MotionConfig>
  );
}
