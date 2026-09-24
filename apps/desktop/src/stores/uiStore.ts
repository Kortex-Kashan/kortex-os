import { create } from "zustand";

export type Theme = "light" | "dark";
export type CopilotMode = "floating" | "docked";
export type CopilotTab = "chat" | "agents" | "tools" | "mcp";

interface UiState {
  theme: Theme;
  toggleTheme: () => void;
  setTheme: (theme: Theme) => void;
  copilotOpen: boolean;
  setCopilotOpen: (open: boolean) => void;
  toggleCopilot: () => void;
  copilotMode: CopilotMode;
  setCopilotMode: (mode: CopilotMode) => void;
  copilotTab: CopilotTab;
  setCopilotTab: (tab: CopilotTab) => void;
}

export const useUiStore = create<UiState>((set) => ({
  theme: "dark",
  toggleTheme: () =>
    set((state) => ({ theme: state.theme === "light" ? "dark" : "light" })),
  setTheme: (theme) => set({ theme }),
  copilotOpen: false,
  setCopilotOpen: (copilotOpen) => {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(copilotOpen ? "kortex:open-copilot" : "kortex:close-copilot"));
    }
    set({ copilotOpen });
  },
  toggleCopilot: () =>
    set((state) => {
      const next = !state.copilotOpen;
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("kortex:toggle-copilot"));
      }
      return { copilotOpen: next };
    }),
  copilotMode: "floating",
  setCopilotMode: (copilotMode) => set({ copilotMode }),
  copilotTab: "chat",
  setCopilotTab: (copilotTab) => set({ copilotTab }),
}));


