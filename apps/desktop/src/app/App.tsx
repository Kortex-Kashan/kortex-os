import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { queryClient } from "@/lib/queryClient";
import { router } from "@/routes";
import { useThemeSync } from "@/hooks/useTheme";
import { useKortexEventStream } from "@/hooks/useKortexEventStream";

export function App() {
  useThemeSync();
  useKortexEventStream();

  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
