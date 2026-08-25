import * as React from "react";
import { motion } from "motion/react";
import { Button, Spinner } from "@kortex/design-system";

// Reuses the shell's existing empty-state content instead of duplicating a
// near-identical placeholder. This is a one-way dependency from workspace/
// on shell/ (composition, not a shell edit) — shell/Workspace.tsx itself
// is untouched by M2.2.
import { WorkspaceEmptyState } from "@/shell/Workspace";
import { PanelLayout } from "@/panels/PanelLayout";

import { useWorkspace } from "./WorkspaceProvider";

interface WorkspaceErrorBoundaryState {
  error: Error | null;
}

class WorkspaceErrorBoundary extends React.Component<
  React.PropsWithChildren,
  WorkspaceErrorBoundaryState
> {
  state: WorkspaceErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): WorkspaceErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error) {
    // ADR-0002 §7.5 routes render errors to Rust-side logging via a
    // dedicated `report_render_error` IPC command — unavailable until M3's
    // IPC bridge exists, so console.error is the interim sink.
    console.error("Workspace application failed to render:", error);
  }

  private reset = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-full min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
          <p className="text-heading text-destructive">Application failed to load</p>
          <p className="max-w-sm text-body text-muted-foreground">{this.state.error.message}</p>
          <Button variant="outline" size="sm" onClick={this.reset}>
            Try again
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}

function WorkspaceLoadingState() {
  return (
    <div className="flex h-full min-h-[60vh] flex-col items-center justify-center gap-3">
      <Spinner size={24} />
      <p className="text-body text-muted-foreground">Loading application…</p>
    </div>
  );
}

export function WorkspaceView() {
  const { activeApplication } = useWorkspace();

  if (!activeApplication) {
    return (
      <PanelLayout>
        <WorkspaceEmptyState />
      </PanelLayout>
    );
  }

  const ActiveApplicationComponent = activeApplication.component;

  return (
    <PanelLayout>
      <WorkspaceErrorBoundary key={activeApplication.id}>
        <React.Suspense fallback={<WorkspaceLoadingState />}>
          <motion.div
            key={activeApplication.id}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.15 }}
            className="h-full"
          >
            <ActiveApplicationComponent />
          </motion.div>
        </React.Suspense>
      </WorkspaceErrorBoundary>
    </PanelLayout>
  );
}
