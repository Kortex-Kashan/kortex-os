import * as React from "react";

import { loadPanelState, savePanelState } from "./panelPersistence";
import { PanelRegistry } from "./PanelRegistry";
import { DEFAULT_PANEL_SIZE_PX, type PanelDefinition } from "./panelTypes";

interface PanelContextValue {
  panels: PanelDefinition[];
  openPanelIds: string[];
  activePanelId: string | null;
  registerPanel: (panel: PanelDefinition) => void;
  unregisterPanel: (id: string) => void;
  isPanelOpen: (id: string) => boolean;
  openPanel: (id: string) => void;
  closePanel: (id: string) => void;
  togglePanel: (id: string) => void;
  activatePanel: (id: string) => void;
  getPanelSize: (id: string) => number;
  setPanelSize: (id: string, size: number) => void;
}

const PanelContext = React.createContext<PanelContextValue | null>(null);

export function usePanels(): PanelContextValue {
  const context = React.useContext(PanelContext);
  if (!context) {
    throw new Error("usePanels must be used within a PanelProvider");
  }
  return context;
}

export interface PanelProviderProps {
  initialPanels?: PanelDefinition[];
  children: React.ReactNode;
}

function initialSizeFor(
  panel: PanelDefinition,
  persistedSizes: Record<string, number> | undefined,
): number {
  return persistedSizes?.[panel.id] ?? panel.defaultSize?.default ?? DEFAULT_PANEL_SIZE_PX;
}

function shouldOpenInitially(
  panel: PanelDefinition,
  persisted: { openPanelIds: string[] } | null,
): boolean {
  return persisted ? persisted.openPanelIds.includes(panel.id) : panel.defaultOpen;
}

/**
 * Plain React Context + useState — no Zustand, per the M2.4 task brief
 * (panel/open-closed state is neither the ephemeral local UI state nor
 * the server-derived data ADR-0002 §12 splits between Zustand and
 * TanStack Query, mirroring WorkspaceProvider's own reasoning for using a
 * dedicated context instead of `uiStore`).
 */
export function PanelProvider({ initialPanels = [], children }: PanelProviderProps) {
  // Registry construction, the initial-registration side effect, and the
  // one-time persisted-state read all live inside this ref-guarded block
  // for the same reason WorkspaceProvider guards its registry init:
  // StrictMode double-invokes render-phase functions, and registering
  // `initialPanels` twice against the same registry instance would throw
  // a false "already registered" on the second pass.
  const registryRef = React.useRef<PanelRegistry | null>(null);
  const persistedRef = React.useRef<ReturnType<typeof loadPanelState> | undefined>(undefined);
  if (!registryRef.current) {
    const registry = new PanelRegistry();
    for (const panel of initialPanels) {
      registry.register(panel);
    }
    registryRef.current = registry;
  }
  if (persistedRef.current === undefined) {
    persistedRef.current = loadPanelState();
  }
  const registry = registryRef.current;
  const persisted = persistedRef.current;

  const [panels, setPanels] = React.useState<PanelDefinition[]>(() => registry.list());

  const [openPanelIds, setOpenPanelIds] = React.useState<string[]>(() =>
    registry
      .list()
      .filter((panel) => shouldOpenInitially(panel, persisted))
      .map((panel) => panel.id),
  );

  const [sizes, setSizes] = React.useState<Record<string, number>>(() => {
    const initial: Record<string, number> = {};
    for (const panel of registry.list()) {
      initial[panel.id] = initialSizeFor(panel, persisted?.sizes);
    }
    return initial;
  });

  const [activePanelId, setActivePanelId] = React.useState<string | null>(null);

  const registerPanel = React.useCallback(
    (panel: PanelDefinition) => {
      registry.register(panel);
      setPanels(registry.list());
      setOpenPanelIds((current) =>
        current.includes(panel.id) || !shouldOpenInitially(panel, persisted)
          ? current
          : [...current, panel.id],
      );
      setSizes((current) => ({ ...current, [panel.id]: initialSizeFor(panel, persisted?.sizes) }));
    },
    [registry, persisted],
  );

  const unregisterPanel = React.useCallback(
    (id: string) => {
      registry.unregister(id);
      setPanels(registry.list());
      setOpenPanelIds((current) => current.filter((panelId) => panelId !== id));
      setSizes((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
      setActivePanelId((current) => (current === id ? null : current));
    },
    [registry],
  );

  const isPanelOpen = React.useCallback((id: string) => openPanelIds.includes(id), [openPanelIds]);

  const openPanel = React.useCallback(
    (id: string) => {
      if (!registry.has(id)) {
        throw new Error(`Cannot open unknown panel "${id}".`);
      }
      setOpenPanelIds((current) => (current.includes(id) ? current : [...current, id]));
    },
    [registry],
  );

  const closePanel = React.useCallback((id: string) => {
    setOpenPanelIds((current) => current.filter((panelId) => panelId !== id));
    setActivePanelId((current) => (current === id ? null : current));
  }, []);

  const togglePanel = React.useCallback(
    (id: string) => {
      if (!registry.has(id)) {
        throw new Error(`Cannot toggle unknown panel "${id}".`);
      }
      setOpenPanelIds((current) =>
        current.includes(id) ? current.filter((panelId) => panelId !== id) : [...current, id],
      );
    },
    [registry],
  );

  const activatePanel = React.useCallback(
    (id: string) => {
      if (!registry.has(id)) {
        throw new Error(`Cannot activate unknown panel "${id}".`);
      }
      setOpenPanelIds((current) => (current.includes(id) ? current : [...current, id]));
      setActivePanelId(id);
    },
    [registry],
  );

  const getPanelSize = React.useCallback(
    (id: string) => sizes[id] ?? DEFAULT_PANEL_SIZE_PX,
    [sizes],
  );

  const setPanelSize = React.useCallback((id: string, size: number) => {
    setSizes((current) => ({ ...current, [id]: size }));
  }, []);

  // The single mechanism keeping localStorage in sync with open/closed and
  // size state — every mutation above funnels through setOpenPanelIds/
  // setSizes, so this effect is the only place persistence is written.
  React.useEffect(() => {
    savePanelState({ openPanelIds, sizes });
  }, [openPanelIds, sizes]);

  const value = React.useMemo<PanelContextValue>(
    () => ({
      panels,
      openPanelIds,
      activePanelId,
      registerPanel,
      unregisterPanel,
      isPanelOpen,
      openPanel,
      closePanel,
      togglePanel,
      activatePanel,
      getPanelSize,
      setPanelSize,
    }),
    [
      panels,
      openPanelIds,
      activePanelId,
      registerPanel,
      unregisterPanel,
      isPanelOpen,
      openPanel,
      closePanel,
      togglePanel,
      activatePanel,
      getPanelSize,
      setPanelSize,
    ],
  );

  return <PanelContext.Provider value={value}>{children}</PanelContext.Provider>;
}
