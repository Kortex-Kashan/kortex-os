import * as React from "react";

import { WorkspaceRegistry } from "./WorkspaceRegistry";
import type { WorkspaceApplication } from "./workspaceTypes";

interface WorkspaceContextValue {
  applications: WorkspaceApplication[];
  activeApplicationId: string | null;
  activeApplication: WorkspaceApplication | null;
  setActiveApplication: (id: string | null) => void;
  registerApplication: (application: WorkspaceApplication) => void;
  unregisterApplication: (id: string) => void;
}

const WorkspaceContext = React.createContext<WorkspaceContextValue | null>(null);

export function useWorkspace(): WorkspaceContextValue {
  const context = React.useContext(WorkspaceContext);
  if (!context) {
    throw new Error("useWorkspace must be used within a WorkspaceProvider");
  }
  return context;
}

export interface WorkspaceProviderProps {
  initialApplications?: WorkspaceApplication[];
  children: React.ReactNode;
}

/**
 * Plain React Context + useState — no new state library, and the existing
 * Zustand `uiStore` is untouched. Workspace/application state doesn't fit
 * either of ADR-0002 §12's existing buckets (it's neither ephemeral UI
 * state nor server-derived data), so it gets its own dedicated context
 * rather than being folded into uiStore.
 */
export function WorkspaceProvider({ initialApplications = [], children }: WorkspaceProviderProps) {
  // The registry instance AND the initial-registration side effect both
  // live inside this one ref-guarded block, not split across it and a
  // useState lazy initializer. StrictMode (main.tsx wraps the app in it)
  // deliberately double-invokes render-phase functions — including
  // useState initializers — to catch impure side effects; registering
  // `initialApplications` from inside a useState initializer would run
  // register() twice against the same registry instance (the ref itself
  // persists fine across the extra render) and throw a false "already
  // registered" on the second pass. Guarding the whole thing behind the
  // ref check keeps it to exactly one real invocation.
  const registryRef = React.useRef<WorkspaceRegistry | null>(null);
  if (!registryRef.current) {
    const registry = new WorkspaceRegistry();
    for (const application of initialApplications) {
      registry.register(application);
    }
    registryRef.current = registry;
  }
  const registry = registryRef.current;

  const [applications, setApplications] = React.useState<WorkspaceApplication[]>(() => registry.list());

  const [activeApplicationId, setActiveApplicationId] = React.useState<string | null>(null);

  const registerApplication = React.useCallback(
    (application: WorkspaceApplication) => {
      registry.register(application);
      setApplications(registry.list());
    },
    [registry],
  );

  const unregisterApplication = React.useCallback(
    (id: string) => {
      registry.unregister(id);
      setApplications(registry.list());
      setActiveApplicationId((current) => (current === id ? null : current));
    },
    [registry],
  );

  const setActiveApplication = React.useCallback(
    (id: string | null) => {
      if (id !== null && !registry.has(id)) {
        throw new Error(`Cannot activate unknown workspace application "${id}".`);
      }
      setActiveApplicationId(id);
    },
    [registry],
  );

  const activeApplication = React.useMemo(
    () => (activeApplicationId ? (registry.get(activeApplicationId) ?? null) : null),
    // `applications` is intentionally a dependency: it changes identity on
    // every (un)register, which is what tells this memo to recompute after
    // the registry's underlying contents change.
    [activeApplicationId, applications, registry],
  );

  const value = React.useMemo<WorkspaceContextValue>(
    () => ({
      applications,
      activeApplicationId,
      activeApplication,
      setActiveApplication,
      registerApplication,
      unregisterApplication,
    }),
    [
      applications,
      activeApplicationId,
      activeApplication,
      setActiveApplication,
      registerApplication,
      unregisterApplication,
    ],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
