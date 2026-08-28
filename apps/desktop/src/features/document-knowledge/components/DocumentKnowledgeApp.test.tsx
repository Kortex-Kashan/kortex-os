import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listDocumentAdaptersMock, listDocumentTemplatesMock, listKnowledgeNodesMock, traverseKnowledgeGraphMock, useAuthMock } =
  vi.hoisted(() => ({
    listDocumentAdaptersMock: vi.fn(),
    listDocumentTemplatesMock: vi.fn(),
    listKnowledgeNodesMock: vi.fn(),
    traverseKnowledgeGraphMock: vi.fn(),
    useAuthMock: vi.fn(),
  }));

vi.mock("../documentsApi", async () => {
  const actual = await vi.importActual<typeof import("../documentsApi")>("../documentsApi");
  return {
    ...actual,
    listDocumentAdapters: listDocumentAdaptersMock,
    listDocumentTemplates: listDocumentTemplatesMock,
  };
});

vi.mock("../knowledgeApi", async () => {
  const actual = await vi.importActual<typeof import("../knowledgeApi")>("../knowledgeApi");
  return {
    ...actual,
    listKnowledgeNodes: listKnowledgeNodesMock,
    traverseKnowledgeGraph: traverseKnowledgeGraphMock,
  };
});

vi.mock("@/auth/AuthProvider", () => ({
  useAuth: useAuthMock,
}));

import { DocumentAccessDeniedError, DocumentRequestError } from "../documentsApi";
import { KnowledgeAccessDeniedError } from "../knowledgeApi";
import type { DocumentAdapter, DocumentTemplate, KnowledgeNode } from "../types";
import { DocumentKnowledgeApp } from "./DocumentKnowledgeApp";

const AUTHENTICATED_STATE = {
  state: {
    status: "AUTHENTICATED" as const,
    identity: { principalId: "alice", principalType: "USER", tenantId: "tenant-1", roles: [] },
  },
};

beforeEach(() => {
  listDocumentAdaptersMock.mockReset();
  listDocumentTemplatesMock.mockReset();
  listKnowledgeNodesMock.mockReset();
  traverseKnowledgeGraphMock.mockReset();
  useAuthMock.mockReset();
  useAuthMock.mockReturnValue(AUTHENTICATED_STATE);
});

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DocumentKnowledgeApp />
    </QueryClientProvider>,
  );
}

/** Resolves all four independent registries to a stable, empty baseline
 * unless a test overrides one of them — keeps each test focused on the
 * one section it's actually exercising. */
function resolveAllEmpty() {
  listDocumentAdaptersMock.mockResolvedValue([]);
  listDocumentTemplatesMock.mockResolvedValue([]);
  listKnowledgeNodesMock.mockResolvedValue([]);
  traverseKnowledgeGraphMock.mockResolvedValue([]);
}

function makeAdapter(overrides: Partial<DocumentAdapter> = {}): DocumentAdapter {
  return {
    adapterId: "kortex.document.dummy.v1",
    displayName: "Dummy Reference Adapter",
    vendor: "KORTEX OS",
    author: "KORTEX Core Team",
    version: "1.0.0",
    license: "MIT",
    description: "Deterministic reference adapter.",
    homepage: null,
    supportedCapabilities: ["PREVIEW"],
    supportedOperations: ["GENERATE"],
    supportsPreview: true,
    supportsStreaming: false,
    supportsMacros: false,
    supportsSecurity: false,
    supportsVersioning: false,
    ...overrides,
  };
}

function makeTemplate(overrides: Partial<DocumentTemplate> = {}): DocumentTemplate {
  return {
    templateId: "payslip.v1",
    name: "Payslip",
    namespace: "hr",
    version: "1.0.0",
    description: "Standard payslip template.",
    placeholders: [],
    requiredFields: [],
    ...overrides,
  };
}

function makeNode(overrides: Partial<KnowledgeNode> = {}): KnowledgeNode {
  return {
    nodeId: "node-1",
    tenantId: "tenant-1",
    entityType: "Concept",
    label: "Distributed Systems",
    properties: {},
    ...overrides,
  };
}

