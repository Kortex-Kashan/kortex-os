import { motion } from "motion/react";
import { SidebarProvider, TooltipProvider } from "@kortex/design-system";

import { AppSidebar } from "./navigation/AppSidebar";
import { StatusBar } from "./StatusBar";
import { TopBar } from "./TopBar";
import { Workspace } from "./Workspace";

/**
 * The desktop shell: persistent chrome (top bar, navigation, status bar)
 * that future KORTEX applications mount inside of via Workspace's route
 * outlet. M2.1 scope only — no feature pages, no IPC, no Tauri (see the
 * M2.1 task brief for the full boundary list).
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
        </motion.div>
      </SidebarProvider>
    </TooltipProvider>
  );
}
