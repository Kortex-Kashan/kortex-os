/**
 * Connections tab (M7.3) — lets a tenant admin create, list, and delete the
 * tenant's own connector profiles and their credentials. Everything is
 * tenant-scoped server-side (`ConnectorEngine.list_profiles`/`.register_
 * profile`/`.delete_profile` bind the caller's own principal.tenant_id,
 * never a client-supplied value) — this component never sends or reads a
 * tenant id itself.
 *
 * Credential handling: the credential field is write-only. A submitted
 * value is sent once via `kortex.security.secret.put` and never read back —
 * `ConnectorProfile` (what `listConnectorProfiles` returns) carries no
 * secret-value field to begin with (see `types.ts`), so there is nothing
 * for this component to accidentally render even if it wanted to.
 */

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
} from "@kortex/design-system";
import { ConnectorAccessDeniedError, ConnectorRequestError } from "../api";
import { useConnectors } from "../hooks/useConnectors";
import {
  useConnectorProfiles,
  useDeleteConnectorProfile,
  useRegisterConnectorProfile,
} from "../hooks/useConnectorProfiles";
import type { ConnectorProfile, CreateConnectionPayload } from "../types";

export function ConnectionsTab() {
  const { data, isPending, isError, error, refetch, isFetching } = useConnectorProfiles();
  const [createOpen, setCreateOpen] = useState(false);

  if (isPending) {
    return <LoadingState />;
  }

  if (isError) {
    if (error instanceof ConnectorAccessDeniedError) {
      return <AccessDeniedState message={error.message} />;
    }
    return <ErrorState message={error.message} onRetry={() => void refetch()} />;
  }

  const profiles = data ?? [];

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Connections</CardTitle>
          <CardDescription>
            Connect an external service so AI Studio and business workflows can use it.
          </CardDescription>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
            Refresh
          </Button>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            New Connection
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {profiles.length === 0 ? (
          <p className="text-body text-muted-foreground" role="status">
            No connections configured.
          </p>
        ) : (
          <ul className="space-y-3" role="list" aria-label="Connections">
            {profiles.map((profile) => (
              <ConnectionCard key={profile.profileId} profile={profile} />
            ))}
          </ul>
        )}
      </CardContent>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New Connection</DialogTitle>
            <DialogDescription>
              Register a connection using one of the drivers already installed on this system.
            </DialogDescription>
          </DialogHeader>
          <CreateConnectionForm onSuccess={() => setCreateOpen(false)} onCancel={() => setCreateOpen(false)} />
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function LoadingState() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Connections</CardTitle>
        <CardDescription>
          Connect an external service so AI Studio and business workflows can use it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-3" role="status" aria-label="Loading connections">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      </CardContent>
    </Card>
  );
}

function AccessDeniedState({ message }: { message: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Connections</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <Badge variant="destructive">Access denied</Badge>
        <p className="text-body text-muted-foreground">You do not have permission to view connections.</p>
        <p className="text-caption text-muted-foreground">{message}</p>
      </CardContent>
    </Card>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Connections</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-body text-muted-foreground">Something went wrong loading your connections.</p>
        <p className="text-caption text-muted-foreground">{message}</p>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </CardContent>
    </Card>
  );
}

function ConnectionCard({ profile }: { profile: ConnectorProfile }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const deleteProfile = useDeleteConnectorProfile();

  return (
    <>
      <li className="rounded-md border border-border p-4" data-testid="connection-card">
        <div className="flex items-center justify-between gap-2">
          <span className="text-body font-medium text-foreground">{profile.name}</span>
          <Badge variant={profile.isActive ? "secondary" : "outline"}>
            {profile.isActive ? "Active" : "Inactive"}
          </Badge>
        </div>
        <p className="text-caption text-muted-foreground">
          {profile.driverId} · {profile.profileId}
        </p>
        <div className="mt-3">
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setConfirmOpen(true)}
            aria-label={`Delete connection ${profile.name}`}
          >
            Delete
          </Button>
        </div>
        {deleteProfile.isError && (
          <p className="mt-2 text-caption text-destructive" role="alert">
            {deleteProfile.error instanceof Error ? deleteProfile.error.message : "Failed to delete connection."}
          </p>
        )}
      </li>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete connection "{profile.name}"?</DialogTitle>
            <DialogDescription>
              Anything relying on this connection — including AI Studio agents — will no longer be able to
              reach it. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmOpen(false)}
              disabled={deleteProfile.isPending}
            >
              Keep Connection
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={deleteProfile.isPending}
              onClick={() => {
                void deleteProfile.mutateAsync(profile.profileId).then(() => setConfirmOpen(false));
              }}
            >
              {deleteProfile.isPending ? "Deleting…" : "Confirm Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function CreateConnectionForm({ onSuccess, onCancel }: { onSuccess: () => void; onCancel: () => void }) {
  const { data: drivers } = useConnectors();
  const register = useRegisterConnectorProfile();
  const [profileId, setProfileId] = useState("");
  const [name, setName] = useState("");
  const [driverId, setDriverId] = useState("");
  const [credential, setCredential] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  // Default to the only installed driver once the registry loads, so a
  // single-driver install (the common case today) never requires an extra
  // click through the picker.
  useEffect(() => {
    if (!driverId && drivers && drivers.length === 1) {
      setDriverId(drivers[0].driverId);
    }
  }, [driverId, drivers]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!profileId.trim() || !name.trim() || !driverId.trim()) {
      setFormError("Connection ID, name, and driver are required.");
      return;
    }
    setFormError(null);

    const payload: CreateConnectionPayload = {
      profileId: profileId.trim(),
      name: name.trim(),
      driverId: driverId.trim(),
      credential: credential.trim() || undefined,
    };

    try {
      await register.mutateAsync(payload);
      onSuccess();
    } catch (err) {
      setFormError(
        err instanceof ConnectorAccessDeniedError || err instanceof ConnectorRequestError
          ? err.message
          : "Failed to create the connection.",
      );
    }
  }

  const driverOptions = drivers ?? [];

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="connection-profile-id">Connection ID</Label>
        <Input
          id="connection-profile-id"
          value={profileId}
          onChange={(e) => setProfileId(e.target.value)}
          placeholder="e.g. billing-api"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="connection-name">Name</Label>
        <Input
          id="connection-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Billing API"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="connection-driver">Driver</Label>
        {driverOptions.length === 0 ? (
          <p className="text-caption text-muted-foreground">
            No drivers are installed yet — check the Drivers tab.
          </p>
        ) : (
          <Select value={driverId} onValueChange={setDriverId}>
            <SelectTrigger id="connection-driver" disabled={register.isPending}>
              <SelectValue placeholder="Select a driver…" />
            </SelectTrigger>
            <SelectContent>
              {driverOptions.map((driver) => (
                <SelectItem key={driver.driverId} value={driver.driverId}>
                  {driver.displayName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="connection-credential">Credential (optional)</Label>
        <Input
          id="connection-credential"
          type="password"
          autoComplete="off"
          value={credential}
          onChange={(e) => setCredential(e.target.value)}
          placeholder="API key or token"
        />
        <p className="text-caption text-muted-foreground">
          Stored encrypted. It will never be shown again after you save.
        </p>
      </div>
      {formError && (
        <p className="text-caption text-destructive" role="alert">
          {formError}
        </p>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={register.isPending}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={register.isPending}>
          {register.isPending ? "Creating…" : "Create Connection"}
        </Button>
      </DialogFooter>
    </form>
  );
}
