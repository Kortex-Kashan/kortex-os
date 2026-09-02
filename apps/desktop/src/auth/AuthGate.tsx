import * as React from "react";
import { AnimatePresence, motion, MotionConfig } from "motion/react";
import { Spinner } from "@kortex/design-system";
import { motionTokens } from "@kortex/design-system/tokens";

import { useAuth } from "./AuthProvider";
import { BackendUnavailableScreen } from "./BackendUnavailableScreen";
import { BootstrapScreen } from "./BootstrapScreen";
import { LoginScreen } from "./LoginScreen";
import type { AuthState } from "./authTypes";

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
 * M7.1: the bounded backend-startup readiness window (`backendReadiness.ts`).
 * Distinct from `CheckingScreen` — this one can legitimately be visible for
 * several real seconds while the auto-spawned backend finishes booting, so
 * it says so, with live attempt progress rather than a bare spinner.
 */
function StartingScreen({ attempt, maxAttempts }: { attempt: number; maxAttempts: number }) {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-3 bg-background">
      <Spinner size={24} aria-label="Starting the KORTEX backend" />
      <span className="text-body text-muted-foreground">
        Starting KORTEX backend… ({attempt}/{maxAttempts})
      </span>
    </div>
  );
}

/** Maps a non-`CHECKING`, non-`AUTHENTICATED` state to a stable
 * `AnimatePresence` key and the screen to render for it. */
function screenFor(state: Exclude<AuthState, { status: "CHECKING" } | { status: "AUTHENTICATED" }>): {
  key: string;
  node: React.ReactNode;
} {
  if (state.status === "STARTING") {
    return { key: "starting", node: <StartingScreen attempt={state.attempt} maxAttempts={state.maxAttempts} /> };
  }
  if (state.status === "BACKEND_UNAVAILABLE") {
    return { key: "backend-unavailable", node: <BackendUnavailableScreen /> };
  }
  if (state.status === "BOOTSTRAP_REQUIRED" || state.status === "BOOTSTRAPPING" || state.status === "BOOTSTRAP_ERROR") {
    return { key: "bootstrap", node: <BootstrapScreen /> };
  }
  return { key: "login", node: <LoginScreen /> };
}

/**
 * Gates the application shell behind local-runtime startup and
 * authentication. Mounted inside `SessionProvider` (theme restoration) but
 * outside `WorkspaceProvider`/`PanelProvider`/`DesktopShell`
 * (`routes/index.tsx`) — the authenticated shell is `children` here, and it
 * is never rendered while `state.status` is anything other than
 * `"AUTHENTICATED"`. `CHECKING` renders neither the shell nor any other
 * screen, avoiding the CHECKING -> (any screen) -> SHELL flicker the M4.1
 * brief calls out by name — the same principle now also covers
 * CHECKING -> STARTING -> BOOTSTRAP/LOGIN -> SHELL (M7.1).
 *
 * The only animation here is the crossfade between whichever non-shell
 * screen is current and the shell itself (Phase 9) — `MotionConfig
 * reducedMotion="user"` scopes `prefers-reduced-motion` handling to just
 * this boundary, without changing how `motion` is used elsewhere
 * (`DesktopShell`/`WorkspaceView`/panels already animate their own mounts
 * independently; wrapping them in a second fade here would double-animate
 * the shell's entrance).
 */
export function AuthGate({ children }: AuthGateProps) {
  const { state } = useAuth();

  if (state.status === "CHECKING") {
    return <CheckingScreen />;
  }

  const { key, node } = state.status === "AUTHENTICATED" ? { key: "shell", node: null } : screenFor(state);

  return (
    <MotionConfig reducedMotion="user">
      <AnimatePresence mode="wait" initial={false}>
        {state.status === "AUTHENTICATED" ? (
          <React.Fragment key="shell">{children}</React.Fragment>
        ) : (
          <motion.div
            key={key}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: motionTokens.duration.fast }}
            className="h-screen"
          >
            {node}
          </motion.div>
        )}
      </AnimatePresence>
    </MotionConfig>
  );
}
