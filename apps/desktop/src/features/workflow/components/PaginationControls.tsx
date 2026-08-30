/** Shared Prev/Next pagination footer for the workflow list surfaces (M5-A7). */

import { Button } from "@kortex/design-system";

export function PaginationControls({
  page,
  pageCount,
  hasPrev,
  hasNext,
  onPrev,
  onNext,
}: {
  page: number;
  pageCount: number;
  hasPrev: boolean;
  hasNext: boolean;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (pageCount <= 1) return null;
  return (
    <div className="flex items-center justify-between pt-2" role="navigation" aria-label="Pagination">
      <Button variant="outline" size="sm" onClick={onPrev} disabled={!hasPrev}>
        Previous
      </Button>
      <span className="text-xs text-muted-foreground">
        Page {page + 1} of {pageCount}
      </span>
      <Button variant="outline" size="sm" onClick={onNext} disabled={!hasNext}>
        Next
      </Button>
    </div>
  );
}
