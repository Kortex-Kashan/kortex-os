import type { PanelDefinition } from "./panelTypes";

/**
 * A plain, instantiable registry — deliberately not a module-level
 * singleton, since ES module caching would make that a de facto global
 * shared by every importer. Callers (PanelProvider, tests) construct
 * their own instance, which is what keeps this testable in isolation.
 * Mirrors WorkspaceRegistry (apps/desktop/src/workspace/WorkspaceRegistry.ts).
 */
export class PanelRegistry {
  private readonly panels = new Map<string, PanelDefinition>();

  register(panel: PanelDefinition): void {
    if (this.panels.has(panel.id)) {
      throw new Error(`Panel "${panel.id}" is already registered.`);
    }
    this.panels.set(panel.id, panel);
  }

  unregister(id: string): void {
    this.panels.delete(id);
  }

  get(id: string): PanelDefinition | undefined {
    return this.panels.get(id);
  }

  has(id: string): boolean {
    return this.panels.has(id);
  }

  list(): PanelDefinition[] {
    return Array.from(this.panels.values());
  }
}
