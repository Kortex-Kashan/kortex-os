import { useState } from "react";
import { Button } from "@kortex/design-system";

import type { BrowserProfileId, ProfileSummary } from "../api";

interface ProfileSwitcherProps {
  profiles: ProfileSummary[];
  activeProfileId: BrowserProfileId | null;
  disabled: boolean;
  onSwitch: (profileId: BrowserProfileId) => void;
  onCreate: (displayName: string) => void;
  onRename: (profileId: BrowserProfileId, displayName: string) => void;
  onDelete: (profileId: BrowserProfileId) => void;
}

function availabilityLabel(profile: ProfileSummary): string | null {
  switch (profile.availability.kind) {
    case "locked":
      return "In use";
    case "corrupted":
      return "Corrupted";
    case "available":
      return null;
  }
}

/** Browser-B3: compact profile selector. Per the approved V1 UX (decision
 * D24), switching profiles is handled entirely by `useBrowserTabs`
 * reacting to `activeProfileId` changing — this component only ever
 * reports the user's intent (switch/create/rename/delete), it never
 * touches a `BrowserSurfaceId` itself. */
export function ProfileSwitcher({
  profiles,
  activeProfileId,
  disabled,
  onSwitch,
  onCreate,
  onRename,
  onDelete,
}: ProfileSwitcherProps) {
  const [isCreating, setIsCreating] = useState(false);
  const [newProfileName, setNewProfileName] = useState("");
  const [renamingId, setRenamingId] = useState<BrowserProfileId | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [pendingDeleteId, setPendingDeleteId] = useState<BrowserProfileId | null>(null);

  function submitCreate() {
    const trimmed = newProfileName.trim();
    if (!trimmed) return;
    onCreate(trimmed);
    setNewProfileName("");
    setIsCreating(false);
  }

  function submitRename(profileId: BrowserProfileId) {
    const trimmed = renameValue.trim();
    if (trimmed) {
      onRename(profileId, trimmed);
    }
    setRenamingId(null);
  }

  return (
    <div className="flex items-center gap-1 border-b border-border/70 bg-background/60 px-2 py-1.5">
      {/* `radiogroup`/`radio`, not `tablist`/`tab` — exactly one profile is
          selected at a time, but there is no per-profile content panel the
          way `BrowserTabBar`'s own (genuine) tabs have; reusing `tab` here
          would also collide with that component's ARIA role in the same
          page. */}
      <div className="flex flex-1 flex-wrap items-center gap-1" role="radiogroup" aria-label="Browser profiles">
        {profiles.map((profile) => {
          const isActive = profile.profileId === activeProfileId;
          const badge = availabilityLabel(profile);
          const isRenaming = renamingId === profile.profileId;
          return (
            <div
              key={profile.profileId}
              role="radio"
              aria-checked={isActive}
              className={`group flex items-center gap-1.5 rounded-md border px-2 py-1 text-caption transition-colors ${
                isActive
                  ? "border-primary/60 bg-primary/10 text-foreground"
                  : "border-transparent text-muted-foreground hover:bg-accent/40"
              }`}
            >
              {isRenaming ? (
                <input
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submitRename(profile.profileId);
                    if (e.key === "Escape") setRenamingId(null);
                  }}
                  onBlur={() => submitRename(profile.profileId)}
                  className="w-28 rounded border border-input bg-background px-1 py-0.5 text-caption"
                  aria-label="Profile name"
                />
              ) : (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onSwitch(profile.profileId)}
                  onDoubleClick={() => {
                    setRenamingId(profile.profileId);
                    setRenameValue(profile.displayName);
                  }}
                  title={badge ? `${profile.displayName} — ${badge}` : profile.displayName}
                >
                  {profile.displayName}
                </button>
              )}
              {badge && (
                <span
                  className={`rounded px-1 text-[10px] uppercase ${
                    profile.availability.kind === "corrupted"
                      ? "bg-destructive/20 text-destructive"
                      : "bg-muted text-muted-foreground"
                  }`}
                >
                  {badge}
                </span>
              )}
              {!isActive && !isRenaming && (
                <button
                  type="button"
                  aria-label={`Delete ${profile.displayName}`}
                  className="rounded text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100"
                  disabled={disabled}
                  onClick={(e) => {
                    e.stopPropagation();
                    setPendingDeleteId(profile.profileId);
                  }}
                >
                  ×
                </button>
              )}
            </div>
          );
        })}
      </div>

      {isCreating ? (
        <div className="flex items-center gap-1">
          <input
            autoFocus
            value={newProfileName}
            onChange={(e) => setNewProfileName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitCreate();
              if (e.key === "Escape") setIsCreating(false);
            }}
            placeholder="Profile name"
            className="w-32 rounded border border-input bg-background px-2 py-1 text-caption"
            aria-label="New profile name"
            data-testid="new-profile-name-input"
          />
          <Button size="sm" variant="outline" disabled={disabled} onClick={submitCreate}>
            Create
          </Button>
        </div>
      ) : (
        <Button
          size="sm"
          variant="ghost"
          disabled={disabled}
          onClick={() => setIsCreating(true)}
          data-testid="new-profile-button"
        >
          New Profile
        </Button>
      )}

      {pendingDeleteId && (
        <div
          role="alertdialog"
          aria-label="Confirm profile deletion"
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/80"
        >
          <div className="rounded-lg border border-border bg-background p-4 shadow-low">
            <p className="mb-3 text-body">
              Delete profile "{profiles.find((p) => p.profileId === pendingDeleteId)?.displayName}"? This removes its
              browsing data and cannot be undone.
            </p>
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setPendingDeleteId(null)}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => {
                  onDelete(pendingDeleteId);
                  setPendingDeleteId(null);
                }}
              >
                Delete
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
