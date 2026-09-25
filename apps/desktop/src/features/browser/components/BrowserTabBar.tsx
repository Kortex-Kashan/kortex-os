import { Button } from "@kortex/design-system";

import type { BrowserTab } from "../hooks/useBrowserTabs";

/** Every tab rendered here is backed by a real `BrowserSurfaceId`
 * (`BrowserTab.id`) created via `create_surface` — this component only
 * ever renders tabs it was handed, never invents a placeholder one. */
interface BrowserTabBarProps {
  tabs: BrowserTab[];
  activeTabId: string | null;
  disabled: boolean;
  onSwitch: (id: string) => void;
  onClose: (id: string) => void;
  onNewTab: () => void;
}

function tabLabel(tab: BrowserTab): string {
  if (!tab.state) return "New Tab";
  try {
    const host = new URL(tab.state.url).hostname;
    return host || tab.state.url;
  } catch {
    return tab.state.url;
  }
}

export function BrowserTabBar({ tabs, activeTabId, disabled, onSwitch, onClose, onNewTab }: BrowserTabBarProps) {
  return (
    <div
      role="tablist"
      aria-label="Browser tabs"
      className="flex items-center gap-1 border-b border-border/70 bg-background/60 px-2 pt-2"
    >
      {tabs.map((tab) => {
        const isActive = tab.id === activeTabId;
        return (
          <div
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            className={`group flex max-w-48 items-center gap-2 rounded-t-md border border-b-0 px-3 py-1.5 text-caption transition-colors ${
              isActive
                ? "border-border bg-background text-foreground"
                : "border-transparent bg-transparent text-muted-foreground hover:bg-accent/40"
            }`}
          >
            <button
              type="button"
              className="min-w-0 flex-1 truncate text-left"
              onClick={() => onSwitch(tab.id)}
              disabled={disabled}
              title={tab.state?.url ?? "New Tab"}
            >
              {tab.state?.loading && (
                <span className="mr-1 inline-block size-1.5 animate-pulse rounded-full bg-cyan" aria-hidden />
              )}
              {tabLabel(tab)}
            </button>
            <button
              type="button"
              aria-label={`Close ${tabLabel(tab)}`}
              className="rounded text-muted-foreground opacity-0 hover:bg-accent hover:text-foreground group-hover:opacity-100"
              disabled={disabled}
              onClick={(e) => {
                e.stopPropagation();
                onClose(tab.id);
              }}
            >
              <CloseIcon className="size-3.5" />
            </button>
          </div>
        );
      })}
      <Button
        size="icon"
        variant="ghost"
        className="mb-1 size-7"
        aria-label="New tab"
        disabled={disabled}
        onClick={onNewTab}
      >
        <PlusIcon className="size-4" />
      </Button>
    </div>
  );
}

function PlusIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden {...props}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function CloseIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden {...props}>
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}
