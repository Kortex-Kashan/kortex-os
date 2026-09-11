import * as React from "react";
import { Badge, Button } from "@kortex/design-system";

export interface BuilderToolbarProps {
  name: string;
  description: string;
  status: string;
  isDirty: boolean;
  isSaving: boolean;
  isValidating: boolean;
  canPublish: boolean;
  canUndo: boolean;
  canRedo: boolean;
  zoom: number;
  onNameChange: (name: string) => void;
  onDescriptionChange: (description: string) => void;
  onSaveDraft: () => void;
  onValidate: () => void;
  onReviewAndPublish: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetZoom: () => void;
  onAutoLayout: () => void;
  onUndo: () => void;
  onRedo: () => void;
}

export function BuilderToolbar({
  name,
  description,
  status,
  isDirty,
  isSaving,
  isValidating,
  canPublish,
  canUndo,
  canRedo,
  zoom,
  onNameChange,
  onDescriptionChange,
  onSaveDraft,
  onValidate,
  onReviewAndPublish,
  onZoomIn,
  onZoomOut,
  onResetZoom,
  onAutoLayout,
  onUndo,
  onRedo,
}: BuilderToolbarProps): React.JSX.Element {
  const [isEditingDesc, setIsEditingDesc] = React.useState(false);

  return (
    <header
      aria-label="Workflow Builder Toolbar"
      className="h-14 border-b border-border bg-card/80 backdrop-blur px-4 flex items-center justify-between gap-4 select-none z-10"
      data-testid="builder-toolbar"
    >
      {/* Left: Workflow title, description, and status */}
      <div className="flex items-center gap-3 min-w-0">
        <div className="flex flex-col min-w-0">
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              placeholder="Untitled Workflow"
              className="bg-transparent text-sm font-semibold text-foreground focus:outline-none focus:ring-1 focus:ring-primary rounded px-1 -ml-1 w-52 truncate"
              data-testid="builder-name-input"
            />
            <Badge variant="outline" className="text-[10px] uppercase tracking-wider px-1.5 py-0">
              {status}
            </Badge>
            {isDirty && (
              <Badge
                variant="secondary"
                className="text-[10px] bg-amber-500/10 text-amber-500 border border-amber-500/30 px-1.5 py-0 font-medium"
                data-testid="builder-unsaved-badge"
              >
                Unsaved Changes
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            {isEditingDesc ? (
              <input
                type="text"
                value={description}
                onChange={(e) => onDescriptionChange(e.target.value)}
                onBlur={() => setIsEditingDesc(false)}
                onKeyDown={(e) => e.key === "Enter" && setIsEditingDesc(false)}
                autoFocus
                placeholder="Workflow description..."
                className="bg-background text-[11px] text-foreground border border-input rounded px-1.5 py-0.5 w-72 focus:outline-none"
              />
            ) : (
              <span
                onClick={() => setIsEditingDesc(true)}
                className="text-[11px] text-muted-foreground truncate max-w-xs cursor-pointer hover:text-foreground"
                title="Click to edit description"
              >
                {description || "Add description..."}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Middle: Canvas Viewport & Layout Controls */}
      <div className="flex items-center gap-1 bg-muted/40 p-1 rounded-md border border-border">
        <Button
          variant="ghost"
          size="sm"
          onClick={onUndo}
          disabled={!canUndo}
          className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
          title="Undo (Ctrl+Z)"
          data-testid="builder-undo-button"
        >
          ↶
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onRedo}
          disabled={!canRedo}
          className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
          title="Redo (Ctrl+Y)"
          data-testid="builder-redo-button"
        >
          ↷
        </Button>
        <div className="w-px h-4 bg-border mx-1" />
        <Button
          variant="ghost"
          size="sm"
          onClick={onZoomOut}
          className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
          title="Zoom Out"
          data-testid="builder-zoom-out-button"
        >
          −
        </Button>
        <span
          onClick={onResetZoom}
          className="text-[11px] font-mono font-medium text-muted-foreground w-12 text-center cursor-pointer hover:text-foreground"
          title="Reset View"
          data-testid="builder-zoom-indicator"
        >
          {Math.round(zoom * 100)}%
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={onZoomIn}
          className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
          title="Zoom In"
          data-testid="builder-zoom-in-button"
        >
          +
        </Button>
        <div className="w-px h-4 bg-border mx-1" />
        <Button
          variant="ghost"
          size="sm"
          onClick={onAutoLayout}
          className="h-7 text-xs px-2 text-muted-foreground hover:text-foreground"
          title="Auto-arrange steps in a clean vertical line"
          data-testid="builder-autolayout-button"
        >
          Align
        </Button>
      </div>

      {/* Right: Save, Validate, Review & Publish */}
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={onSaveDraft}
          disabled={isSaving}
          className="h-8 text-xs font-medium"
          data-testid="builder-save-draft-button"
        >
          {isSaving ? "Saving..." : "Save Draft"}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={onValidate}
          disabled={isValidating}
          className="h-8 text-xs"
          data-testid="builder-validate-button"
        >
          {isValidating ? "Validating..." : "Validate"}
        </Button>

        <Button
          variant="default"
          size="sm"
          onClick={onReviewAndPublish}
          disabled={!canPublish}
          className="h-8 text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90"
          data-testid="builder-publish-button"
        >
          Review &amp; Publish
        </Button>
      </div>
    </header>
  );
}
