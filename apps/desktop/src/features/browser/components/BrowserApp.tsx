import { useBrowserProfiles } from "../hooks/useBrowserProfiles";
import { useBrowserTabs } from "../hooks/useBrowserTabs";
import { BrowserTabBar } from "./BrowserTabBar";
import { BrowserToolbar } from "./BrowserToolbar";
import { ProfileSwitcher } from "./ProfileSwitcher";

/** Browser-B2/B3: profiles, tabs, a toolbar (back/forward/reload/address),
 * and the content area the active tab's real WebView2 surface is
 * positioned into (`useBrowserTabs`' `containerRef`, tracked via
 * `ResizeObserver` — no polling). `useBrowserProfiles` owns the profile
 * list/selection; `useBrowserTabs(activeProfileId)` reacts to the active
 * profile changing (decision D24: closes every tab, opens one fresh tab
 * against the new profile). Deliberately does not implement: downloads/
 * bookmarks/history, provider authentication, or any AI/automation
 * capability — see `docs/architecture/browser_known_limitations.md`. */
export function BrowserApp() {
  const {
    profiles,
    activeProfileId,
    isLoading: isLoadingProfiles,
    error: profileError,
    switchProfile,
    createProfile,
    renameProfile,
    deleteProfile,
  } = useBrowserProfiles();

  const {
    tabs,
    activeTabId,
    activeTab,
    containerRef,
    isBusy,
    error: tabError,
    openTab,
    closeTab,
    switchTab,
    navigate,
    reload,
    goBack,
    goForward,
  } = useBrowserTabs(activeProfileId);

  const error = tabError ?? profileError;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-border/70 bg-background/40 shadow-low backdrop-blur-xl">
      <ProfileSwitcher
        profiles={profiles}
        activeProfileId={activeProfileId}
        disabled={isBusy || isLoadingProfiles}
        onSwitch={switchProfile}
        onCreate={(displayName) => void createProfile(displayName)}
        onRename={(profileId, displayName) => void renameProfile(profileId, displayName)}
        onDelete={(profileId) => void deleteProfile(profileId)}
      />
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
