import { useMemo, useState } from "react";

/**
 * Minimal client-side pagination over an already-fetched array (M5-A7).
 *
 * None of `kortex.workflow.instance.list` / `approval.list` / `schedule.list`
 * support server-side `limit`/`offset` today, so this cannot bound the
 * network fetch — only the number of cards rendered at once, which is the
 * concrete problem this addresses (a raw unpaginated list of "dozens or
 * hundreds" of items was the audited gap). Deliberately not a general-purpose
 * data-grid: fixed page size, no sorting, no server round-trip per page.
 */
export function usePagedList<T>(items: T[], pageSize = 10) {
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const clampedPage = Math.min(page, pageCount - 1);

  const pageItems = useMemo(
    () => items.slice(clampedPage * pageSize, clampedPage * pageSize + pageSize),
    [items, clampedPage, pageSize],
  );

  return {
    pageItems,
    page: clampedPage,
    pageCount,
    hasPrev: clampedPage > 0,
    hasNext: clampedPage < pageCount - 1,
    goPrev: () => setPage((p) => Math.max(0, p - 1)),
    goNext: () => setPage((p) => p + 1),
  };
}
