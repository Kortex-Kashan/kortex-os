import { describe, expect, it } from "vitest";

import { AiStudioApp } from "@/features/ai-studio/components/AiStudioApp";
import { ConnectorsApp } from "@/features/connectors/components/ConnectorsApp";
import { DocumentKnowledgeApp } from "@/features/document-knowledge/components/DocumentKnowledgeApp";
import { MarketplaceApp } from "@/features/marketplace/components/MarketplaceApp";
import { WorkflowApp } from "@/features/workflow/components/WorkflowApp";
import { DEFAULT_APPLICATIONS } from "./defaultApps";
import { WorkspaceRegistry } from "./WorkspaceRegistry";

describe("DEFAULT_APPLICATIONS", () => {
  it("registers exactly the six required applications", () => {
    expect(DEFAULT_APPLICATIONS.map((app) => app.name)).toEqual([
      "Dashboard",
      "AI Studio",
      "Workflow Engine",
      "Connector Engine",
      "Marketplace",
      "Document & Knowledge",
    ]);
  });

  it("has unique, non-empty IDs and required fields for every entry", () => {
    const ids = DEFAULT_APPLICATIONS.map((app) => app.id);
    expect(new Set(ids).size).toBe(ids.length);

    for (const app of DEFAULT_APPLICATIONS) {
      expect(app.id).toBeTruthy();
      expect(app.name).toBeTruthy();
      expect(app.description).toBeTruthy();
      expect(app.route.startsWith("/")).toBe(true);
      expect(typeof app.icon).toBe("function");
      expect(typeof app.component).toBe("function");
      expect(app.permissions.length).toBeGreaterThan(0);
    }
  });

  it("wires the Connector Engine application to the real ConnectorsApp, not a placeholder", () => {
    const connectorApp = DEFAULT_APPLICATIONS.find((app) => app.id === "connector-engine");
    expect(connectorApp?.component).toBe(ConnectorsApp);
  });

  it("wires the Workflow Engine application to the real WorkflowApp, not a placeholder", () => {
    const workflowApp = DEFAULT_APPLICATIONS.find((app) => app.id === "workflow-engine");
    expect(workflowApp?.component).toBe(WorkflowApp);
  });

  it("wires the Marketplace application to the real MarketplaceApp, not a placeholder", () => {
    const marketplaceApp = DEFAULT_APPLICATIONS.find((app) => app.id === "marketplace");
    expect(marketplaceApp?.component).toBe(MarketplaceApp);
  });

  it("wires the AI Studio application to the real AiStudioApp, not a placeholder", () => {
    const aiStudioApp = DEFAULT_APPLICATIONS.find((app) => app.id === "ai-studio");
    expect(aiStudioApp?.component).toBe(AiStudioApp);
  });

  it("wires the Document & Knowledge application to the real DocumentKnowledgeApp, not a placeholder", () => {
    const documentKnowledgeApp = DEFAULT_APPLICATIONS.find((app) => app.id === "document-knowledge");
    expect(documentKnowledgeApp?.component).toBe(DocumentKnowledgeApp);
  });

  it("registers cleanly into a WorkspaceRegistry with no duplicate-ID conflicts", () => {
    const registry = new WorkspaceRegistry();
    for (const app of DEFAULT_APPLICATIONS) {
      expect(() => registry.register(app)).not.toThrow();
    }
    expect(registry.list()).toHaveLength(6);
  });
});