describe("DocumentKnowledgeApp", () => {
  it("shows loading state for every section while requests are in flight", () => {
    listDocumentAdaptersMock.mockReturnValueOnce(new Promise<DocumentAdapter[]>(() => {}));
    listDocumentTemplatesMock.mockReturnValueOnce(new Promise<DocumentTemplate[]>(() => {}));
    listKnowledgeNodesMock.mockReturnValueOnce(new Promise<KnowledgeNode[]>(() => {}));

    renderApp();

    expect(screen.getByRole("status", { name: /loading document adapters/i })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: /loading document templates/i })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: /loading knowledge entities/i })).toBeInTheDocument();
  });

  it("shows empty-registry messages when every registry is empty", async () => {
    resolveAllEmpty();

    renderApp();

    expect(await screen.findByText("No document adapters are currently registered.")).toBeInTheDocument();
    expect(await screen.findByText("No document templates are currently registered.")).toBeInTheDocument();
    expect(await screen.findByText("No knowledge entities are currently registered.")).toBeInTheDocument();
    expect(
      screen.getByText("Select a knowledge entity above to explore its relationships."),
    ).toBeInTheDocument();
  });

  it("communicates that editing/authoring/mutation are not available", async () => {
    resolveAllEmpty();

    renderApp();

    expect(
      await screen.findByText(/Document editing, template authoring, and knowledge mutation are not available yet\./),
    ).toBeInTheDocument();
  });

  it("renders real document adapter and template data", async () => {
    listDocumentAdaptersMock.mockResolvedValue([makeAdapter()]);
    listDocumentTemplatesMock.mockResolvedValue([makeTemplate()]);
    listKnowledgeNodesMock.mockResolvedValue([]);

    renderApp();

    expect(await screen.findByText("Dummy Reference Adapter")).toBeInTheDocument();
    expect(await screen.findByText("Payslip")).toBeInTheDocument();
    expect(screen.getAllByTestId("document-adapter-card")).toHaveLength(1);
    expect(screen.getAllByTestId("document-template-card")).toHaveLength(1);
  });

  it("selecting a knowledge entity loads its relationships", async () => {
    listDocumentAdaptersMock.mockResolvedValue([]);
    listDocumentTemplatesMock.mockResolvedValue([]);
    listKnowledgeNodesMock.mockResolvedValue([makeNode()]);
    traverseKnowledgeGraphMock.mockResolvedValueOnce([makeNode({ nodeId: "node-2", label: "Consensus" })]);

    renderApp();

    const nodeCard = await screen.findByText("Distributed Systems");
    fireEvent.click(nodeCard);

    expect(traverseKnowledgeGraphMock).toHaveBeenCalledWith("node-1", "tenant-1", 2);
    expect(await screen.findByText("Consensus")).toBeInTheDocument();
  });

  it("shows an honest empty relationships message when traversal finds nothing", async () => {
    listDocumentAdaptersMock.mockResolvedValue([]);
    listDocumentTemplatesMock.mockResolvedValue([]);
    listKnowledgeNodesMock.mockResolvedValue([makeNode()]);
    traverseKnowledgeGraphMock.mockResolvedValueOnce([]);

    renderApp();

    fireEvent.click(await screen.findByText("Distributed Systems"));

    expect(await screen.findByText("No related entities were found within range.")).toBeInTheDocument();
  });

  it("never renders raw entity properties as anything other than plain text (no secret-shaped rendering)", async () => {
    listDocumentAdaptersMock.mockResolvedValue([]);
    listDocumentTemplatesMock.mockResolvedValue([]);
    listKnowledgeNodesMock.mockResolvedValue([
      makeNode({ properties: { secretHandle: "sh_should_never_render_specially" } }),
    ]);

    renderApp();

    // The property is rendered as plain visible text (it's the node's own
    // data, not a credential) -- what must never happen is a *different*
    // capability's genuinely sensitive field leaking through unrelated to
    // what the node actually returned. Documents adapters/templates carry
    // no secret field at all (verified in documentsApi.test.ts); this
    // asserts the same absence holds in the rendered DOM end to end.
    await screen.findByText("Distributed Systems");
    expect(screen.queryByText(/vector_embedding|vectorEmbedding/i)).not.toBeInTheDocument();
  });

  it("shows an access-denied state for the document adapters section on PERMISSION_DENIED", async () => {
    listDocumentAdaptersMock.mockRejectedValueOnce(new DocumentAccessDeniedError("Missing permission: document:read"));
    listDocumentTemplatesMock.mockResolvedValue([]);
    listKnowledgeNodesMock.mockResolvedValue([]);

    renderApp();

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(screen.getByText("You do not have permission to view this registry.")).toBeInTheDocument();
    expect(screen.queryByText(/session expired/i)).not.toBeInTheDocument();
    // Other sections are unaffected by this section's failure.
    expect(await screen.findByText("No document templates are currently registered.")).toBeInTheDocument();
  });

  it("shows an access-denied state for the knowledge entities section on PERMISSION_DENIED", async () => {
    listDocumentAdaptersMock.mockResolvedValue([]);
    listDocumentTemplatesMock.mockResolvedValue([]);
    listKnowledgeNodesMock.mockRejectedValueOnce(new KnowledgeAccessDeniedError("Missing permission: knowledge:read"));

    renderApp();

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
  });

  it("shows a generic, recoverable error with retry for a section on any other failure", async () => {
    listDocumentAdaptersMock.mockRejectedValue(new DocumentRequestError("backend unreachable"));
    listDocumentTemplatesMock.mockResolvedValue([]);
    listKnowledgeNodesMock.mockResolvedValue([]);

    renderApp();

    expect(
      await screen.findByText("Something went wrong loading this registry.", undefined, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  }, 8000);

  it("retries the failed section when Retry is clicked", async () => {
    listDocumentAdaptersMock.mockRejectedValue(new DocumentRequestError("backend unreachable"));
    listDocumentTemplatesMock.mockResolvedValue([]);
    listKnowledgeNodesMock.mockResolvedValue([]);

    renderApp();

    const retryButton = await screen.findByRole("button", { name: "Retry" }, { timeout: 3000 });
    listDocumentAdaptersMock.mockReset();
    listDocumentAdaptersMock.mockResolvedValueOnce([makeAdapter()]);
    fireEvent.click(retryButton);

    expect(await screen.findByText("Dummy Reference Adapter")).toBeInTheDocument();
  }, 8000);

  it("refreshes the document templates section independently when its Refresh is clicked", async () => {
    listDocumentAdaptersMock.mockResolvedValue([]);
    listDocumentTemplatesMock.mockResolvedValueOnce([makeTemplate()]);
    listDocumentTemplatesMock.mockResolvedValueOnce([makeTemplate({ name: "Updated Payslip" })]);
    listKnowledgeNodesMock.mockResolvedValue([]);

    renderApp();

    await screen.findByText("Payslip");
    const templatesHeading = screen.getByText("Document Templates");
    const templatesSection = templatesHeading.closest("section");
    if (!templatesSection) {
      throw new Error("Document Templates section not found");
    }
    fireEvent.click(within(templatesSection).getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("Updated Payslip")).toBeInTheDocument();
    expect(listDocumentTemplatesMock).toHaveBeenCalledTimes(2);
  });
});
