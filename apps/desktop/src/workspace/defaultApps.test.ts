import { describe, expect, it } from "vitest";

import { ConnectorsApp } from "@/features/connectors/components/ConnectorsApp";
import { WorkflowApp } from "@/features/workflow/components/WorkflowApp";
import { DEFAULT_APPLICATIONS } from "./defaultApps";
import { WorkspaceRegistry } from "./WorkspaceRegistry";

describe("DEFAULT_APPLICATIONS", () => {
  it("registers exactly the five required placeholder applications", () => {
    expect(DEFAULT_APPLICATIONS.map((app) => app.name)).toEqual([
      "Dashboard",
      "AI Studio",
      "Workflow Engine",
      "Connector Engine",
      "Marketplace",
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

  it("registers cleanly into a WorkspaceRegistry with no duplicate-ID conflicts", () => {
    const registry = new WorkspaceRegistry();
    for (const app of DEFAULT_APPLICATIONS) {
      expect(() => registry.register(app)).not.toThrow();
    }
    expect(registry.list()).toHaveLength(5);
  });
});
