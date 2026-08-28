import { useState } from "react";
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
import { DocumentAccessDeniedError } from "../documentsApi";
import { useDocumentAdapters } from "../hooks/useDocumentAdapters";
import { useDocumentTemplates } from "../hooks/useDocumentTemplates";
import { useKnowledgeNodes } from "../hooks/useKnowledgeNodes";
import { useKnowledgeTraversal } from "../hooks/useKnowledgeTraversal";
import { KnowledgeAccessDeniedError } from "../knowledgeApi";
import type { DocumentAdapter, DocumentTemplate, KnowledgeNode } from "../types";

/** The Document & Knowledge workspace: read-only exploration of the
 * Document adapter/template registries and the Knowledge Graph's entity/
 * relationship data — and nothing else. No document editing, no template
 * authoring, no knowledge mutation. Each of the four registries below is
 * an independent capability with its own loading/populated/empty/
 * access-denied/error/retry state — a failure in one never hides another. */
export function DocumentKnowledgeApp() {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Document &amp; Knowledge</CardTitle>
        <CardDescription>
          Document and knowledge registry — browse only. Document editing, template authoring, and
          knowledge mutation are not available yet.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <DocumentAdaptersSection />
        <DocumentTemplatesSection />
        <KnowledgeEntitiesSection selectedNodeId={selectedNodeId} onSelectNode={setSelectedNodeId} />
        <KnowledgeRelationshipsSection selectedNodeId={selectedNodeId} />
      </CardContent>
    </Card>
  );
}

