import * as React from "react";
import { motion } from "motion/react";
import { motionTokens } from "@kortex/design-system";

import { PanelContainer } from "./PanelContainer";
import { usePanels } from "./PanelProvider";
import type { PanelDefinition, PanelPosition } from "./panelTypes";

export interface PanelLayoutProps {
  children: React.ReactNode;
}

function openPanelsForPosition(
  panels: PanelDefinition[],
  openPanelIds: string[],
  position: PanelPosition,
): PanelDefinition[] {
  return panels.filter((panel) => panel.position === position && openPanelIds.includes(panel.id));
}

/**
 * Renders the OS-owned workspace composition areas — left, main, right,
 * and bottom — around whatever the caller passes as `children` (the
 * active application, per the WorkspaceView → PanelLayout → Active
 * Application + Panels integration). Applications never lay themselves
 * out directly; they only register panels via PanelProvider, and this is
 * the single place those registrations become screen area.
 */
export function PanelLayout({ children }: PanelLayoutProps) {
  const { panels, openPanelIds, getPanelSize, closePanel } = usePanels();

  const leftPanels = openPanelsForPosition(panels, openPanelIds, "left");
  const rightPanels = openPanelsForPosition(panels, openPanelIds, "right");
  const bottomPanels = openPanelsForPosition(panels, openPanelIds, "bottom");

  const leftWidth = Math.max(0, ...leftPanels.map((panel) => getPanelSize(panel.id)));
  const rightWidth = Math.max(0, ...rightPanels.map((panel) => getPanelSize(panel.id)));
  const bottomHeight = Math.max(0, ...bottomPanels.map((panel) => getPanelSize(panel.id)));

  return (
    <div className="flex h-full flex-1 overflow-hidden" data-testid="panel-layout">
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="flex flex-1 overflow-hidden">
          {leftPanels.length > 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: motionTokens.duration.fast }}
              className="flex shrink-0 flex-col divide-y divide-border overflow-hidden border-r border-border"
              style={{ width: leftWidth }}
              data-testid="panel-area-left"
            >
              {leftPanels.map((panel) => (
                <PanelContainer
                  key={panel.id}
                  title={panel.title}
                  icon={panel.icon}
                  onClose={() => closePanel(panel.id)}
                  className="flex-1"
                >
                  <panel.component />
                </PanelContainer>
              ))}
            </motion.div>
          )}

          <div className="min-w-0 flex-1 overflow-auto" data-testid="panel-area-main">
            {children}
          </div>

          {rightPanels.length > 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: motionTokens.duration.fast }}
              className="flex shrink-0 flex-col divide-y divide-border overflow-hidden border-l border-border"
              style={{ width: rightWidth }}
              data-testid="panel-area-right"
            >
              {rightPanels.map((panel) => (
                <PanelContainer
                  key={panel.id}
                  title={panel.title}
                  icon={panel.icon}
                  onClose={() => closePanel(panel.id)}
                  className="flex-1"
                >
                  <panel.component />
                </PanelContainer>
              ))}
            </motion.div>
          )}
        </div>

        {bottomPanels.length > 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: motionTokens.duration.fast }}
            className="flex shrink-0 flex-row divide-x divide-border overflow-hidden border-t border-border"
            style={{ height: bottomHeight }}
            data-testid="panel-area-bottom"
          >
            {bottomPanels.map((panel) => (
              <PanelContainer
                key={panel.id}
                title={panel.title}
                icon={panel.icon}
                onClose={() => closePanel(panel.id)}
                className="flex-1"
              >
                <panel.component />
              </PanelContainer>
            ))}
          </motion.div>
        )}
      </div>
    </div>
  );
}
