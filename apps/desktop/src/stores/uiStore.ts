import { create } from "zustand";

export type Theme = "light" | "dark";

interface UiState {
  theme: Theme;
  toggleTheme: () => void;
  setTheme: (theme: Theme) => void;
}

export const useUiStore = create<UiState>((set) => ({
  theme: "light",
  toggleTheme: () =>
    set((state) => ({ theme: state.theme === "light" ? "dark" : "light" })),
  // Explicit setter alongside toggleTheme — session/SessionProvider.tsx
  // restores a persisted theme on startup and needs to set an absolute
  // value, not toggle relative to whatever the store's default happens
  // to be.
  setTheme: (theme) => set({ theme }),
}));
