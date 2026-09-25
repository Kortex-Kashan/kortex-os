import { useBrowserTabs } from "../hooks/useBrowserTabs";
import { BrowserTabBar } from "./BrowserTabBar";
import { BrowserToolbar } from "./BrowserToolbar";

/** Browser-B2: tabs, a toolbar (back/forward/reload/address), and the
 * content area the active tab's real WebView2 surface is positioned into
 * (`useBrowserTabs`' `containerRef`, tracked via `ResizeObserver` — no
 * polling). Deliberately does not implement: persistent profiles/sessions
 * (Browser-B3), downloads/bookmarks/history, provider authentication, or
 * any AI/automation capability — see `docs/architecture/
 * browser_known_limitations.md`. */
export function BrowserApp() {
  const {
    tabs,
    activeTabId,
    activeTab,
    containerRef,
    isBusy,
    error,
    openTab,
    closeTab,
    switchTab,
    navigate,
    reload,
    goBack,
    goForward,
  } = useBrowserTabs();

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-border/70 bg-background/40 shadow-low backdrop-blur-xl">
      <BrowserTabBar
        tabs={tabs}
        activeTabId={activeTabId}
        disabled={isBusy}
        onSwitch={switchTab}
        onClose={(id) => void closeTab(id)}
        onNewTab={() => void openTab()}
      />
      <BrowserToolbar
        url={activeTab?.state?.url ?? ""}
        loading={activeTab?.state?.loading ?? false}
        canGoBack={activeTab?.state?.canGoBack ?? false}
        canGoForward={activeTab?.state?.canGoForward ?? false}
        disabled={isBusy || !activeTab}
        onNavigate={(url) => void navigate(url)}
        onBack={() => void goBack()}
        onForward={() => void goForward()}
        onReload={() => void reload()}
      />
      {error && (
        <p className="border-b border-border/70 bg-destructive/10 px-3 py-1.5 text-caption text-destructive" role="alert">
          {error}
        </p>
      )}
      {/* The active tab's real WebView2 surface is positioned over exactly
          this element's on-screen rect by `useBrowserTabs` — it renders no
          visible content of its own (the surface sits above it), it only
          exists to be measured. */}
      <div ref={containerRef} className="min-h-0 flex-1" data-testid="browser-content-area" />
    </div>
  );
}
