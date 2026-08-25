import { AssistantIcon, InspectorIcon, LogsIcon } from "./icons";
import { createPlaceholderPanelContent } from "./PlaceholderPanelContent";
import type { PanelDefinition } from "./panelTypes";

/**
 * Infrastructure examples only (M2.4 task brief) — not real features.
 * These demonstrate the three declarable panel kinds: a side panel
 * (Inspector), a bottom panel (Logs), and a contextual-tool panel
 * (Assistant — registered at "right", the contextual-tools rail per
 * panelTypes.ts's PanelPosition doc comment). Real application panels
 * register themselves the same way in later milestones.
 */
export const DEFAULT_PANELS: PanelDefinition[] = [
  {
    id: "inspector",
    title: "Inspector",
    icon: InspectorIcon,
    position: "right",
    component: createPlaceholderPanelContent(),
    defaultOpen: false,
    permissions: [],
  },
  {
    id: "logs",
    title: "Logs",
    icon: LogsIcon,
    position: "bottom",
    component: createPlaceholderPanelContent(),
    defaultOpen: true,
    defaultSize: { default: 160, min: 96, max: 480 },
    permissions: [],
  },
  {
    id: "assistant",
    title: "Assistant",
    icon: AssistantIcon,
    position: "right",
    component: createPlaceholderPanelContent(),
    defaultOpen: false,
    permissions: [],
  },
];
