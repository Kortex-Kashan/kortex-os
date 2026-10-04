import { useEffect } from "react";

import { BrowserSessionProvider, useBrowserSession, type BrowserSession } from "../BrowserSessionProvider";
import { BrowserTabBar } from "./BrowserTabBar";
import { BrowserToolbar } from "./BrowserToolbar";
import { ProfileSwitcher } from "./ProfileSwitcher";

/** Browser-B2/B3: profiles, tabs, a toolbar (back/forward/reload/address),
 * and the content area the active tab's real WebView2 surface is
 * positioned into (`useBrowserTabs`' `containerRef`, tracked via
 * `ResizeObserver` — no polling). The profiles and tabs themselves live in
 * the shell-level `BrowserSessionProvider`, so this view unmounting (the
 * user switching to another KORTEX application) keeps every tab alive and
 * parked off-screen until it is shown again. Rendered outside a signed-in
 * shell, it owns a session of its own, ended when it unmounts. Switching
 * profiles parks the previous profile's tabs and shows the newly active
 * profile's own tabs again (one new tab only if it has none). Deliberately
 * does not implement: downloads/bookmarks/history, provider authentication,
 * or any AI/automation capability — see
 * `docs/architecture/browser_known_limitations.md`. */
export function BrowserApp() {
  const session = useBrowserSession();
  if (session === null) {
    return (
      <BrowserSessionProvider>
        <BrowserView />
      </BrowserSessionProvider>
    );
  }
  return <BrowserView />;
}

function BrowserView() {
  const { profiles, tabs, activate } = useBrowserSession() as BrowserSession;
  useEffect(() => {
    activate();
  }, [activate]);

  const {
    profiles: profileList,
    activeProfileId,
    isLoading: isLoadingProfiles,
    error: profileError,
    switchProfile,
    createProfile,
    renameProfile,
    deleteProfile,
  } = profiles;

  const {
    tabs: tabList,
    activeTabId,
    activeTab,
    containerRef,
    isBusy,
    error: tabError,
    openTab,
    closeTab,
    closeProfileTabs,
    switchTab,
    navigate,
    reload,
    goBack,
    goForward,
  } = tabs;

  // With no active profile, the profile error is the cause (e.g. no
  // authenticated tenant) and a tab error only its consequence — show the
  // cause, never mask it.
  const error = activeProfileId === null ? (profileError ?? tabError) : (tabError ?? profileError);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-border/70 bg-background/40 shadow-low backdrop-blur-xl">
      <ProfileSwitcher
        profiles={profileList}
        activeProfileId={activeProfileId}
        disabled={isBusy || isLoadingProfiles}
        onSwitch={switchProfile}
        onCreate={(displayName) => void createProfile(displayName)}
        onRename={(profileId, displayName) => void renameProfile(profileId, displayName)}
        onDelete={(profileId) =>
          // A deleted profile's parked tabs are still live surfaces holding
          // its lock — close them first (the backend refuses a locked profile).
          void closeProfileTabs(profileId).then(() => deleteProfile(profileId))
        }
      />
      <BrowserTabBar
        tabs={tabList}
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
          exists to be measured. Its absence is what parks every surface. */}
      <div ref={containerRef} className="min-h-0 flex-1" data-testid="browser-content-area" />
    </div>
  );
}
