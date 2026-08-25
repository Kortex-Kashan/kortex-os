/**
 * A uniform stub — not real feature UI. Every default demo panel uses
 * this same factory; their actual interfaces (if any) ship in later
 * milestones. Split out of defaultPanels.ts because it needs JSX, which
 * can't compile in a plain .ts file (mirrors
 * workspace/PlaceholderApplication.tsx).
 */
export function createPlaceholderPanelContent() {
  return function PlaceholderPanelContent() {
    return <p className="p-3 text-body text-muted-foreground">Future application panel</p>;
  };
}
