import { describe, expect, it } from "vitest";

import { WorkspaceRegistry } from "./WorkspaceRegistry";
import type { WorkspaceApplication } from "./workspaceTypes";

function makeApp(overrides: Partial<WorkspaceApplication> = {}): WorkspaceApplication {
  return {
    id: "test-app",
    name: "Test App",
    description: "A test application.",
    icon: () => null,
    route: "/test-app",
    component: () => null,
    permissions: [],
    ...overrides,
  };
}

describe("WorkspaceRegistry", () => {
  it("registers and retrieves an application", () => {
    const registry = new WorkspaceRegistry();
    const app = makeApp();

    registry.register(app);

    expect(registry.get("test-app")).toBe(app);
    expect(registry.has("test-app")).toBe(true);
    expect(registry.list()).toEqual([app]);
  });

  it("throws when registering a duplicate ID", () => {
    const registry = new WorkspaceRegistry();
    registry.register(makeApp());

    expect(() => registry.register(makeApp({ name: "Different name" }))).toThrow(
      'Workspace application "test-app" is already registered.',
    );
    // The original registration must survive a failed duplicate attempt.
    expect(registry.get("test-app")?.name).toBe("Test App");
  });

  it("unregisters an application", () => {
    const registry = new WorkspaceRegistry();
    registry.register(makeApp());

    registry.unregister("test-app");

    expect(registry.has("test-app")).toBe(false);
    expect(registry.get("test-app")).toBeUndefined();
    expect(registry.list()).toEqual([]);
  });

  it("unregistering an unknown ID is a no-op, not an error", () => {
    const registry = new WorkspaceRegistry();
    expect(() => registry.unregister("does-not-exist")).not.toThrow();
  });

  it("lists every registered application", () => {
    const registry = new WorkspaceRegistry();
    registry.register(makeApp({ id: "app-a", name: "App A" }));
    registry.register(makeApp({ id: "app-b", name: "App B" }));

    expect(registry.list().map((app) => app.id).sort()).toEqual(["app-a", "app-b"]);
  });

  it("is isolated per instance — no shared global state", () => {
    const first = new WorkspaceRegistry();
    const second = new WorkspaceRegistry();

    first.register(makeApp());

    expect(first.has("test-app")).toBe(true);
    expect(second.has("test-app")).toBe(false);
  });
});
