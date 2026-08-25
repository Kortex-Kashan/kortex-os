import { useEffect } from "react";
import { useUiStore } from "@/stores/uiStore";

export function useThemeSync() {
  const theme = useUiStore((state) => state.theme);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);
}