function SectionShell({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-4">
        <h3 className="text-heading leading-none tracking-tight">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function LoadingRows({ label }: { label: string }) {
  return (
    <div className="space-y-3" role="status" aria-label={label}>
      <Skeleton className="h-14 w-full" />
      <Skeleton className="h-14 w-full" />
    </div>
  );
}

function RefreshButton({ onRefresh, isRefreshing }: { onRefresh: () => void; isRefreshing: boolean }) {
  return (
    <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
      Refresh
    </Button>
  );
}

function FailureState({
  error,
  onRetry,
  isAccessDenied,
}: {
  error: Error;
  onRetry: () => void;
  isAccessDenied: boolean;
}) {
  if (isAccessDenied) {
    return (
      <div className="space-y-2">
        <Badge variant="destructive">Access denied</Badge>
        <p className="text-body text-muted-foreground">You do not have permission to view this registry.</p>
        <p className="text-caption text-muted-foreground">{error.message}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-body text-muted-foreground">Something went wrong loading this registry.</p>
      <p className="text-caption text-muted-foreground">{error.message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

function DocumentAdaptersSection() {
  const { data, isPending, isError, error, refetch, isFetching } = useDocumentAdapters();

  if (isPending) {
    return (
      <SectionShell title="Document Adapters">
        <LoadingRows label="Loading document adapters" />
      </SectionShell>
    );
  }

  if (isError) {
    return (
      <SectionShell title="Document Adapters">
        <FailureState
          error={error}
          onRetry={() => void refetch()}
          isAccessDenied={error instanceof DocumentAccessDeniedError}
        />
      </SectionShell>
    );
  }

  const adapters = data ?? [];

  return (
    <SectionShell
      title="Document Adapters"
      action={<RefreshButton onRefresh={() => void refetch()} isRefreshing={isFetching} />}
    >
      {adapters.length === 0 ? (
        <p className="text-body text-muted-foreground">No document adapters are currently registered.</p>
      ) : (
        <ul className="space-y-3">
          {adapters.map((adapter) => (
            <AdapterCard key={adapter.adapterId} adapter={adapter} />
          ))}
        </ul>
      )}
    </SectionShell>
  );
}

function DocumentTemplatesSection() {
  const { data, isPending, isError, error, refetch, isFetching } = useDocumentTemplates();

  if (isPending) {
    return (
      <SectionShell title="Document Templates">
        <LoadingRows label="Loading document templates" />
      </SectionShell>
    );
  }

  if (isError) {
    return (
      <SectionShell title="Document Templates">
        <FailureState
          error={error}
          onRetry={() => void refetch()}
          isAccessDenied={error instanceof DocumentAccessDeniedError}
        />
      </SectionShell>
    );
  }

  const templates = data ?? [];

  return (
    <SectionShell
      title="Document Templates"
      action={<RefreshButton onRefresh={() => void refetch()} isRefreshing={isFetching} />}
    >
      {templates.length === 0 ? (
        <p className="text-body text-muted-foreground">No document templates are currently registered.</p>
      ) : (
        <ul className="space-y-3">
          {templates.map((template) => (
            <TemplateCard key={template.templateId} template={template} />
          ))}
        </ul>
      )}
    </SectionShell>
  );
}

function KnowledgeEntitiesSection({
  selectedNodeId,
  onSelectNode,
}: {
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}) {
  const { data, isPending, isError, error, refetch, isFetching } = useKnowledgeNodes();

  if (isPending) {
    return (
      <SectionShell title="Knowledge Entities">
        <LoadingRows label="Loading knowledge entities" />
      </SectionShell>
    );
  }

  if (isError) {
    return (
      <SectionShell title="Knowledge Entities">
        <FailureState
          error={error}
          onRetry={() => void refetch()}
          isAccessDenied={error instanceof KnowledgeAccessDeniedError}
        />
      </SectionShell>
    );
  }

  const nodes = data ?? [];

  return (
    <SectionShell
      title="Knowledge Entities"
      action={<RefreshButton onRefresh={() => void refetch()} isRefreshing={isFetching} />}
    >
      {nodes.length === 0 ? (
        <p className="text-body text-muted-foreground">No knowledge entities are currently registered.</p>
      ) : (
        <ul className="space-y-3">
          {nodes.map((node) => (
            <NodeCard
              key={node.nodeId}
              node={node}
              isSelected={node.nodeId === selectedNodeId}
              onSelect={() => onSelectNode(node.nodeId)}
            />
          ))}
        </ul>
      )}
    </SectionShell>
  );
}

function KnowledgeRelationshipsSection({ selectedNodeId }: { selectedNodeId: string | null }) {
  const { data, isPending, isError, error, refetch, isFetching } = useKnowledgeTraversal(selectedNodeId);

  if (selectedNodeId === null) {
    return (
      <SectionShell title="Relationships">
        <p className="text-body text-muted-foreground">
          Select a knowledge entity above to explore its relationships.
        </p>
      </SectionShell>
    );
  }

  if (isPending) {
    return (
      <SectionShell title="Relationships">
        <LoadingRows label="Loading relationships" />
      </SectionShell>
    );
  }

  if (isError) {
    return (
      <SectionShell title="Relationships">
        <FailureState
          error={error}
          onRetry={() => void refetch()}
          isAccessDenied={error instanceof KnowledgeAccessDeniedError}
        />
      </SectionShell>
    );
  }

  const related = data ?? [];

  return (
    <SectionShell
      title="Relationships"
      action={<RefreshButton onRefresh={() => void refetch()} isRefreshing={isFetching} />}
    >
      {related.length === 0 ? (
        <p className="text-body text-muted-foreground">No related entities were found within range.</p>
      ) : (
        <ul className="space-y-3">
          {related.map((node) => (
            <NodeCard key={node.nodeId} node={node} isSelected={false} onSelect={() => {}} />
          ))}
        </ul>
      )}
    </SectionShell>
  );
}

function AdapterCard({ adapter }: { adapter: DocumentAdapter }) {
  return (
    <li className="rounded-md border border-border p-4" data-testid="document-adapter-card">
      <div className="flex items-center justify-between gap-2">
        <span className="text-body font-medium text-foreground">{adapter.displayName}</span>
        <Badge variant="secondary">v{adapter.version}</Badge>
      </div>
      <p className="text-caption text-muted-foreground">
        {adapter.vendor} · {adapter.adapterId}
      </p>
      <p className="mt-2 text-body text-muted-foreground">{adapter.description}</p>
    </li>
  );
}

function TemplateCard({ template }: { template: DocumentTemplate }) {
  return (
    <li className="rounded-md border border-border p-4" data-testid="document-template-card">
      <div className="flex items-center justify-between gap-2">
        <span className="text-body font-medium text-foreground">{template.name}</span>
        <Badge variant="secondary">v{template.version}</Badge>
      </div>
      <p className="text-caption text-muted-foreground">
        {template.namespace} · {template.templateId}
      </p>
      <p className="mt-2 text-body text-muted-foreground">{template.description}</p>
    </li>
  );
}

function NodeCard({
  node,
  isSelected,
  onSelect,
}: {
  node: KnowledgeNode;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const propertyEntries = Object.entries(node.properties);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={isSelected}
        className={`w-full rounded-md border p-4 text-left ${
          isSelected ? "border-primary" : "border-border"
        }`}
        data-testid="knowledge-node-card"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-body font-medium text-foreground">{node.label}</span>
          <Badge variant="secondary">{node.entityType}</Badge>
        </div>
        <p className="text-caption text-muted-foreground">{node.nodeId}</p>
        {propertyEntries.length > 0 && (
          <p className="mt-2 text-caption text-muted-foreground">
            {propertyEntries.map(([key, value]) => `${key}: ${String(value)}`).join(" · ")}
          </p>
        )}
      </button>
    </li>
  );
}
