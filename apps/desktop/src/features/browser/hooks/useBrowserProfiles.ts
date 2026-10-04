import { useCallback, useEffect, useState } from "react";

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
 * persisted profile like any other, just created on the user's behalf).
 *
 * Loads when `enabled` (the Browser view has been opened at least once) and
 * loads AGAIN whenever `identityKey` (the signed-in identity) changes — the
 * profile list belongs to the authenticated tenant, so it is never loaded
 * once and kept across a change of identity. The desktop resolves the
 * tenant itself; `identityKey` is only the trigger to re-ask. */
export function useBrowserProfiles({
  enabled = true,
  identityKey = null,
}: { enabled?: boolean; identityKey?: string | null } = {}) {
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [activeProfileId, setActiveProfileId] = useState<BrowserProfileId | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
    if (!enabled) return;
    // A superseded load (StrictMode's dev-only double effect run, or an
    // identity change mid-load) must not apply its result or auto-create a
    // second "Default" profile.
    let cancelled = false;
    void (async () => {
      setIsLoading(true);
      setError(null);
      try {
        let list = await listBrowserProfiles();
        if (cancelled) return;
        if (list.length === 0) {
          await createBrowserProfile(DEFAULT_FIRST_PROFILE_NAME);
          list = await listBrowserProfiles();
          if (cancelled) return;
        }
        setProfiles(list);
        if (list.length > 0) {
          // Most-recently-opened first, falling back to the first entry —
          // a reasonable default when nothing has been opened yet.
          const mostRecent = [...list].sort((a, b) => (b.lastOpenedAt ?? 0) - (a.lastOpenedAt ?? 0))[0];
          setActiveProfileId(mostRecent.profileId);
        } else {
          setActiveProfileId(null);
        }
      } catch (err) {
        if (cancelled) return;
        // Fail closed: e.g. no authenticated tenant yet — nothing is active.
        setProfiles([]);
        setActiveProfileId(null);
        setError(browserRuntimeErrorMessage(err));
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, identityKey]);

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
   * against this profile — the caller must close its tabs first. `BrowserApp`
   * only offers deletion for the currently-INACTIVE profiles in the
   * switcher, whose tabs are parked but still alive, so it closes them
   * (`useBrowserTabs.closeProfileTabs`) before calling this. */
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
