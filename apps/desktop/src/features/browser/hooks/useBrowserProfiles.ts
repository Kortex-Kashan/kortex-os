import { useCallback, useEffect, useRef, useState } from "react";

import {
  createBrowserProfile,
  deleteBrowserProfile,
  listBrowserProfiles,
  renameBrowserProfile,
  type BrowserProfileId,
  type ProfileSummary,
} from "../api";
import { browserRuntimeErrorMessage } from "./useBrowserTabs";

const DEFAULT_FIRST_PROFILE_NAME = "Default";

/** Browser-B3: profile list + active-profile selection, composed with
 * `useBrowserTabs(activeProfileId)` by `BrowserApp`. On first load, if the
 * current tenant has no profiles yet, one is auto-created (named
 * "Default") and made active — avoids a jarring empty state on first use
 * without inventing a fallback/shared profile id anywhere (this is a real,
 * persisted profile like any other, just created on the user's behalf). */
export function useBrowserProfiles() {
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [activeProfileId, setActiveProfileId] = useState<BrowserProfileId | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const hasInitializedRef = useRef(false);

  const refreshProfiles = useCallback(async () => {
    try {
      const list = await listBrowserProfiles();
      setProfiles(list);
      return list;
    } catch (err) {
      setError(browserRuntimeErrorMessage(err));
      return [];
    }
  }, []);

  useEffect(() => {
    if (hasInitializedRef.current) return;
    hasInitializedRef.current = true;
    void (async () => {
      setIsLoading(true);
      setError(null);
      try {
        let list = await listBrowserProfiles();
        if (list.length === 0) {
          await createBrowserProfile(DEFAULT_FIRST_PROFILE_NAME);
          list = await listBrowserProfiles();
        }
        setProfiles(list);
        if (list.length > 0) {
          // Most-recently-opened first, falling back to the first entry —
          // a reasonable default when nothing has been opened yet.
          const mostRecent = [...list].sort((a, b) => (b.lastOpenedAt ?? 0) - (a.lastOpenedAt ?? 0))[0];
          setActiveProfileId(mostRecent.profileId);
        }
      } catch (err) {
        setError(browserRuntimeErrorMessage(err));
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  const switchProfile = useCallback((profileId: BrowserProfileId) => {
    setActiveProfileId(profileId);
  }, []);

  const createProfile = useCallback(
    async (displayName: string) => {
      setError(null);
      try {
        const profileId = await createBrowserProfile(displayName);
        await refreshProfiles();
        setActiveProfileId(profileId);
        return profileId;
      } catch (err) {
        setError(browserRuntimeErrorMessage(err));
        return null;
      }
    },
    [refreshProfiles],
  );

  const renameProfile = useCallback(
    async (profileId: BrowserProfileId, displayName: string) => {
      setError(null);
      try {
        await renameBrowserProfile(profileId, displayName);
        await refreshProfiles();
      } catch (err) {
        setError(browserRuntimeErrorMessage(err));
      }
    },
    [refreshProfiles],
  );

  /** Refused by the backend (`ProfileLocked`) while any tab is still open
   * against this profile — the caller must close its tabs first (`BrowserApp`
   * only offers deletion for the currently-INACTIVE profiles in the
   * switcher, so this is not the active/open one in practice). */
  const deleteProfile = useCallback(
    async (profileId: BrowserProfileId) => {
      setError(null);
      try {
        await deleteBrowserProfile(profileId);
        const list = await refreshProfiles();
        if (activeProfileId === profileId) {
          setActiveProfileId(list.length > 0 ? list[0].profileId : null);
        }
      } catch (err) {
        setError(browserRuntimeErrorMessage(err));
      }
    },
    [refreshProfiles, activeProfileId],
  );

  return {
    profiles,
    activeProfileId,
    isLoading,
    error,
    switchProfile,
    createProfile,
    renameProfile,
    deleteProfile,
    refreshProfiles,
  };
}
