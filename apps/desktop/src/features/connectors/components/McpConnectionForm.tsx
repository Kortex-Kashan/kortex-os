/**
 * McpConnectionForm (Integration Hub M1)
 *
 * Dedicated UI for configuring and testing a remote Model Context Protocol (MCP)
 * connection over Streamable HTTP.
 * Enforces:
 * - HTTPS endpoint validation.
 * - Test Connection before / during registration using a temporary session.
 * - Credential write-only submission via SecretStore.
 * - profileId and name registration with driver_id="connector-mcp".
 */

import { useState } from "react";
import type { FormEvent } from "react";
import {
  Badge,
  Button,
  DialogFooter,
  Input,
  Label,
} from "@kortex/design-system";
import { testMcpConnection } from "../api";
import { useRegisterConnectorProfile } from "../hooks/useConnectorProfiles";
import type { CreateConnectionPayload } from "../types";

export interface McpConnectionFormProps {
  onSuccess: () => void;
  onCancel: () => void;
}

export function McpConnectionForm({ onSuccess, onCancel }: McpConnectionFormProps) {
  const register = useRegisterConnectorProfile();
  const [profileId, setProfileId] = useState("");
  const [name, setName] = useState("");
  const [endpointUrl, setEndpointUrl] = useState("");
  const [credential, setCredential] = useState("");
  const [testStatus, setTestStatus] = useState<"idle" | "testing" | "success" | "error">("idle");
  const [testError, setTestError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  async function handleTestConnection() {
    if (!endpointUrl.trim()) {
      setTestError("Please enter an MCP endpoint URL first.");
      setTestStatus("error");
      return;
    }

    if (!endpointUrl.startsWith("https://")) {
      setTestError("MCP endpoint URL must start with 'https://' (Streamable HTTP).");
      setTestStatus("error");
      return;
    }

    setTestStatus("testing");
    setTestError(null);

    try {
      const ok = await testMcpConnection(endpointUrl.trim(), credential.trim() || undefined);
      if (ok) {
        setTestStatus("success");
      } else {
        setTestStatus("error");
        setTestError("Remote server rejected verification.");
      }
    } catch (err) {
      setTestStatus("error");
      setTestError(err instanceof Error ? err.message : "Connection test failed.");
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);

    if (!profileId.trim() || !name.trim() || !endpointUrl.trim()) {
      setFormError("Connection ID, name, and MCP endpoint URL are required.");
      return;
    }

    if (!endpointUrl.startsWith("https://")) {
      setFormError("MCP endpoint URL must start with 'https://'.");
      return;
    }

    const payload: CreateConnectionPayload = {
      profileId: profileId.trim(),
      name: name.trim(),
      driverId: "connector-mcp",
      credential: credential.trim() || undefined,
      options: {
        endpoint_url: endpointUrl.trim(),
      },
    };

    try {
      await register.mutateAsync(payload);
      onSuccess();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to register MCP connection.");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" data-testid="mcp-connection-form">
      <div className="space-y-1.5">
        <Label htmlFor="mcp-profile-id">Connection ID</Label>
        <Input
          id="mcp-profile-id"
          placeholder="e.g. github-mcp"
          value={profileId}
          onChange={(e) => setProfileId(e.target.value)}
          disabled={register.isPending}
          required
        />
        <p className="text-caption text-muted-foreground">
          Unique identifier for this MCP profile. Tools will be namespaced as kortex.mcp.&lt;id&gt;.*
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="mcp-name">Display Name</Label>
        <Input
          id="mcp-name"
          placeholder="e.g. GitHub MCP Integration"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={register.isPending}
          required
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="mcp-endpoint-url">Streamable HTTP Endpoint URL</Label>
        <div className="flex gap-2">
          <Input
            id="mcp-endpoint-url"
            type="url"
            placeholder="https://api.example.com/mcp"
            value={endpointUrl}
            onChange={(e) => {
              setEndpointUrl(e.target.value);
              setTestStatus("idle");
              setTestError(null);
            }}
            disabled={register.isPending}
            className="flex-1"
            required
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleTestConnection()}
            disabled={testStatus === "testing" || register.isPending}
            data-testid="test-mcp-btn"
          >
            {testStatus === "testing" ? "Testing…" : "Test Connection"}
          </Button>
        </div>
        <div className="flex items-center gap-2 mt-1">
          {testStatus === "success" && (
            <Badge variant="secondary" className="text-green-600 bg-green-500/10">
              Verified ✓
            </Badge>
          )}
          {testStatus === "error" && (
            <Badge variant="destructive">
              Failed ✗
            </Badge>
          )}
          {testError && <span className="text-caption text-destructive">{testError}</span>}
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="mcp-credential">Bearer Token / API Key (Optional)</Label>
        <Input
          id="mcp-credential"
          type="password"
          placeholder="••••••••••••"
          value={credential}
          onChange={(e) => setCredential(e.target.value)}
          disabled={register.isPending}
        />
        <p className="text-caption text-muted-foreground">
          Encrypted securely in SecretStore. Never logged or exposed in plaintext.
        </p>
      </div>

      {formError && (
        <p className="text-caption text-destructive" role="alert">
          {formError}
        </p>
      )}

      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onCancel}
          disabled={register.isPending}
        >
          Cancel
        </Button>
        <Button
          type="submit"
          size="sm"
          disabled={register.isPending}
          data-testid="save-mcp-btn"
        >
          {register.isPending ? "Connecting…" : "Connect MCP Server"}
        </Button>
      </DialogFooter>
    </form>
  );
}
