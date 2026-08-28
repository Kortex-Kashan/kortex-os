import type { ReactNode } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
} from "@kortex/design-system";
import { MarketplaceAccessDeniedError } from "../api";
import { useMarketplace } from "../hooks/useMarketplace";
import type { MarketplaceListing } from "../types";

/** The Marketplace workspace: read-only catalog discovery, and nothing
 * else — no install, no purchase, no publish, no lifecycle management.
 * See `MarketplaceAccessDeniedError`'s own docstring (in `../api.ts`) for
 * why a `PERMISSION_DENIED` failure renders one unified "access denied"
 * state here rather than branching on session-expired vs. forbidden. */
export function MarketplaceApp() {
  const { data, isPending, isError, error, refetch, isFetching } = useMarketplace();

  if (isPending) {
    return <LoadingState />;
  }

  if (isError) {
    if (error instanceof MarketplaceAccessDeniedError) {
      return <AccessDeniedState message={error.message} />;
    }
    return <ErrorState message={error.message} onRetry={() => void refetch()} />;
  }

  const listings = data ?? [];

  if (listings.length === 0) {
    return <EmptyState onRefresh={() => void refetch()} isRefreshing={isFetching} />;
  }

  return <PopulatedState listings={listings} onRefresh={() => void refetch()} isRefreshing={isFetching} />;
}

function WorkspaceCard({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

// Shared description across every state — set once here so the "browse
// only" boundary can't drift out of sync between states.
const MARKETPLACE_DESCRIPTION = "Marketplace catalog — browse only. Installing, purchasing, and publishing are not available yet.";

function LoadingState() {
  return (
    <WorkspaceCard title="Marketplace" description={MARKETPLACE_DESCRIPTION}>
      <div className="space-y-3" role="status" aria-label="Loading marketplace catalog">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    </WorkspaceCard>
  );
}

function EmptyState({ onRefresh, isRefreshing }: { onRefresh: () => void; isRefreshing: boolean }) {
  return (
    <WorkspaceCard
      title="Marketplace"
      description={MARKETPLACE_DESCRIPTION}
      action={
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
          Refresh
        </Button>
      }
    >
      <p className="text-body text-muted-foreground">No items are currently available in the catalog.</p>
    </WorkspaceCard>
  );
}

function AccessDeniedState({ message }: { message: string }) {
  return (
    <WorkspaceCard title="Marketplace" description={MARKETPLACE_DESCRIPTION}>
      <div className="space-y-2">
        <Badge variant="destructive">Access denied</Badge>
        <p className="text-body text-muted-foreground">
          You do not have permission to view the marketplace catalog.
        </p>
        <p className="text-caption text-muted-foreground">{message}</p>
      </div>
    </WorkspaceCard>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <WorkspaceCard title="Marketplace" description={MARKETPLACE_DESCRIPTION}>
      <div className="space-y-3">
        <p className="text-body text-muted-foreground">
          Something went wrong loading the marketplace catalog.
        </p>
        <p className="text-caption text-muted-foreground">{message}</p>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </div>
    </WorkspaceCard>
  );
}

function PopulatedState({
  listings,
  onRefresh,
  isRefreshing,
}: {
  listings: MarketplaceListing[];
  onRefresh: () => void;
  isRefreshing: boolean;
}) {
  return (
    <WorkspaceCard
      title="Marketplace"
      description={`${MARKETPLACE_DESCRIPTION} ${listings.length} available.`}
      action={
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
          Refresh
        </Button>
      }
    >
      <ul className="space-y-3">
        {listings.map((listing) => (
          <li
            key={listing.listingId}
            className="rounded-md border border-border p-4"
            data-testid="marketplace-listing-card"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-body font-medium text-foreground">{listing.name}</span>
              <div className="flex items-center gap-2">
                {listing.status === "DEPRECATED" && <Badge variant="secondary">Deprecated</Badge>}
                <Badge variant="secondary">v{listing.version}</Badge>
              </div>
            </div>
            <p className="text-caption text-muted-foreground">
              {listing.publisher} · {listing.itemType}
              {listing.compatibility ? ` · ${listing.compatibility}` : ""}
            </p>
            <p className="mt-2 text-body text-muted-foreground">{listing.description}</p>
          </li>
        ))}
      </ul>
    </WorkspaceCard>
  );
}
