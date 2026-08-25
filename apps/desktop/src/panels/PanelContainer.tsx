import * as React from "react";
import type { ComponentType, SVGProps } from "react";
import { motion } from "motion/react";
import { Button, cn, motionTokens } from "@kortex/design-system";

import { ChevronIcon, CloseIcon } from "./icons";

export interface PanelContainerProps {
  title: string;
  icon?: ComponentType<SVGProps<SVGSVGElement>>;
  /** Uncontrolled initial collapse state. Ignored once `collapsed` is passed. */
  defaultCollapsed?: boolean;
  /** Controlled collapse state — omit to let PanelContainer manage its own. */
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
  /** Omit to render the panel without a close control. */
  onClose?: () => void;
  children: React.ReactNode;
  className?: string;
}

/**
 * Reusable panel chrome: title, collapse toggle, close button, content.
 * Deliberately decoupled from PanelProvider/PanelRegistry — it takes
 * plain props, not a PanelDefinition — so it stays testable and reusable
 * in isolation (ADR-0002 §3 principle 7). PanelLayout is what wires it to
 * the panel context.
 */
export function PanelContainer({
  title,
  icon: Icon,
  defaultCollapsed = false,
  collapsed: collapsedProp,
  onCollapsedChange,
  onClose,
  children,
  className,
}: PanelContainerProps) {
  const [collapsedState, setCollapsedState] = React.useState(defaultCollapsed);
  const collapsed = collapsedProp ?? collapsedState;

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsedState(next);
    onCollapsedChange?.(next);
  };

  return (
    <div
      className={cn("flex flex-col overflow-hidden bg-card text-card-foreground", className)}
      data-testid="panel-container"
      data-collapsed={collapsed}
    >
      <div className="flex h-8 shrink-0 items-center justify-between gap-2 border-b border-border px-2">
        <div className="flex min-w-0 items-center gap-1.5">
          {Icon && <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />}
          <span className="truncate text-caption font-medium">{title}</span>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon"
            className="size-6"
            aria-label={collapsed ? `Expand ${title}` : `Collapse ${title}`}
            onClick={toggleCollapsed}
          >
            <ChevronIcon className={cn("size-3.5 transition-transform", collapsed && "-rotate-90")} />
          </Button>
          {onClose && (
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              aria-label={`Close ${title}`}
              onClick={onClose}
            >
              <CloseIcon className="size-3.5" />
            </Button>
          )}
        </div>
      </div>
      {!collapsed && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: motionTokens.duration.fast }}
          className="flex-1 overflow-auto"
        >
          {children}
        </motion.div>
      )}
    </div>
  );
}
