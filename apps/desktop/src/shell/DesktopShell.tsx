import { motion } from "motion/react";
import { SidebarProvider, TooltipProvider } from "@kortex/design-system";
import { MiniChatHost } from "@/features/mini-chat/MiniChatHost";

import { AppSidebar } from "./navigation/AppSidebar";
import { StatusBar } from "./StatusBar";
import { TopBar } from "./TopBar";
import { Workspace } from "./Workspace";

/**
 * The desktop shell: persistent chrome (top bar, navigation, status bar)
 * that future KORTEX applications mount inside of via Workspace's route
 * outlet. M2.1 scope only — no feature pages, no IPC, no Tauri (see the
 * M2.1 task brief for the full boundary list).
 *
 * `MiniChatHost` (Mini Chat) is a sibling of `Workspace`, not a descendant
 * of it — deliberately outside the route outlet, so switching applications
 * never unmounts it. It shares this component's own lifetime instead:
 * `DesktopShell` itself only ever mounts while `AuthGate` considers the
 * session `"AUTHENTICATED"` (see `routes/index.tsx`), so Mini Chat is
 * destroyed and recreated exactly when the rest of the authenticated shell
 * is — never independently.
 */
export function DesktopShell() {
  return (
    <TooltipProvider>
      <SidebarProvider>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.2 }}
          className="flex h-screen flex-col bg-background text-foreground"
        >
          <TopBar />
          <div className="flex flex-1 overflow-hidden">
            <AppSidebar />
            <Workspace />
          </div>
          <StatusBar />
          <MiniChatHost />
        </motion.div>
      </SidebarProvider>
    </TooltipProvider>
  );
}
